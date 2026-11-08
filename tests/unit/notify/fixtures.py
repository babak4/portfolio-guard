"""Hand-built runs and advice for the alert tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from guard.advice.model import Advice, AllocationRow, ContributionLine, Flag, Sleeve, Trade
from guard.ladder import Decision
from guard.run.results import DailyEvent, DailyResult, RunResult

SLEEVE = Sleeve(0.9, 0.1, 0.9, 0.1, 0.9)


def advice(
    *,
    status: str = "advice",
    trades: tuple[Trade, ...] = (),
    contributions: tuple[ContributionLine, ...] = (),
    flags: tuple[Flag, ...] = (),
    reasons: tuple[str, ...] = (),
) -> Advice:
    allocation = (
        AllocationRow("VWRP", "growth", Decimal("48123"), 0.9, 0.9, 0.0),
        AllocationRow("CSH2", "safe", Decimal("5347"), 0.1, 0.1, 0.0),
    )
    return Advice(
        status=status,  # type: ignore[arg-type]
        reasons=reasons,
        trades=trades,
        contribution_plan=contributions,
        allocation=allocation,
        sleeve=SLEEVE,
        flags=flags,
        guardrails=(),
        portfolio_value=Decimal("53470"),
        tactical_value=Decimal("53470"),
    )


def weekly(
    *,
    status: str = "ok",
    kind: str = "hold",
    before: float = 0.9,
    after: float = 0.9,
    adv: Advice | None = None,
    reason: str | None = None,
) -> RunResult:
    return RunResult(
        status,
        "weekly",
        "2026-11-09",
        reason=reason,
        advice=adv if adv is not None else advice(),
        decision=Decision(kind, before, after, 0),  # type: ignore[arg-type]
        last_close_date=date(2026, 11, 10),
    )


def daily(
    *, fast: bool = False, unconfirmed: bool = False, paused: bool = False, week: float = -0.03
) -> tuple[DailyResult, DailyEvent]:
    event = DailyEvent("day_drop", "The market fell 5.3% in a day.", fast)
    result = DailyResult(
        "ok",
        "2026-11-10",
        events=(event,),
        day_return=-0.053,
        week_return=week,
        last_close_date=date(2026, 11, 10),
        unconfirmed=unconfirmed,
        paused=paused,
    )
    return result, event
