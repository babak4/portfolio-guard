"""The live signal series: daily GBP closes of the primary ETF, checked against a second ETF.

The primary (a GBP-listed ETF) drives the signals. The secondary (a USD-listed ETF) exists only
to catch a bad primary: its returns, converted to pounds with the daily USD per GBP rate, must
match the primary's over the last five trading days. Levels are not compared because the two
funds differ. Signals are scale-free, so the primary's pence or pound unit does not matter.

Any failure raises ``DataIntegrityError``. Nothing is guessed, and nothing is stored unless every
check passed.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from guard.config import GuardConfig
from guard.config.models import Signals as SignalsConfig
from guard.market.base import (
    CrossCheckError,
    DataIntegrityError,
    Listing,
    MacroProvider,
    Observation,
    PriceProvider,
)
from guard.market.crosscheck import CheckedSeries
from guard.market.fetch import (
    DEFAULT_MACRO_ALIASES,
    SourceSeries,
    fetch_macro,
    fetch_prices,
)
from guard.market.sanity import check_jumps, check_staleness
from guard.market.store import MarketStore
from guard.run.redact import redact_amounts

WEEK_ROWS = 5
FX_SERIES = "USD_PER_GBP"
FX_OVERLAP_DAYS = 15
TRADING_DAYS_PER_MONTH = 21
DAYS_PER_WEEK = 5

ReturnsCheck = Literal["strict", "advisory"]
History = Literal["weekly", "daily"]


@dataclass(frozen=True)
class LiveLevels:
    """``levels`` are the primary's closes (floats) on ``days``; ``weekly_return`` is the move
    over the last five trading days, or ``None`` with fewer than six closes."""

    days: tuple[date, ...]
    levels: tuple[float, ...]
    sources: tuple[str, ...]
    weekly_return: float | None
    last_close_date: date
    # Advisory return check only: the second ETF did not confirm the primary's move. The series
    # is then neither stored nor to be trusted beyond reporting what it shows.
    unconfirmed: bool = False
    unconfirmed_note: str | None = None


def required_closes(signals: SignalsConfig, history: History = "weekly") -> int:
    """The fewest closes the configured windows can be computed from.

    The weekly run needs every signal; the daily check only the volatility window and the
    week's return (six closes)."""
    vol = signals.volatility_window_days + 1
    week = WEEK_ROWS + 1
    if history == "daily":
        return max(vol, week)
    trend = (
        signals.trend_window
        if signals.trend_method == "sma_days"
        else signals.trend_window * TRADING_DAYS_PER_MONTH
    )
    return max(trend, signals.drawdown_lookback_weeks * DAYS_PER_WEEK, vol, week)


def _before(observations: Sequence[Observation], today: date) -> list[Observation]:
    """Completed sessions only: anything dated on or after today may still be in progress."""
    return [o for o in observations if o.day < today]


def _fetch_one(
    listing: Listing,
    providers: Sequence[PriceProvider],
    start: date,
    end: date,
    today: date,
) -> SourceSeries:
    result = fetch_prices(listing, providers, start, end, max_sources=1)
    if not result.sources:
        raise DataIntegrityError(
            "no_data", listing.listing_id, (), f"no provider returned data; errors: {result.errors}"
        )
    source = result.sources[0]
    kept = _before(source.observations, today)
    if not kept:
        raise DataIntegrityError(
            "no_data", listing.listing_id, (source.source_id,), "no completed closes"
        )
    return SourceSeries(source.source_id, tuple(kept))


def _fx(
    providers: Sequence[MacroProvider], start: date, end: date, today: date, cfg: GuardConfig
) -> CheckedSeries:
    """USD per GBP from the first source (Frankfurter, the ECB's daily rate), checked against the
    second (FRED) on the dates both have. FRED publishes with a lag of days, so the two are not
    expected to end on the same day; they must share at least one date in the last 15 days and
    agree on every shared date."""
    result = fetch_macro(
        FX_SERIES, providers, start, end, aliases=DEFAULT_MACRO_ALIASES, max_sources=2
    )
    sources = [
        SourceSeries(s.source_id, tuple(_before(s.observations, today))) for s in result.sources
    ]
    sources = [s for s in sources if s.observations]
    if len(sources) < 2:
        raise DataIntegrityError(
            "fx_sources",
            FX_SERIES,
            tuple(s.source_id for s in sources),
            f"two independent rate sources are required; errors: {result.errors}",
        )
    series, other = sources[0], sources[1]
    names = (series.source_id, other.source_id)
    other_by_day = {o.day: o.value for o in other.observations}
    since = today - timedelta(days=FX_OVERLAP_DAYS)
    shared = [o for o in series.observations if o.day >= since and o.day in other_by_day]
    if not shared:
        raise CrossCheckError(
            "fx_overlap",
            FX_SERIES,
            names,
            f"no date in the last {FX_OVERLAP_DAYS} days has a rate from both sources",
        )
    tolerance = Decimal(str(cfg.data.crosscheck_tolerance))
    for o in shared:
        reference = other_by_day[o.day]
        if reference <= 0 or abs(o.value - reference) / reference > tolerance:
            raise CrossCheckError(
                "fx_disagree",
                FX_SERIES,
                names,
                f"rates differ on {o.day} by more than {cfg.data.crosscheck_tolerance:.2%}",
            )
    return CheckedSeries(FX_SERIES, series.observations, names, Decimal(0))


