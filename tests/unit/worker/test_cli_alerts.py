from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from guard.notify.deliver import Channel
from guard.worker.main import main
from tests.unit.run.fakes import make_deps, market

TOKEN = "123456:SECRET-TOKEN-abcdefghijklmnopqrstuv"


class Sender:
    def __init__(self, failing: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.failing = set(failing)
        self.sent: list[tuple[str, str]] = []

    def send(self, channel: Channel, message) -> None:  # type: ignore[no-untyped-def]
        if channel.name in self.failing:
            raise RuntimeError(f"down {channel.url}")
        self.sent.append((channel.name, message.title))


def _run(sender: Sender, *argv: str) -> tuple[int, str]:
    prices, macro = market(datetime(2026, 11, 10).date())
    out = io.StringIO()
    code = main(list(argv), deps_factory=lambda: make_deps(prices, macro), out=out, sender=sender)
    return code, out.getvalue()


@pytest.fixture
def secrets(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "GUARD_FAKE_SECRETS",
        json.dumps(
            {
                "guard_telegram_bot_token": TOKEN,
                "guard_telegram_chat_id": "42",
                "guard_ntfy_topic": "my-topic",
            }
        ),
    )


def test_test_alert_reports_the_channel_and_never_the_token(secrets: None) -> None:
    sender = Sender()
    code, text = _run(sender, "test-alert")
    assert code == 0 and "telegram" in text and TOKEN not in text
    assert sender.sent == [("telegram", "Portfolio Guard test: alerts are working")]


def test_test_alert_falls_back(secrets: None) -> None:
    code, text = _run(Sender(failing={"telegram"}), "test-alert")
    assert code == 0 and "ntfy" in text and TOKEN not in text


def test_test_alert_exits_1_when_every_channel_fails(secrets: None) -> None:
    code, text = _run(Sender(failing={"telegram", "ntfy"}), "test-alert")
    assert code == 1 and "failed" in text and TOKEN not in text


def test_test_alert_with_nothing_configured(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUARD_FAKE_SECRETS", "{}")
    code, text = _run(Sender(), "test-alert")
    assert code == 1 and "not configured" in text


def test_status_shows_alert_configuration_and_last_delivery(secrets: None) -> None:
    _, before = _run(Sender(), "status")
    assert "alerts: configured (telegram, ntfy)" in before and "never" in before
    _run(Sender(), "test-alert")
    _, after = _run(Sender(), "status")
    assert "alert telegram: ok" in after and TOKEN not in after


def test_status_when_alerts_are_not_configured(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GUARD_FAKE_SECRETS", "{}")
    assert "alerts: not configured" in _run(Sender(), "status")[1]
