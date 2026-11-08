"""Advice result types. Money is ``Decimal`` in whole pounds; weights are fractions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Action = Literal["BUY", "SELL"]
Status = Literal["advice", "paused", "blocked"]
Severity = Literal["info", "warn"]


@dataclass(frozen=True)
class Trade:
    account_id: str
    symbol: str
    action: Action
    amount: Decimal


@dataclass(frozen=True)
class ContributionLine:
    account_id: str
    symbol: str
    amount: Decimal


@dataclass(frozen=True)
class Flag:
    """A note for the owner. ``kind`` groups flags (us_exposure, fee, unknown_fund, ...)."""

    kind: str
    severity: Severity
    message: str
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuardrailCheck:
    """One guardrail result. ``adjusted`` is set when the check passed only by changing the
    advice (the weekly cap scaling trades down); ``detail`` says how."""

    name: str
    passed: bool
    detail: str
    adjusted: bool = False


@dataclass(frozen=True)
class AllocationRow:
    """One fund. ``weight`` is its share of the whole portfolio; ``target_weight`` is the share
    the advised equity level implies (``None`` for fixed satellites)."""

    symbol: str
    role: str
    value: Decimal
    weight: float
    target_weight: float | None
    drift: float | None


@dataclass(frozen=True)
class Sleeve:
    """Growth and safe shares of the tactical value: now, as advised, and after the trades.

    ``growth_after`` is the growth share once the advised trades are made (contributions not
    counted); after the weekly cap it can differ from ``growth_advised``.
    """

    growth_actual: float
    safe_actual: float
    growth_advised: float
    safe_advised: float
    growth_after: float


@dataclass(frozen=True)
class Advice:
    status: Status
    reasons: tuple[str, ...]
    trades: tuple[Trade, ...]
    contribution_plan: tuple[ContributionLine, ...]
    allocation: tuple[AllocationRow, ...]
    sleeve: Sleeve
    flags: tuple[Flag, ...]
    guardrails: tuple[GuardrailCheck, ...]
    portfolio_value: Decimal
    tactical_value: Decimal
