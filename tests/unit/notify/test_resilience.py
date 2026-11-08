"""The alert path must not fail silently, and what it sends must not repeat or leak."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard.notify.deliver import Channel, make_notifier, push_static
from guard.notify.render import Message, render_notice
from guard.run.results import DailyEvent, DailyResult, Notice
from guard.storage import open_ops
from guard.storage.alert_store import AlertStore
from tests.unit.run.fakes import make_deps, market

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
TOKEN = "123456:SECRET-TOKEN-abcdefghijklmnopqrstuv"
TG = Channel("telegram", f"tgram://{TOKEN}/42", secrets=(TOKEN,))


class Sender:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing
        self.sent: list[Message] = []

    def send(self, channel: Channel, message: Message) -> None:
        if self.failing:
            raise RuntimeError(f"down {channel.url}")
        self.sent.append(message)


@pytest.fixture
def deps(data_dir: Path):  # type: ignore[no-untyped-def]
    prices, macro = market(datetime(2026, 11, 10).date())
    d = make_deps(prices, macro, now=NOW)
    yield d
    d.close()


def test_push_static_sends_the_text_and_records_it(data_dir: Path) -> None:
    s = Sender()
    conn = open_ops()
    ok = push_static("Portfolio Guard: weekly run could not start", "Check.", kind="weekly",
                     sender=s, channels=lambda: [TG], conn=conn)  # fmt: skip
    assert ok and s.sent[0].title == "Portfolio Guard: weekly run could not start"
    assert AlertStore(conn).rows()[0]["ok"] == 1
    conn.close()


def test_push_static_never_raises_and_never_logs_secrets(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def boom() -> list[Channel]:
        raise OSError(TOKEN)

    with caplog.at_level(logging.DEBUG):
        assert push_static("t", "b", kind="weekly", sender=Sender(), channels=boom) is False
        assert (
            push_static("t", "b", kind="weekly", sender=Sender(True), channels=lambda: [TG])
            is False
        )
    assert TOKEN not in caplog.text


def test_a_notifier_that_throws_still_pushes_a_static_message(
    deps,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    sender = Sender()

    def broken(*_a: object, **_k: object) -> Message:
        raise ValueError("balance 12345.67")

    notify = make_notifier(deps, sender=sender, channels=lambda: [TG])
    monkeypatch.setattr("guard.notify.deliver.render_notice", broken)
    with caplog.at_level(logging.DEBUG):
        notify(Notice("weekly", "stopped", "2026-11-09", "why"))
    assert [m.title for m in sender.sent] == ["Portfolio Guard: alert pipeline failed"]
    assert "12345" not in caplog.text and "ValueError" in caplog.text
    assert AlertStore(deps.ops).rows()[-1]["kind"] == "pipeline"


def test_daily_events_are_sent_once_per_slot_and_kind(deps) -> None:  # type: ignore[no-untyped-def]
    sender = Sender()
    notify = make_notifier(deps, sender=sender, channels=lambda: [TG])
    ev = DailyEvent("day_drop", "The market fell 5.3% in a day.", False)
    res = DailyResult("ok", "2026-11-10", events=(ev,), day_return=-0.053)
    other = DailyEvent("volatility", "Volatility is 30%.", False)
    n = Notice("daily", "event", "2026-11-10", ev.message, result=res, event=ev)
    notify(n)
    notify(n)  # a restart or misfire
    assert len(sender.sent) == 1
    notify(Notice("daily", "event", "2026-11-10", other.message, result=res, event=other))
    assert len(sender.sent) == 2
    notify(Notice("daily", "event", "2026-11-11", ev.message, result=res, event=ev))
    assert len(sender.sent) == 3


def test_a_failed_daily_push_is_retried_next_time(deps) -> None:  # type: ignore[no-untyped-def]
    ev = DailyEvent("day_drop", "The market fell 5.3% in a day.", False)
    n = Notice("daily", "event", "2026-11-10", ev.message, event=ev)
    make_notifier(deps, sender=Sender(True), channels=lambda: [TG])(n)
    good = Sender()
    make_notifier(deps, sender=good, channels=lambda: [TG])(n)
    assert len(good.sent) == 1


def test_only_a_missing_alert_marks_the_heartbeat_slot(deps) -> None:  # type: ignore[no-untyped-def]
    notify = make_notifier(deps, sender=Sender(), channels=lambda: [TG])
    notify(Notice("heartbeat", "failed", "2026-11-09", "heartbeat failed"))
    assert not AlertStore(deps.ops).once_done("heartbeat:2026-11-09")


def test_a_failed_heartbeat_has_its_own_text_not_missing() -> None:
    m = render_notice(Notice("heartbeat", "failed", "", "heartbeat failed: RuntimeError"), ())
    assert "missing" not in (m.title + m.body).lower()
    assert "heartbeat" in (m.title + m.body).lower()


def test_an_unknown_daily_event_headline_is_scrubbed() -> None:
    res = DailyResult("ok", "2026-11-10")
    ev = DailyEvent("something_new", "Your isa balance is £12,345.67 today. More.", False)
    m = render_notice(
        Notice("daily", "event", "2026-11-10", ev.message, result=res, event=ev), ["isa"]
    )
    assert "12,345" not in m.body and "£" not in m.body and "isa" not in m.body.lower()


def test_scrub_masks_glued_amounts_and_keeps_dates_and_symbols() -> None:
    from guard.notify.render import scrub

    out = scrub("GBP12000 in isa; balance12,345.67; CSH2 on 2026-11-09", ["isa"])
    assert "12000" not in out and "12,345" not in out
    assert "CSH2" in out and "2026-11-09" in out and "isa" not in out
