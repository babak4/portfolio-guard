from __future__ import annotations

import re
from decimal import Decimal

import pytest

from guard.advice.model import ContributionLine, Flag, Trade
from guard.notify.render import (
    Message,
    render_alert,
    render_daily,
    render_heartbeat,
    render_notice,
    render_weekly,
)
from guard.run.results import Notice, RunResult
from tests.unit.notify.fixtures import advice, daily, weekly

ACCOUNTS = ("isa", "gia")


def _text(m: Message) -> str:
    return f"{m.title}\n{m.body}"


def test_a_quiet_hold_week_is_green_and_says_hold() -> None:
    m = render_weekly(weekly(), ACCOUNTS)
    assert m.title.startswith("🟢")
    assert "hold at 90% equity" in m.body
    assert "No trades advised" in m.body
    assert "As of close 2026-11-10" in m.body
    assert "Details on the dashboard" in m.body
    assert m.urgency == "info"


def test_a_derisk_with_trades_names_funds_and_direction_only() -> None:
    adv = advice(
        trades=(
            Trade("isa", "VWRP", "SELL", Decimal("8123")),
            Trade("isa", "CSH2", "BUY", Decimal("8123")),
        ),
        contributions=(ContributionLine("gia", "CSH2", Decimal("1000")),),
        flags=(Flag("fee", "warn", "x"), Flag("note", "info", "y")),
    )
    m = render_weekly(weekly(kind="derisk", before=0.9, after=0.75, adv=adv), ACCOUNTS)
    assert m.title.startswith("🟡")
    assert "reduce toward 75% equity" in m.body
    assert "2 trades advised: sell VWRP, buy CSH2 (amounts on the dashboard)" in m.body
    assert "leans to safe" in m.body
    assert "1 warning" in m.body
    assert m.urgency == "warn"


def test_contribution_leaning_to_growth() -> None:
    adv = advice(contributions=(ContributionLine("gia", "VWRP", Decimal("1000")),))
    assert "leans to growth" in render_weekly(weekly(adv=adv), ACCOUNTS).body


def test_blocked_advice_is_red_with_a_reason_without_digits_or_accounts() -> None:
    adv = advice(status="blocked", reasons=("trade of 12345.67 in gia exceeds the cap",))
    m = render_weekly(weekly(adv=adv, reason="trade of 12345.67 in gia exceeds the cap"), ACCOUNTS)
    assert m.title.startswith("🔴")
    assert "blocked" in m.body
    assert "12345" not in m.body and not re.search(r"\bgia\b", m.body)


def test_paused_week_is_amber() -> None:
    r = RunResult("paused", "weekly", "2026-11-09", reason="paused by the owner")
    m = render_weekly(r, ACCOUNTS)
    assert m.title.startswith("🟡") and "paused" in m.body


def test_daily_message_wording() -> None:
    result, event = daily(fast=True)
    m = render_daily(result, event)
    assert "World shares fell 5.3% yesterday" in m.body
    assert "fast path applies" in m.body
    assert m.title.startswith("🔴") and m.urgency == "urgent"


def test_daily_not_fast_and_unconfirmed() -> None:
    result, event = daily(unconfirmed=True)
    m = render_daily(result, event)
    assert "fast path does not apply" in m.body
    assert "not confirmed by the second source" in m.body
    assert m.title.startswith("🟡")


def test_daily_paused_wording() -> None:
    result, event = daily(paused=True)
    assert "advice is paused" in render_daily(result, event).body


def test_stopped_alert_hides_amounts_and_accounts() -> None:
    n = Notice("weekly", "stopped", "2026-11-09", "gia total 1234.56 differs from isa by £99.10")
    m = render_alert(n, ACCOUNTS)
    assert m.title.startswith("🔴") and m.urgency == "urgent"
    assert "stopped" in m.body
    assert not re.search(r"\d{3,}|£|\bgia\b|\bisa\b", m.body.replace("2026-11-09", ""))


@pytest.mark.parametrize("status", ["failed", "locked"])
def test_failed_and_locked_alerts(status: str) -> None:
    m = render_alert(Notice("daily", status, "2026-11-10", "boom"), ACCOUNTS)
    assert {"failed": "failed", "locked": "could not start"}[status] in m.body


def test_heartbeat_alert() -> None:
    m = render_heartbeat("2026-11-09")
    assert "weekly advice is missing" in m.title.lower() or "weekly advice is missing" in m.body
    assert m.urgency == "urgent"


def test_render_notice_dispatches() -> None:
    n = Notice("weekly", "report", "2026-11-09", "", result=weekly())
    assert render_notice(n, ACCOUNTS).title.startswith("🟢")
    assert "missing" in _text(render_notice(Notice("heartbeat", "missing", "2026-11-09", ""), ()))
    result, event = daily()
    assert (
        "World shares"
        in render_notice(Notice("daily", "event", "s", "m", result=result, event=event), ()).body
    )


def test_no_rendered_text_leaks_amounts_pounds_or_accounts() -> None:
    amounts = ["48123", "5347", "8123", "1000", "53470", "12345.67"]
    reasons = ("balance 53470 vs 48123 in isa", "gia cash £1,000 short")
    fixtures = []
    for kind, before, after in [("hold", 0.9, 0.9), ("derisk", 0.9, 0.75), ("reentry", 0.75, 0.9)]:
        for status in ("advice", "blocked", "paused"):
            for with_trades in (False, True):
                trades = (
                    (
                        Trade("isa", "VWRP", "SELL", Decimal("8123")),
                        Trade("gia", "CSH2", "BUY", Decimal("12345.67")),
                    )
                    if with_trades
                    else ()
                )
                adv = advice(
                    status=status,
                    trades=trades,
                    contributions=(ContributionLine("gia", "VWRP", Decimal("1000")),),
                    flags=(Flag("fee", "warn", "isa holds £48,123"),),
                    reasons=reasons,
                )
                fixtures.append(
                    weekly(
                        status="ok",
                        kind=kind,
                        before=before,
                        after=after,
                        adv=adv,
                        reason=reasons[0] if status != "advice" else None,
                    )
                )
    messages = [render_weekly(f, ACCOUNTS) for f in fixtures]
    for reason in reasons + ("isa total 99999.99",):
        for st in ("stopped", "failed", "locked"):
            messages.append(render_alert(Notice("weekly", st, "2026-11-09", reason), ACCOUNTS))
    for fast in (False, True):
        result, event = daily(fast=fast, unconfirmed=fast)
        messages.append(render_daily(result, event))
    messages.append(render_heartbeat("2026-11-09"))
    assert len(messages) >= 30
    for m in messages:
        text = _text(m)
        assert "£" not in text
        for a in amounts:
            assert a not in text.replace(",", "")
        assert not re.search(r"\b(isa|gia)\b", text, re.IGNORECASE)
