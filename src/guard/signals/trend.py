"""Trend: the latest level against a long moving average."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from guard.signals.result import GAP, SignalResult


def trend_signal(levels: Sequence[float], window: int) -> SignalResult:
    """Negative when the latest level is below the mean of the last ``window`` levels."""
    if window <= 0 or len(levels) < window:
        return GAP
    sma = sum(levels[-window:]) / window
    value = levels[-1] / sma - 1.0
    return SignalResult(value=value, negative=levels[-1] < sma)


def trend_signal_months(days: Sequence[date], levels: Sequence[float], months: int) -> SignalResult:
    """Negative when the latest level is below the mean of the last ``months`` month-end closes.

    The latest level stands in for the current, unfinished month's close.
    """
    if months <= 0 or len(days) != len(levels) or not levels:
        return GAP
    closes: list[float] = []
    for i, d in enumerate(days):
        if i + 1 == len(days) or (days[i + 1].year, days[i + 1].month) != (d.year, d.month):
            closes.append(levels[i])
    if len(closes) < months:
        return GAP
    sma = sum(closes[-months:]) / months
    return SignalResult(value=levels[-1] / sma - 1.0, negative=levels[-1] < sma)
