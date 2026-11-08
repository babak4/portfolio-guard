"""Turn runs and notices into short push messages. Pure: no I/O, clock or secrets.

A message names funds and directions and gives percentages, never money amounts or account
names; the amounts are on the dashboard.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from guard.advice.model import Advice
from guard.report import daily_light, weekly_light
from guard.run.redact import redact_amounts
from guard.run.results import DailyEvent, DailyResult, Notice, RunResult

Urgency = Literal["info", "warn", "urgent"]

MARKER = {"green": "🟢", "amber": "🟡", "red": "🔴"}
URGENCY: dict[str, Urgency] = {"green": "info", "amber": "warn", "red": "urgent"}
DASHBOARD = "Details on the dashboard."


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    urgency: Urgency
    kind: str = "alert"


def scrub(text: str, account_ids: Sequence[str] = ()) -> str:
    """``text`` with numbers replaced, currency signs gone and account ids named generically."""
    out = redact_amounts(text).replace("£", "").replace("$", "").replace("€", "")
    for account in account_ids:
        out = re.sub(rf"\b{re.escape(account)}\b", "an account", out, flags=re.IGNORECASE)
    return out


def _percent(x: float) -> str:
    return f"{x:.0%}"


def _ladder_phrase(result: RunResult) -> str | None:
    d = result.decision
    if d is None:
        return None
    if d.equity_after < d.equity_before:
        return f"reduce toward {_percent(d.equity_after)} equity"
    if d.equity_after > d.equity_before:
        return f"raise toward {_percent(d.equity_after)} equity"
    suffix = " (data gap)" if d.kind == "data_gap" else ""
    return f"hold at {_percent(d.equity_after)} equity{suffix}"


def _trades_phrase(advice: Advice) -> str:
    if not advice.trades:
        return "no trades advised"
    seen: list[str] = []
    for t in advice.trades:
        part = f"{t.action.lower()} {t.symbol}"
        if part not in seen:
            seen.append(part)
    n = len(advice.trades)
    noun = "trade" if n == 1 else "trades"
    return f"{n} {noun} advised: {', '.join(seen)} (amounts on the dashboard)"


def _contribution_phrase(advice: Advice) -> str:
    if not advice.contribution_plan:
        return "There is no monthly contribution plan"
    roles = {row.symbol: row.role for row in advice.allocation}
    totals = {"growth": Decimal(0), "safe": Decimal(0)}
    for line in advice.contribution_plan:
        role = roles.get(line.symbol)
        if role in totals:
            totals[role] += line.amount
    if totals["growth"] > 0 and totals["safe"] > 0:
        return "The monthly contribution plan splits between growth and safe"
    if totals["safe"] > 0:
        return "The monthly contribution plan leans to safe"
    return "The monthly contribution plan leans to growth"


def _warn_count(advice: Advice | None) -> int:
    return 0 if advice is None else sum(1 for f in advice.flags if f.severity == "warn")


def render_weekly(result: RunResult, account_ids: Sequence[str] = ()) -> Message:
    advice = result.advice
    decision = result.decision
    warns = _warn_count(advice)
    trades = 0 if advice is None else len(advice.trades)
    light = weekly_light(
        result.status,
        None if decision is None else decision.kind,
        None if advice is None else advice.status,
        warn_flags=warns,
        trades=trades,
    )
    lines: list[str] = []
    blocked = advice is not None and advice.status == "blocked"
    if result.status == "paused":
        title = "Weekly check: advice is paused"
        lines.append("Advice is paused, so there is no advice this week.")
    elif blocked:
        title = "Weekly check: advice blocked"
        reason = result.reason or "; ".join(advice.reasons if advice else ())
        lines.append(f"Advice was blocked: {scrub(reason, account_ids)}".rstrip(": "))
    else:
        title = "Weekly check"
        phrase = _ladder_phrase(result)
        if phrase:
            lines.append(f"Ladder: {phrase}.")
        if advice is not None:
            trades_text = _trades_phrase(advice)
            lines.append(f"{trades_text[0].upper()}{trades_text[1:]}.")
            lines.append(f"{_contribution_phrase(advice)}.")
            if warns:
                lines.append(f"{warns} {'warning' if warns == 1 else 'warnings'}.")
        else:
            lines.append("This week's run is complete.")
    if result.last_close_date is not None:
        lines.append(f"As of close {result.last_close_date.isoformat()}.")
    lines.append(DASHBOARD)
    return Message(f"{MARKER[light]} {title}", "\n".join(lines), URGENCY[light], "weekly")


def render_daily(result: DailyResult, event: DailyEvent) -> Message:
    light = daily_light(event_count=len(result.events) or 1, fast_path=event.fast_path)
    lines = [_daily_headline(result, event)]
    if result.paused:
        lines.append("No fast-path guidance: advice is paused.")
    elif event.fast_path:
        lines.append("The fast path applies.")
    else:
        lines.append("The fast path does not apply.")
    if result.unconfirmed:
        lines.append("This move is not confirmed by the second source.")
    lines.append(DASHBOARD)
    return Message(f"{MARKER[light]} Daily check", "\n".join(lines), URGENCY[light], "daily")


def _daily_headline(result: DailyResult, event: DailyEvent) -> str:
    if event.kind == "day_drop" and result.day_return is not None:
        return f"World shares fell {-result.day_return:.1%} yesterday."
    if event.kind == "week_drop" and result.week_return is not None:
        return f"World shares fell {-result.week_return:.1%} over five trading days."
    if event.kind == "volatility":
        return "Market volatility is above its limit."
    return event.message.split(". ")[0].rstrip(".") + "."


_STATUS_WORDS = {"stopped": "stopped", "failed": "failed", "locked": "could not start"}


def render_alert(notice: Notice, account_ids: Sequence[str] = ()) -> Message:
    what = {"weekly": "weekly run", "daily": "daily check", "snapshot": "snapshot"}.get(
        notice.kind, notice.kind
    )
    word = _STATUS_WORDS.get(notice.status, notice.status)
    lines = [f"The {what} {word}."]
    reason = scrub(notice.message, account_ids).strip()
    if reason:
        lines.append(f"Reason: {reason}")
    if notice.slot:
        lines.append(f"Slot {notice.slot}.")
    lines.append("No advice was produced. Check the dashboard and the worker log.")
    return Message(
        f"{MARKER['red']} Portfolio Guard: {what} {word}", "\n".join(lines), "urgent", notice.kind
    )


def render_heartbeat(slot: str) -> Message:
    body = (
        f"No weekly advice has been recorded for the week of {slot}. "
        "The worker may be down or the run may have been missed. Check the dashboard."
    )
    return Message(f"{MARKER['red']} Weekly advice is missing", body, "urgent", "heartbeat")


def render_test() -> Message:
    return Message(
        "Portfolio Guard test: alerts are working", "This is a test message.", "info", "test"
    )


def render_notice(notice: Notice, account_ids: Sequence[str] = ()) -> Message:
    """The message for a notice, falling back to the alert wording when the data is missing."""
    result = notice.result
    if notice.kind == "heartbeat":
        return render_heartbeat(notice.slot)
    if notice.status == "report" and isinstance(result, RunResult):
        return render_weekly(result, account_ids)
    if notice.status == "event" and isinstance(result, DailyResult) and notice.event is not None:
        return render_daily(result, notice.event)
    if notice.status == "event":
        text = scrub(notice.message, account_ids)
        return Message(f"{MARKER['amber']} Daily check", f"{text}\n{DASHBOARD}", "warn", "daily")
    return render_alert(notice, account_ids)
