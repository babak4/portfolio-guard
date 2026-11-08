from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from guard.config.models import Ladder
from guard.signals import SignalResult


@dataclass(frozen=True)
class LadderState:
    """Everything the ladder remembers between weekly runs.

    ``equity`` is the advised equity weight. ``step`` is the ladder step being moved to (it
    changes when a move starts, not when it finishes). ``streak`` counts consecutive runs wanting
    a step further from equities than ``step``. ``pending_equity`` is the unfinished remainder of
    a capped de-risk; ``reentry_dest`` and ``reentry_tranche`` describe a staged return.
    """

    equity: float
    step: int
    streak: int = 0
    last_change_week: int | None = None
    pending_equity: float | None = None
    reentry_dest: float | None = None
    reentry_tranche: float = 0.0


@dataclass(frozen=True)
class Signals:
    """One weekly run's inputs. ``weekly_return`` is the reference index's return over the week."""

    trend: SignalResult
    drawdown: SignalResult
    volatility: SignalResult
    weekly_return: float | None = None

    def all(self) -> tuple[SignalResult, SignalResult, SignalResult]:
        return (self.trend, self.drawdown, self.volatility)


DecisionKind = Literal["hold", "derisk", "continue", "reentry", "data_gap"]


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    equity_before: float
    equity_after: float
    step: int
    reasons: tuple[str, ...] = ()


def initial_state(cfg: Ladder) -> LadderState:
    return LadderState(equity=cfg.steps[0].equity, step=0)
