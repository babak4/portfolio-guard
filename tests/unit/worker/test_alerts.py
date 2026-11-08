"""The scheduler wired to the real notifier with a fake sender."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard.notify.deliver import Channel, make_notifier
from guard.notify.heartbeat import heartbeat_job
from guard.run.results import DailyEvent, DailyResult, RunResult
from guard.storage.alert_store import AlertStore
from guard.worker.schedule import guarded
from tests.unit.notify.fixtures import weekly
from tests.unit.run.fakes import make_deps, market

CH = Channel("telegram", "tgram://1:abc/2", secrets=("1:abc",))
WED = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)


class Recorder:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send(self, channel: Channel, message: object) -> None:
        self.sent.append(message)


@pytest.fixture
def deps(data_dir: Path):  # type: ignore[no-untyped-def]
    prices, macro = market(datetime(2026, 11, 10).date())
    d = make_deps(prices, macro, now=WED)
    yield d
    d.close()


def _wire(deps):  # type: ignore[no-untyped-def]
    rec = Recorder()
    deps.notify = make_notifier(deps, sender=rec, channels=lambda: [CH])
    return rec


def test_weekly_ok_run_triggers_one_report(deps) -> None:  # type: ignore[no-untyped-def]
    rec = _wire(deps)
    guarded("weekly", "weekly", deps, lambda d, n: weekly())()
    assert len(rec.sent) == 1 and rec.sent[0].title.startswith("🟢")  # type: ignore[attr-defined]


def test_stopped_run_alert_has_no_balances(deps) -> None:  # type: ignore[no-untyped-def]
    rec = _wire(deps)
    reason = "isa total 12345.67 differs from the sum 11000.00 by £1345.67"
    from guard.run.redact import redact_amounts

    guarded(
        "weekly",
        "weekly",
        deps,
        lambda d, n: RunResult("stopped", "weekly", "2026-11-09", reason=redact_amounts(reason)),
    )()
    assert len(rec.sent) == 1
    text = json.dumps(rec.sent[0].__dict__)  # type: ignore[attr-defined]
    assert "12345" not in text and "11000" not in text and "£" not in text and "1345" not in text
    assert "stopped" in text


def test_failed_job_alert_is_delivered_before_deps_close(data_dir: Path) -> None:
    prices, macro = market(datetime(2026, 11, 10).date())
    rec = Recorder()

    def factory():  # type: ignore[no-untyped-def]
        d = make_deps(prices, macro, now=WED)
        d.notify = make_notifier(d, sender=rec, channels=lambda: [CH])
        return d

    def boom(d, n):  # type: ignore[no-untyped-def]
        raise RuntimeError("balance 99999")

    guarded("weekly", "weekly", factory, boom)()
    assert len(rec.sent) == 1 and "99999" not in json.dumps(rec.sent[0].__dict__)  # type: ignore[attr-defined]


def test_daily_events_send_daily_messages(deps) -> None:  # type: ignore[no-untyped-def]
    rec = _wire(deps)
    ev = DailyEvent("day_drop", "The market fell 5.3% in a day.", False)
    res = DailyResult("ok", "2026-11-10", events=(ev,), day_return=-0.053, week_return=-0.02)
    guarded("daily", "daily", deps, lambda d, n: res)()
    assert len(rec.sent) == 1 and "5.3%" in rec.sent[0].body  # type: ignore[attr-defined]


def test_heartbeat_sends_once_when_the_week_has_no_run(deps) -> None:  # type: ignore[no-untyped-def]
    rec = _wire(deps)
    for _ in range(2):
        guarded("heartbeat", "heartbeat", deps, heartbeat_job)()
    assert len(rec.sent) == 1
    assert "missing" in rec.sent[0].title.lower() + rec.sent[0].body.lower()  # type: ignore[attr-defined]


def test_heartbeat_silent_when_the_week_has_an_ok_run(deps) -> None:  # type: ignore[no-untyped-def]
    from guard.storage import run_store

    rec = _wire(deps)
    run_store.record_run(
        deps.ops,
        kind="weekly",
        slot="2026-11-09",
        status="ok",
        stop_reason=None,
        config_version="v",
        code_version="c",
        started_at=WED,
        finished_at=WED,
    )
    guarded("heartbeat", "heartbeat", deps, heartbeat_job)()
    assert rec.sent == []
    assert AlertStore(deps.ops).rows() == []
