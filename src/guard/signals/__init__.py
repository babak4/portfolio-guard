"""Pure signal functions over a daily GBP level series. Missing data is a gap, never a guess."""

from guard.signals.drawdown import drawdown_signal
from guard.signals.result import SignalResult
from guard.signals.trend import trend_signal, trend_signal_months
from guard.signals.volatility import volatility_signal

__all__ = [
    "SignalResult",
    "drawdown_signal",
    "trend_signal",
    "trend_signal_months",
    "volatility_signal",
]