def _on_or_before(days: Sequence[date], values: Sequence[Decimal], day: date) -> Decimal | None:
    i = bisect_right(days, day)
    return values[i - 1] if i else None


def _convert(secondary: Sequence[Observation], fx: Sequence[Observation]) -> list[Observation]:
    """USD closes in pounds, using the last rate on or before each date."""
    fx_days = [o.day for o in fx]
    fx_values = [o.value for o in fx]
    out: list[Observation] = []
    for o in secondary:
        rate = _on_or_before(fx_days, fx_values, o.day)
        if rate is None or rate <= 0:
            continue
        out.append(Observation(o.day, o.value / rate))
    return out


def _check_returns(
    primary: SourceSeries,
    secondary: Sequence[Observation],
    names: tuple[str, str],
    series_id: str,
    tolerance: float,
) -> None:
    obs = primary.observations
    if len(obs) <= WEEK_ROWS:
        raise DataIntegrityError(
            "history", series_id, names[:1], f"only {len(obs)} closes; need {WEEK_ROWS + 1}"
        )
    days = [o.day for o in secondary]
    values = [o.value for o in secondary]
    last, first = obs[-1], obs[-1 - WEEK_ROWS]
    end = _on_or_before(days, values, last.day)
    begin = _on_or_before(days, values, first.day)
    if end is None or begin is None or begin == 0:
        raise CrossCheckError(
            "live_return", series_id, names, "the second ETF has no overlapping closes"
        )
    if days[bisect_right(days, last.day) - 1] < last.day - timedelta(days=4):
        raise CrossCheckError(
            "live_return", series_id, names, "the second ETF's latest close is too old to compare"
        )
    r_primary = float(last.value / first.value - 1)
    r_secondary = float(end / begin - 1)
    if abs(r_primary - r_secondary) > tolerance:
        raise CrossCheckError(
            "live_return",
            series_id,
            names,
            f"{WEEK_ROWS}-day returns to {last.day} differ: {r_primary:.2%} against "
            f"{r_secondary:.2%} (tolerance {tolerance:.2%})",
        )


def build_live_levels(
    config: GuardConfig,
    price_providers: Sequence[PriceProvider],
    macro_providers: Sequence[MacroProvider],
    store: MarketStore,
    today: date,
    *,
    returns_check: ReturnsCheck = "strict",
    history: History = "weekly",
    required_close: date | None = None,
) -> LiveLevels:
    """Fetch, check and store the live series as of ``today`` (the run's local date).

    No lag is allowed: the latest close of each ETF must be the latest completed session.
    ``returns_check="advisory"`` reports a return disagreement instead of raising it.
    ``required_close`` is a date the primary must have closed on, or the series is not ready.
    """
    live = config.live_series
    data = config.data.model_copy(update={"stale_trading_days": 0})
    start = today - timedelta(days=live.history_days)
    primary_listing = Listing.from_listing_id(live.primary, "GBP")
    secondary_listing = Listing.from_listing_id(live.secondary, "USD")

    primary = _fetch_one(primary_listing, price_providers, start, today, today)
    if required_close is not None and primary.observations[-1].day < required_close:
        raise DataIntegrityError(
            "not_ready",
            live.primary,
            (primary.source_id,),
            "today's close is not available yet",
        )
    secondary = _fetch_one(secondary_listing, price_providers, start, today, today)
    fx = _fx(macro_providers, start, today, today, config)

    needed = required_closes(config.signals, history)
    if len(primary.observations) < needed:
        raise DataIntegrityError(
            "history",
            live.primary,
            (primary.source_id,),
            f"only {len(primary.observations)} closes; the signals need {needed}",
        )
    check_staleness(
        live.primary,
        primary.source_id,
        primary.observations[-1].day,
        today,
        data,
        markets=("uk",),
    )
    check_jumps(live.primary, primary.source_id, primary.observations, data)
    check_staleness(
        live.secondary,
        secondary.source_id,
        secondary.observations[-1].day,
        today,
        data,
        markets=("us",),
    )
    names = (
        f"{live.primary}@{primary.source_id}",
        f"{live.secondary}@{secondary.source_id}",
    )
    converted = _convert(secondary.observations, fx.observations)
    unconfirmed_note: str | None = None
    try:
        _check_returns(primary, converted, names, live.primary, data.live_return_tolerance)
    except CrossCheckError as e:
        if returns_check == "strict":
            raise
        unconfirmed_note = redact_amounts(str(e))

    if unconfirmed_note is None:
        store.save(
            CheckedSeries(live.primary, primary.observations, (primary.source_id,), Decimal(0))
        )
    obs = primary.observations
    levels = tuple(float(o.value) for o in obs)
    weekly = levels[-1] / levels[-1 - WEEK_ROWS] - 1.0 if len(levels) > WEEK_ROWS else None
    return LiveLevels(
        days=tuple(o.day for o in obs),
        levels=levels,
        sources=(*names, "+".join(fx.sources)),
        weekly_return=weekly,
        last_close_date=obs[-1].day,
        unconfirmed=unconfirmed_note is not None,
        unconfirmed_note=unconfirmed_note,
    )
