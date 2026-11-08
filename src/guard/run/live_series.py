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

from guard.config import GuardConfig
from guard.market.base import (
    CrossCheckError,
    DataIntegrityError,
    Listing,
    MacroProvider,
    Observation,
    PriceProvider,
)
from guard.market.crosscheck import CheckedSeries, cross_check
from guard.market.fetch import (
    DEFAULT_MACRO_ALIASES,
    FetchResult,
    SourceSeries,
    fetch_macro,
    fetch_prices,
)
from guard.market.sanity import check_jumps, check_staleness
from guard.market.store import MarketStore

WEEK_ROWS = 5
FX_SERIES = "USD_PER_GBP"


@dataclass(frozen=True)
class LiveLevels:
    """``levels`` are the primary's closes (floats) on ``days``; ``weekly_return`` is the move
    over the last five trading days, or ``None`` with fewer than six closes."""

    days: tuple[date, ...]
    levels: tuple[float, ...]
    sources: tuple[str, ...]
    weekly_return: float | None
    last_close_date: date


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
    result = fetch_macro(
        FX_SERIES, providers, start, end, aliases=DEFAULT_MACRO_ALIASES, max_sources=2
    )
    trimmed = FetchResult(
        result.series_id,
        [SourceSeries(s.source_id, tuple(_before(s.observations, today))) for s in result.sources],
        result.errors,
    )
    trimmed.sources = [s for s in trimmed.sources if s.observations]
    return cross_check(trimmed, cfg.data)


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
) -> LiveLevels:
    """Fetch, check and store the live series as of ``today`` (the run's local date)."""
    live = config.live_series
    start = today - timedelta(days=live.history_days)
    primary_listing = Listing.from_listing_id(live.primary, "GBP")
    secondary_listing = Listing.from_listing_id(live.secondary, "USD")

    primary = _fetch_one(primary_listing, price_providers, start, today, today)
    secondary = _fetch_one(secondary_listing, price_providers, start, today, today)
    fx = _fx(macro_providers, start, today, today, config)

    check_staleness(
        live.primary,
        primary.source_id,
        primary.observations[-1].day,
        today,
        config.data,
        markets=("uk",),
    )
    check_jumps(live.primary, primary.source_id, primary.observations, config.data)
    check_staleness(
        live.secondary,
        secondary.source_id,
        secondary.observations[-1].day,
        today,
        config.data,
        markets=("us",),
    )
    names = (
        f"{live.primary}@{primary.source_id}",
        f"{live.secondary}@{secondary.source_id}",
    )
    converted = _convert(secondary.observations, fx.observations)
    _check_returns(primary, converted, names, live.primary, config.data.live_return_tolerance)

    store.save(CheckedSeries(live.primary, primary.observations, (primary.source_id,), Decimal(0)))
    obs = primary.observations
    levels = tuple(float(o.value) for o in obs)
    weekly = levels[-1] / levels[-1 - WEEK_ROWS] - 1.0 if len(levels) > WEEK_ROWS else None
    return LiveLevels(
        days=tuple(o.day for o in obs),
        levels=levels,
        sources=(*names, "+".join(fx.sources)),
        weekly_return=weekly,
        last_close_date=obs[-1].day,
    )
