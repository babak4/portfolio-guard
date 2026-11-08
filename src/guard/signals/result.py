from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalResult:
    """``negative`` means the signal votes to reduce equity. A data gap is never negative."""

    value: float | None
    negative: bool
    data_gap: bool = False


GAP = SignalResult(value=None, negative=False, data_gap=True)
