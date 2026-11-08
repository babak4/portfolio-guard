from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard.notify.deliver import (
    Channel,
    channels_from_secrets,
    deliver,
    make_notifier,
)
from guard.notify.render import Message
from guard.run.results import Notice
from guard.storage import open_ops
from guard.storage.alert_store import AlertStore
from tests.unit.notify.fixtures import weekly
from tests.unit.run.fakes import make_deps, market

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
TOKEN = "123456:SECRET-TOKEN-abcdefghijklmnopqrstuv"
TG = Channel("telegram", f"tgram://{TOKEN}/42", secrets=(TOKEN,))
NTFY = Channel("ntfy", "ntfy://my-topic", secrets=("my-topic",))
MSG = Message("🟢 Test", "body", "info", kind="weekly")


class FakeSender:
    def __init__(self, failing: set[str] = frozenset(), leak: str = "") -> None:  # type: ignore[assignment]
        self.failing = set(failing)
        self.leak = leak
        self.sent: list[tuple[str, Message]] = []

    def send(self, channel: Channel, message: Message) -> None:
        if channel.name in self.failing:
            raise RuntimeError(f"cannot reach {channel.url} with {self.leak} balance 12345.67")
        self.sent.append((channel.name, message))


@pytest.fixture
def store(data_dir: Path):  # type: ignore[no-untyped-def]
    conn = open_ops()
    yield AlertStore(conn)
    conn.close()


def _rows(store: AlertStore) -> list[dict[str, object]]:
    return store.rows()


def test_primary_ok_records_one_attempt(store: AlertStore) -> None:
    sender = FakeSender()
    result = deliver(MSG, sender, [TG, NTFY], store, NOW)
    assert result.delivered and result.channel == "telegram"
    assert [c for c, _ in sender.sent] == ["telegram"]
    rows = _rows(store)
    assert len(rows) == 1 and rows[0]["channel"] == "telegram" and rows[0]["ok"] == 1
    assert rows[0]["kind"] == "weekly" and rows[0]["urgency"] == "info"


def test_primary_fails_fallback_sends_and_both_are_recorded(store: AlertStore) -> None:
    sender = FakeSender(failing={"telegram"}, leak=TOKEN)
    result = deliver(MSG, sender, [TG, NTFY], store, NOW)
    assert result.delivered and result.channel == "ntfy"
    rows = _rows(store)
    assert [(r["channel"], r["ok"]) for r in rows] == [("telegram", 0), ("ntfy", 1)]


def test_both_fail_records_two_failed_rows_and_does_not_raise(store: AlertStore) -> None:
    sender = FakeSender(failing={"telegram", "ntfy"}, leak=TOKEN)
    result = deliver(MSG, sender, [TG, NTFY], store, NOW)
    assert not result.delivered and result.channel is None
    assert [(r["channel"], r["ok"]) for r in _rows(store)] == [("telegram", 0), ("ntfy", 0)]


def test_nothing_configured_records_not_configured(store: AlertStore, caplog) -> None:  # type: ignore[no-untyped-def]
    with caplog.at_level(logging.INFO):
        result = deliver(MSG, FakeSender(), [], store, NOW)
    assert not result.delivered
    rows = _rows(store)
    assert len(rows) == 1 and rows[0]["ok"] == 0 and rows[0]["error"] == "not configured"


def test_token_and_url_never_reach_logs_or_the_database(store: AlertStore, caplog) -> None:  # type: ignore[no-untyped-def]
    sender = FakeSender(failing={"telegram", "ntfy"}, leak=TOKEN)
    with caplog.at_level(logging.DEBUG):
        deliver(MSG, sender, [TG, NTFY], store, NOW)
    dump = json.dumps(_rows(store), default=str)
    for secret in (TOKEN, "tgram://", "my-topic", "12345.67"):
        assert secret not in dump
        assert secret not in caplog.text


def test_channels_from_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "GUARD_FAKE_SECRETS",
        json.dumps(
            {
                "guard_telegram_bot_token": "1:abc",
                "guard_telegram_chat_id": "42",
                "guard_ntfy_topic": "topic",
                "guard_ntfy_server": "https://ntfy.example.com",
            }
        ),
    )
    chans = channels_from_secrets()
    assert [c.name for c in chans] == ["telegram", "ntfy"]
    assert chans[0].url == "tgram://1:abc/42"
    assert chans[1].url == "ntfys://ntfy.example.com/topic"


def test_channels_from_secrets_none_and_ntfy_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARD_FAKE_SECRETS", "{}")
    assert channels_from_secrets() == []
    monkeypatch.setenv("GUARD_FAKE_SECRETS", json.dumps({"guard_ntfy_topic": "t"}))
    assert [c.url for c in channels_from_secrets()] == ["ntfy://t"]


def test_repr_of_a_channel_hides_the_url() -> None:
    assert TOKEN not in repr(TG)


def _deps(data_dir: Path):  # type: ignore[no-untyped-def]
    prices, macro = market(datetime(2026, 11, 10).date())
    return make_deps(prices, macro)


def test_notifier_renders_and_delivers_a_notice(data_dir: Path) -> None:
    deps = _deps(data_dir)
    sender = FakeSender()
    notify = make_notifier(deps, sender=sender, channels=lambda: [TG])
    notify(Notice("weekly", "report", "2026-11-09", "", result=weekly()))
    assert len(sender.sent) == 1 and sender.sent[0][1].title.startswith("🟢")
    assert len(AlertStore(deps.ops).rows()) == 1
    deps.close()


def test_notifier_never_raises(data_dir: Path, caplog) -> None:  # type: ignore[no-untyped-def]
    deps = _deps(data_dir)

    def boom() -> list[Channel]:
        raise OSError(TOKEN)

    notify = make_notifier(deps, sender=FakeSender(), channels=boom)
    with caplog.at_level(logging.DEBUG):
        notify(Notice("weekly", "stopped", "s", "why"))
    assert TOKEN not in caplog.text
    deps.close()


def test_notifier_marks_a_delivered_heartbeat_once(data_dir: Path) -> None:
    deps = _deps(data_dir)
    notify = make_notifier(deps, sender=FakeSender(), channels=lambda: [TG])
    notify(Notice("heartbeat", "missing", "2026-11-09", ""))
    assert AlertStore(deps.ops).once_done("heartbeat:2026-11-09")
    deps.close()


def test_a_failed_heartbeat_is_not_marked(data_dir: Path) -> None:
    deps = _deps(data_dir)
    notify = make_notifier(deps, sender=FakeSender(failing={"telegram"}), channels=lambda: [TG])
    notify(Notice("heartbeat", "missing", "2026-11-09", ""))
    assert not AlertStore(deps.ops).once_done("heartbeat:2026-11-09")
    deps.close()
