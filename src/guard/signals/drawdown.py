"""Drawdown: distance below the trailing high."""

from __future__ import annotations

from collections.abc import Sequence

from guard.signals.result import GAP, SignalResult


def drawdown_signal(levels: Sequence[float], lookback: int, threshold: float) -> SignalResult:
    """Negative when the latest level is ``threshold`` or more below the trailing high.

    A data gap when there are fewer than ``lookback`` levels: a partial window would understate
    the high.
    """
    if lookback <= 0 or len(levels) < lookback:
        return GAP
    high = max(levels[-lookback:])
    value = levels[-1] / high - 1.0
    return SignalResult(value=value, negative=value <= -threshold + 1e-12)
