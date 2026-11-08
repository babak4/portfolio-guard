"""Plain data returned by the read API. Money is ``Decimal``; weights and returns are fractions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from guard.ladder import LadderState
from guard.report import Light

CONTRIBUTION_LABEL = "monthly contribution (once a month; skip if already made)"


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    kind: str
    slot: str
    status: str
    reason: str | None
    started_at: str
    finished_at: str | None
    light: Light | None
    ladder_kind: str | None = None
    trades: int = 0
    warn_flags: int = 0


@dataclass(frozen=True)
class LastRun:
    """The last successful run of one kind; ``slot`` is ``None`` when there has been none."""

    kind: str
    slot: str | None
    finished_at: datetime | None
    age: timedelta | None


@dataclass(frozen=True)
class StatusView:
    paused: bool
    has_runs: bool
    light: Light | None
    latest_weekly: RunSummary | None
    last_runs: tuple[LastRun, ...]
    weekly_overdue: bool
    expected_weekly_slot: str
    warning: str | None
    alert_deliveries: tuple[dict[str, Any], ...] = ()
    alert_problem: str | None = None  # why the owner may not be hearing from the system
    week_paused: bool = False  # the expected week is covered only by a run recorded as paused
    snapshot_failed: bool = False


@dataclass(frozen=True)
class TradeLine:
    account_id: str
    symbol: str
    action: str
    amount: Decimal


@dataclass(frozen=True)
class ContributionItem:
    account_id: str
    symbol: str
    amount: Decimal
    label: str = CONTRIBUTION_LABEL


@dataclass(frozen=True)
class AllocationLine:
    symbol: str
    role: str
    value: Decimal
    weight: float
    target_weight: float | None
    drift: float | None
    verified: bool | None  # None when the fund is not in the stored fund reference


@dataclass(frozen=True)
class SleeveView:
    growth_actual: float
    safe_actual: float
    growth_advised: float
    safe_advised: float
    growth_after: float


@dataclass(frozen=True)
class FlagView:
    kind: str
    severity: str
    message: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class GuardrailView:
    name: str
    passed: bool
    adjusted: bool
    detail: str


@dataclass(frozen=True)
class DecisionView:
    kind: str
    equity_before: float
    equity_after: float
    step: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SignalView:
    """``trigger_level`` is the value at which the signal turns negative (``None`` if unknown)."""

    key: str
    label: str
    value: float | None
    negative: bool
    data_gap: bool
    trigger_level: float | None
    trigger_text: str


@dataclass(frozen=True)
class AdviceView:
    """One weekly run. ``has_advice`` is false for a run that stopped or failed, and for the
    latest run while advice is paused; the explanation is then in ``run_status`` and
    ``stop_reason``."""

    run_id: str
    slot: str
    run_status: str
    stop_reason: str | None
    paused: bool
    has_advice: bool
    light: Light | None
    advice_status: str | None = None
    reasons: tuple[str, ...] = ()
    trades: tuple[TradeLine, ...] = ()
    contributions: tuple[ContributionItem, ...] = ()
    allocation: tuple[AllocationLine, ...] = ()
    sleeve: SleeveView | None = None
    flags: tuple[FlagView, ...] = ()
    guardrails: tuple[GuardrailView, ...] = ()
    decision: DecisionView | None = None
    ladder_state: LadderState | None = None
    signals: tuple[SignalView, ...] = ()
    last_close_date: date | None = None
    advice_hash: str | None = None
    unverified_symbols: tuple[str, ...] = ()
    portfolio_value: Decimal | None = None


@dataclass(frozen=True)
class LadderEntry:
    week: int
    run_id: str | None
    equity: float
    step: int
    created_at: str


@dataclass(frozen=True)
class HistoryView:
    runs: tuple[RunSummary, ...]
    ladder: tuple[LadderEntry, ...]


@dataclass(frozen=True)
class ReplayView:
    status: str  # identical | different | code_changed | unavailable
    message: str | None = None
    differing: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResponseView:
    id: int
    run_id: str
    slot: str
    decision: str
    note: str | None
    recorded_at: str
    current: bool  # false once a later response supersedes this one


@dataclass(frozen=True)
class RunDetail:
    run_id: str
    kind: str
    slot: str
    status: str
    reason: str | None
    config_version: str | None
    code_version: str | None
    inputs_hash: str | None
    advice_hash: str | None
    payload: dict[str, Any] | None
    payload_json: str | None
    replay: ReplayView | None
    responses: tuple[ResponseView, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IndexView:
    """The reference index's closes with the lines the signals use, aligned by position."""

    run_id: str
    slot: str
    days: tuple[date, ...]
    closes: tuple[float, ...]
    average: tuple[float | None, ...]
    high: tuple[float | None, ...]
    window: int
    average_note: str | None
    lookback_days: int
    drawdown: float | None
    drawdown_threshold: float
    drawdown_cap: float
    signals: tuple[SignalView, ...]
    last_close_date: date | None


@dataclass(frozen=True)
class TotalPoint:
    day: date
    total: Decimal
    cash: Decimal


@dataclass(frozen=True)
class AccountSeries:
    account_id: str
    points: tuple[TotalPoint, ...]


@dataclass(frozen=True)
class PortfolioView:
    series: tuple[AccountSeries, ...]
    combined: tuple[TotalPoint, ...]
    allocation: tuple[AllocationLine, ...]
    allocation_run_id: str | None
    sleeve: SleeveView | None
