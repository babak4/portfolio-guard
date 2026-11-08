from __future__ import annotations

from typing import Any

import pytest

from guard.notify import deliver as mod
from guard.notify.deliver import AppriseSender, Channel, DeliveryError
from guard.notify.render import Message


class FakeApprise:
    ok = True
    calls: list[tuple[str, dict[str, Any]]] = []

    def add(self, url: str) -> bool:
        self.calls.append(("add", {"url": url}))
        return True

    def notify(self, **kw: Any) -> bool:
        self.calls.append(("notify", kw))
        return self.ok


def test_apprise_sender_passes_title_body_and_raises_on_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeApprise.calls = []
    monkeypatch.setattr(mod.apprise, "Apprise", FakeApprise)
    AppriseSender().send(Channel("telegram", "tgram://x/1"), Message("t", "b", "warn"))
    assert FakeApprise.calls[1][1]["title"] == "t" and FakeApprise.calls[1][1]["body"] == "b"
    FakeApprise.ok = False
    try:
        with pytest.raises(DeliveryError) as e:
            AppriseSender().send(Channel("telegram", "tgram://x/1"), Message("t", "b", "info"))
        assert "tgram" not in str(e.value)
    finally:
        FakeApprise.ok = True
