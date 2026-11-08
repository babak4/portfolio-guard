"""Drawdown: distance below the trailing high."""

from __future__ import annotations

from collections.abc import Sequence

from guard.signals.result import GAP, SignalResult


def drawdown_signal(levels: Sequence[float], lookback: int, threshold: float) -> SignalResult:
    """Negative when the latest level is ``threshold`` or more below the trailing high."""
    if lookback <= 0 or not levels:
        return GAP
    high = max(levels[-lookback:])
    value = levels[-1] / high - 1.0
    return SignalResult(value=value, negative=value <= -threshold + 1e-12)
