from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard.config import load_config
from guard.notify.heartbeat import heartbeat_status
from guard.storage import open_ops, run_store
from guard.storage.alert_store import AlertStore

WED_EVENING = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)  # Wednesday of the week of 2026-11-09


@pytest.fixture
def conn(data_dir: Path):  # type: ignore[no-untyped-def]
    c = open_ops()
    yield c
    c.close()


def _run(conn, status: str, slot: str = "2026-11-09") -> None:  # type: ignore[no-untyped-def]
    run_store.record_run(
        conn,
        kind="weekly",
        slot=slot,
        status=status,
        stop_reason=None,
        config_version="v",
        code_version="c",
        started_at=WED_EVENING,
        finished_at=WED_EVENING,
    )


def test_missing_when_no_run_this_week(conn) -> None:  # type: ignore[no-untyped-def]
    s = heartbeat_status(conn, WED_EVENING, load_config()[0])
    assert s.state == "missing" and s.slot == "2026-11-09"


def test_an_older_weeks_run_does_not_count(conn) -> None:  # type: ignore[no-untyped-def]
    _run(conn, "ok", "2026-11-02")
    assert heartbeat_status(conn, WED_EVENING, load_config()[0]).state == "missing"


@pytest.mark.parametrize("status", ["ok", "paused"])
def test_ok_or_paused_run_is_fine(conn, status: str) -> None:  # type: ignore[no-untyped-def]
    _run(conn, status)
    assert heartbeat_status(conn, WED_EVENING, load_config()[0]).state == "ok"


def test_stopped_run_still_counts_as_missing(conn) -> None:  # type: ignore[no-untyped-def]
    _run(conn, "stopped")
    assert heartbeat_status(conn, WED_EVENING, load_config()[0]).state == "missing"


def test_silent_after_alerting(conn) -> None:  # type: ignore[no-untyped-def]
    AlertStore(conn).mark_once("heartbeat:2026-11-09", WED_EVENING)
    assert heartbeat_status(conn, WED_EVENING, load_config()[0]).state == "alerted"


def test_runs_on_every_day_and_alerts_only_after_the_weekly_due_time(conn) -> None:  # type: ignore[no-untyped-def]
    cfg = load_config()[0]
    # Wednesday 17:59 London: this week's slot is not yet overdue, so the check is about last week
    before = datetime(2026, 11, 11, 17, 59, tzinfo=UTC)
    assert heartbeat_status(conn, before, cfg).slot == "2026-11-02"
    # Friday: this week is overdue, once
    friday = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
    s = heartbeat_status(conn, friday, cfg)
    assert s.state == "missing" and s.slot == "2026-11-09"
    AlertStore(conn).mark_once("heartbeat:2026-11-09", friday)
    assert heartbeat_status(conn, friday, cfg).state == "alerted"


def test_a_missed_week_is_found_on_monday_not_only_next_wednesday(conn) -> None:  # type: ignore[no-untyped-def]
    monday = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    s = heartbeat_status(conn, monday, load_config()[0])
    assert s.state == "missing" and s.slot == "2026-11-09"


def test_skips_while_a_weekly_run_holds_the_run_lock(conn, data_dir: Path) -> None:  # type: ignore[no-untyped-def]
    from guard.notify.heartbeat import heartbeat_job
    from guard.storage import writer_lock
    from guard.worker.schedule import _notices

    class D:
        ops = conn
        config = load_config()[0]

    with writer_lock("run") as held:
        assert held
        status = heartbeat_job(D(), WED_EVENING)  # type: ignore[arg-type]
    assert status.state == "running" and _notices("heartbeat", status) == []
    assert heartbeat_job(D(), WED_EVENING).state == "missing"  # type: ignore[arg-type]


def test_a_paused_week_counts_as_done_for_the_heartbeat(conn) -> None:  # type: ignore[no-untyped-def]
    _run(conn, "paused")
    assert run_store.weekly_slot_state(conn, "2026-11-09") == "paused"
    assert heartbeat_status(conn, WED_EVENING, load_config()[0]).state == "ok"
