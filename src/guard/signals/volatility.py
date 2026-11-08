"""Volatility: annualised realised volatility of daily log returns."""

from __future__ import annotations

import math
from collections.abc import Sequence

from guard.signals.result import GAP, SignalResult

TRADING_DAYS = 252


def volatility_signal(levels: Sequence[float], window: int, threshold: float) -> SignalResult:
    """Negative when realised volatility over the last ``window`` returns exceeds ``threshold``."""
    if window < 2 or len(levels) < window + 1:
        return GAP
    tail = levels[-(window + 1) :]
    rets = [math.log(b / a) for a, b in zip(tail, tail[1:], strict=False)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    value = math.sqrt(var) * math.sqrt(TRADING_DAYS)
    return SignalResult(value=value, negative=value > threshold)
