from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from guard import actions
from guard.run.weekly import run_weekly
from guard.storage import open_ops, run_store, writer_lock
from tests.unit.run.fakes import make_deps, market

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
RUN_ID = "weekly-2026-11-09"


@pytest.fixture
def recorded(data_dir: Path) -> None:
    prices, macro = market(date(2026, 11, 10))
    deps = make_deps(prices, macro)
    assert run_weekly(deps, NOW).status == "ok"
    deps.close()


def _paused() -> bool:
    c = open_ops()
    try:
        return run_store.get_paused(c)
    finally:
        c.close()


def test_pause_and_resume_write_the_switch_the_worker_reads(data_dir: Path) -> None:
    assert actions.pause(open_ops).ok and _paused()
    assert actions.resume(open_ops).ok and not _paused()


def test_write_waits_for_a_running_job_then_succeeds(data_dir: Path) -> None:
    held, release = threading.Event(), threading.Event()

    def job() -> None:
        with writer_lock("run", wait_seconds=1) as acquired:
            assert acquired
            held.set()
            release.wait(5)

    t = threading.Thread(target=job)
    t.start()
    assert held.wait(5)
    threading.Timer(0.4, release.set).start()
    start = time.monotonic()
    result = actions.pause(open_ops, wait_seconds=5)
    waited = time.monotonic() - start
    t.join()
    assert result.ok and _paused()
    assert waited >= 0.3  # it waited for the job instead of failing at once


def test_write_gives_a_clear_message_when_the_job_never_finishes(data_dir: Path) -> None:
    held, release = threading.Event(), threading.Event()

    def job() -> None:
        with writer_lock("run", wait_seconds=1):
            held.set()
            release.wait(5)

    t = threading.Thread(target=job)
    t.start()
    assert held.wait(5)
    result = actions.pause(open_ops, wait_seconds=0.2)
    release.set()
    t.join()
    assert not result.ok and "running" in result.message and not _paused()


def test_respond_records_and_the_latest_supersedes(recorded: None) -> None:
    first = actions.respond(open_ops, RUN_ID, "followed", "  done  ")
    second = actions.respond(open_ops, RUN_ID, "changed", "bought less")
    assert first.ok and second.ok
    c = open_ops()
    rows = run_store.responses_for(c, RUN_ID)
    c.close()
    assert [(r.decision, r.note) for r in rows] == [
        ("followed", "done"),
        ("changed", "bought less"),
    ]
    assert rows[1].supersedes == rows[0].id


def test_respond_to_a_stopped_run_is_refused_with_a_message(data_dir: Path) -> None:
    c = open_ops()
    run_store.record_run(
        c, kind="weekly", slot="2026-11-09", status="stopped", started_at=NOW, finished_at=NOW,
        stop_reason="stale",
    )  # fmt: skip
    c.close()
    result = actions.respond(open_ops, RUN_ID, "followed", None)
    assert not result.ok and "no advice" in result.message.lower()


def test_respond_to_an_unknown_run_and_bad_decision(recorded: None) -> None:
    assert "not found" in actions.respond(open_ops, "weekly-1999-01-04", "followed", None).message
    bad = actions.respond(open_ops, RUN_ID, "maybe", None)  # type: ignore[arg-type]
    assert not bad.ok
