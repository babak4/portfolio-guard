"""What a run returns to its caller (the command line now, the alert layer later)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from guard.advice import Advice
from guard.ladder import Decision
from guard.storage.run_store import RunRecord


@dataclass(frozen=True)
class Notice:
    """Something the owner must hear about, handed to ``Deps.notify``. ``kind`` is ``weekly``,
    ``daily`` or ``snapshot``; ``status`` is ``stopped``, ``failed``, ``locked`` or ``event``. The
    message carries no balances and no account names."""

    kind: str
    status: str
    slot: str
    message: str


@dataclass(frozen=True)
class RunResult:
    """``status`` is ``ok``, ``paused``, ``stopped``, ``failed`` or ``locked`` (another run holds
    the writer lock and nothing was recorded). ``run`` is the stored row; ``advice`` and
    ``decision`` are set only for a run computed now, not for an ``ok`` slot found already done.
    ``last_close_date`` is the close the advice is as of, when known."""

    status: str
    kind: str
    slot: str
    run: RunRecord | None = None
    reason: str | None = None
    advice: Advice | None = None
    decision: Decision | None = None
    last_close_date: date | None = None


@dataclass(frozen=True)
class DailyEvent:
    """One reason the owner is told about. Carries no money amounts and no account names."""

    kind: str  # day_drop | week_drop | volatility
    message: str
    fast_path: bool


@dataclass(frozen=True)
class DailyResult:
    """``status`` is ``ok``, ``skipped``, ``stopped``, ``failed`` or ``locked``. ``unconfirmed``
    means the second ETF did not confirm the move; ``paused`` that advice is paused."""

    status: str
    slot: str
    run: RunRecord | None = None
    reason: str | None = None
    events: tuple[DailyEvent, ...] = field(default_factory=tuple)
    day_return: float | None = None
    week_return: float | None = None
    last_close_date: date | None = None
    unconfirmed: bool = False
    paused: bool = False
