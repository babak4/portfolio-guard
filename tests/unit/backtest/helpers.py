"""Shared builders for backtest tests: weekday calendars and a small, controllable ladder."""

from __future__ import annotations

from datetime import date, timedelta

from guard.config.models import Ladder, LadderStep, Signals


def weekdays(start: date, n: int) -> list[date]:
    """``n`` consecutive Monday-to-Friday dates starting on ``start`` (which must be a weekday)."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def flat_then(n: int, changes: dict[int, float], base: float = 100.0) -> list[float]:
    """A level series that holds its last value, changing only on the listed day indices."""
    out: list[float] = []
    level = base
    for i in range(n):
        level = changes.get(i, level)
        out.append(level)
    return out


def trend_only_signals() -> Signals:
    """Only the trend signal can fire: it votes negative when the previous close fell."""
    return Signals(
        trend_method="sma_days",
        trend_window=2,
        drawdown_threshold=0.99,
        drawdown_lookback_weeks=1,
        volatility_window_days=2,
        volatility_threshold=1000.0,
    )


def two_step_ladder(
    high: float = 0.8, low: float = 0.6, *, cooldown_weeks: int = 0, floor: float = 0.0
) -> Ladder:
    """Step 0 holds ``high`` equity, step 1 holds ``low``; moves are immediate and uncapped."""
    return Ladder(
        steps=(LadderStep(equity=high, safe=1 - high), LadderStep(equity=low, safe=1 - low)),
        confirmation_runs=1,
        fast_path_weekly_drop=0.5,
        min_shift=0.05,
        cooldown_weeks=cooldown_weeks,
        max_weekly_change=1.0,
        equity_floor=floor,
        reentry_tranches=1,
    )


def fixed_ladder(equity: float) -> Ladder:
    """A one-step ladder: it can never move, so the advised weight is always ``equity``."""
    return Ladder(
        steps=(LadderStep(equity=equity, safe=1 - equity),),
        confirmation_runs=1,
        fast_path_weekly_drop=0.5,
        min_shift=0.05,
        cooldown_weeks=0,
        max_weekly_change=1.0,
        equity_floor=0.0,
        reentry_tranches=1,
    )
