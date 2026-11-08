"""Performance metrics over a time-weighted index series (pure functions).

Consecutive index points are treated as daily observations for volatility, Sharpe and Sortino
(annualised with 252). The risk-free rate is taken as zero. CAGR uses calendar days / 365.25.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from guard.ladder import Decision

TRADING_DAYS = 252
DAYS_PER_YEAR = 365.25
WHIPSAW_DAYS = 28
_TOL = 1e-12


@dataclass(frozen=True)
class Drawdown:
    """Worst peak-to-trough fall: ``depth`` is negative (0.0 when the series never falls)."""

    depth: float
    peak: date
    trough: date


def _years(dates: Sequence[date]) -> float:
    return (dates[-1] - dates[0]).days / DAYS_PER_YEAR


def _returns(index: Sequence[float]) -> list[float]:
    return [b / a - 1.0 for a, b in zip(index, index[1:], strict=False)]


def cagr(dates: Sequence[date], index: Sequence[float]) -> float:
    """Compound annual growth rate between the first and last points.

    Raises ValueError (None would read as zero growth) unless there are two points on different
    dates and a positive starting level.
    """
    years = _years(dates) if dates else 0.0
    if len(index) < 2 or years <= 0 or index[0] <= 0:
        raise ValueError("need at least two points on different dates and a positive start")
    return float((index[-1] / index[0]) ** (1.0 / years) - 1.0)


def max_drawdown(dates: Sequence[date], index: Sequence[float]) -> Drawdown:
    """The deepest fall from a running peak, with the peak and trough dates."""
    peak_i = 0
    worst = Drawdown(0.0, dates[0], dates[0])
    for i, v in enumerate(index):
        if v > index[peak_i]:
            peak_i = i
        depth = v / index[peak_i] - 1.0
        if depth < worst.depth:
            worst = Drawdown(depth, dates[peak_i], dates[i])
    return worst


def time_to_recover(dates: Sequence[date], index: Sequence[float]) -> int | None:
    """Calendar days from the max-drawdown trough back to the prior peak level; None if never."""
    dd = max_drawdown(dates, index)
    if abs(dd.depth) < _TOL:
        return 0
    peak_level = index[list(dates).index(dd.peak)]
    start = list(dates).index(dd.trough)
    for i in range(start + 1, len(dates)):
        if index[i] >= peak_level:
            return (dates[i] - dd.trough).days
    return None


def _mean_std(xs: Sequence[float]) -> tuple[float, float]:
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return mean, math.sqrt(var)


def volatility(index: Sequence[float]) -> float | None:
    """Annualised sample standard deviation of daily returns; None with fewer than two returns
    (three points), where a sample deviation is undefined."""
    rets = _returns(index)
    if len(rets) < 2:
        return None
    return _mean_std(rets)[1] * math.sqrt(TRADING_DAYS)


def sharpe(index: Sequence[float]) -> float | None:
    """Annualised mean over standard deviation of daily returns (risk-free rate 0).

    None with fewer than two returns, or when the deviation is below 1e-12 (a flat or
    constant-growth series, where the ratio would be numerical noise).
    """
    rets = _returns(index)
    if len(rets) < 2:
        return None
    mean, std = _mean_std(rets)
    return None if std < _TOL else mean / std * math.sqrt(TRADING_DAYS)


def sortino(index: Sequence[float]) -> float | None:
    """Annualised mean over downside deviation below 0 (risk-free rate 0).

    None with fewer than two returns, or when the downside deviation is below 1e-12.
    """
    rets = _returns(index)
    if len(rets) < 2:
        return None
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in rets) / len(rets))
    return None if downside < _TOL else (sum(rets) / len(rets)) / downside * math.sqrt(TRADING_DAYS)


def calmar(dates: Sequence[date], index: Sequence[float]) -> float | None:
    """CAGR over the absolute max drawdown; None when there was no drawdown (under 1e-12).

    Raises ValueError, like ``cagr``, when a drawdown exists but the span is not positive.
    """
    depth = max_drawdown(dates, index).depth
    return None if abs(depth) < _TOL else cagr(dates, index) / abs(depth)


def turnover(total_traded: float, dates: Sequence[date], values: Sequence[float]) -> float | None:
    """One-way annual turnover: half the traded value (buy plus sell legs) per year, over the
    average portfolio value. None when the span or the average value is not positive."""
    if len(dates) < 2 or not values:
        return None
    years, average = _years(dates), sum(values) / len(values)
    if years <= 0 or average <= 0:
        return None
    return total_traded / 2.0 / years / average


def time_defensive(advised_equity: Sequence[float], step0_equity: float) -> float:
    """Fraction of days the advised equity weight was below the ladder's step-0 equity."""
    return sum(1 for w in advised_equity if w < step0_equity - 1e-9) / len(advised_equity)


def whipsaw_round_trips(decisions: Sequence[tuple[date, Decision]]) -> int:
    """Count de-risk episodes followed by the start of a re-entry within four weeks.

    An episode runs from a de-risk decision until the next re-entry decision; it counts once if
    that re-entry starts within 28 days of the episode's last reduction (de-risk or continue).
    """
    count = 0
    last_cut: date | None = None
    for day, d in decisions:
        if d.kind in ("derisk", "continue"):
            last_cut = day
        elif d.kind == "reentry" and last_cut is not None:
            if (day - last_cut).days <= WHIPSAW_DAYS:
                count += 1
            last_cut = None
    return count


def _value_on_or_before(dates: Sequence[date], index: Sequence[float], day: date) -> float | None:
    found: float | None = None
    for d, v in zip(dates, index, strict=True):
        if d > day:
            break
        found = v
    return found


def _years_back(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def rolling_lag(
    dates: Sequence[date],
    strategy: Sequence[float],
    benchmark: Sequence[float],
    years: int,
) -> list[tuple[date, float]]:
    """Return lag in percent per year over rolling ``years``-year windows, stepping one month.

    Windows end on the last date of each calendar month. Lag is the benchmark's annualised
    return minus the strategy's, times 100: positive means the strategy trailed.
    """
    month_ends = [
        d
        for i, d in enumerate(dates)
        if i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (d.year, d.month)
    ]
    out: list[tuple[date, float]] = []
    for end in month_ends:
        start = _years_back(end, years)
        if start < dates[0]:
            continue
        s0 = _value_on_or_before(dates, strategy, start)
        b0 = _value_on_or_before(dates, benchmark, start)
        s1 = _value_on_or_before(dates, strategy, end)
        b1 = _value_on_or_before(dates, benchmark, end)
        if None in (s0, b0, s1, b1):
            continue
        assert s0 is not None and b0 is not None and s1 is not None and b1 is not None
        span = (end - start).days / DAYS_PER_YEAR
        lag = ((b1 / b0) ** (1 / span) - (s1 / s0) ** (1 / span)) * 100.0
        out.append((end, lag))
    return out


__all__ = [
    "Drawdown",
    "cagr",
    "calmar",
    "max_drawdown",
    "rolling_lag",
    "sharpe",
    "sortino",
    "time_defensive",
    "time_to_recover",
    "turnover",
    "volatility",
    "whipsaw_round_trips",
]
