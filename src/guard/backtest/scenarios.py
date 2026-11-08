"""Crisis-window summaries: achieved drawdown against buy-and-hold and a floor-only portfolio."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from guard.backtest.engine import BacktestParams, BacktestResult, Series, run_fixed_weight
from guard.backtest.metrics import max_drawdown
from guard.config.models import Ladder


@dataclass(frozen=True)
class CrisisWindow:
    name: str
    start: date
    end: date


DEFAULT_WINDOWS: tuple[CrisisWindow, ...] = (
    CrisisWindow("2008-2009", date(2008, 1, 1), date(2009, 6, 30)),
    CrisisWindow("2020", date(2020, 2, 1), date(2020, 12, 31)),
    CrisisWindow("2022", date(2022, 1, 1), date(2022, 12, 31)),
)


@dataclass(frozen=True)
class ScenarioSummary:
    """Max drawdowns are negative fractions measured inside the window. ``return_lag`` is the
    buy-and-hold return minus the strategy return over the window (positive: strategy trailed)."""

    achieved_max_drawdown: float
    buy_and_hold_max_drawdown: float
    floor_only_max_drawdown: float
    return_lag: float


def _slice(r: BacktestResult, w: CrisisWindow) -> tuple[list[date], list[float]]:
    pairs = [(d, v) for d, v in zip(r.dates, r.index, strict=True) if w.start <= d <= w.end]
    return [d for d, _ in pairs], [v for _, v in pairs]


def crisis_summary(
    strategy_result: BacktestResult,
    buy_and_hold_result: BacktestResult,
    floor_only_result: BacktestResult,
    windows: Sequence[CrisisWindow] = DEFAULT_WINDOWS,
) -> dict[str, ScenarioSummary | None]:
    """One summary per window; None when any of the three runs has fewer than two points in it."""
    out: dict[str, ScenarioSummary | None] = {}
    for w in windows:
        (sd, si), (bd, bi), (fd, fi) = (
            _slice(strategy_result, w),
            _slice(buy_and_hold_result, w),
            _slice(floor_only_result, w),
        )
        if min(len(sd), len(bd), len(fd)) < 2:
            out[w.name] = None
            continue
        out[w.name] = ScenarioSummary(
            achieved_max_drawdown=max_drawdown(sd, si).depth,
            buy_and_hold_max_drawdown=max_drawdown(bd, bi).depth,
            floor_only_max_drawdown=max_drawdown(fd, fi).depth,
            return_lag=(bi[-1] / bi[0] - 1.0) - (si[-1] / si[0] - 1.0),
        )
    return out


def floor_only_run(
    equity: Series,
    safe: Series,
    ladder: Ladder,
    *,
    drift_band: float,
    data_version: str,
    params: BacktestParams | None = None,
    config_version: str = "",
) -> BacktestResult:
    """A constant-weight portfolio at ``ladder.equity_floor``, rebalanced on decision days."""
    base = params or BacktestParams()
    p = BacktestParams(**{**base.__dict__, "drift_band": drift_band})
    return run_fixed_weight(
        equity,
        safe,
        ladder.equity_floor,
        schedule="decision_days",
        params=p,
        data_version=data_version,
        config_version=config_version,
        label="floor_only",
    )
