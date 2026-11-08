"""Silence is never "all clear": failures of the alert path, the scheduler and the check itself."""

from __future__ import annotations

import io
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from guard.notify.ping import ping_dead_man
from guard.run.deps import Deps, LazyWarehouse, default_deps, open_with_retry
from guard.run.results import Notice, RunResult
from guard.storage import open_ops, run_store
from guard.storage.alert_store import PROBLEM_FAILED_EVERYWHERE, AlertStore
from guard.worker.main import main
from guard.worker.schedule import (
    HEARTBEAT_JOB,
    TICK_JOB,
    build_scheduler,
    guarded,
    missed_job_listener,
    tick_job,
)
from guard.worker.startup import run_startup
from tests.unit.run.fakes import make_deps, market

NOW = datetime(2026, 11, 11, 12, 0, 5, tzinfo=UTC)  # Wednesday, the weekly run just started
LATE = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)  # the week of 2026-11-16 is a day overdue


class Cli:
    def __init__(self, now: datetime = NOW) -> None:
        self.prices, self.macro = market(date(2026, 11, 10))
        self.now = now

    def __call__(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        code = main(
            list(argv),
            deps_factory=lambda: make_deps(self.prices, self.macro, now=self.now),
            out=out,
        )
        return code, out.getvalue()


@pytest.fixture(autouse=True)
def _no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARD_FAKE_SECRETS", "{}")


def _weekly(conn, slot: str, status: str = "ok") -> None:  # type: ignore[no-untyped-def]
    run_store.record_run(
        conn, kind="weekly", slot=slot, status=status, started_at=NOW, finished_at=NOW
    )


# ---- the failure-reporting path ---------------------------------------------------------------
def test_deps_that_cannot_open_send_the_static_push(data_dir: Path) -> None:
    pushed: list[tuple[str, str, str]] = []

    def factory() -> Deps:
        raise OSError("database is locked, token abc123")

    guarded(
        "weekly run",
        "weekly",
        factory,
        lambda d, n: None,
        static_push=lambda title, body, kind: pushed.append((title, body, kind)),
    )()
    assert pushed == [("Portfolio Guard: weekly run could not start", pushed[0][1], "weekly")]
    assert "abc123" not in pushed[0][1]


def test_a_static_push_that_raises_is_swallowed(data_dir: Path) -> None:
    def factory() -> Deps:
        raise OSError("x")

    def broken(*_a: object) -> None:
        raise RuntimeError("y")

    guarded("weekly run", "weekly", factory, lambda d, n: None, static_push=broken)()


# ---- the scheduler tick, listener and dead-man ping -------------------------------------------
def test_the_tick_job_records_the_time(data_dir: Path) -> None:
    deps = make_deps(*market(date(2026, 11, 10)), now=NOW)
    tick_job(deps)()
    assert run_store.scheduler_tick(deps.ops) is not None
    deps.close()


def test_the_scheduler_has_a_minute_tick_and_a_daily_heartbeat(data_dir: Path) -> None:
    deps = make_deps(*market(date(2026, 11, 10)), now=NOW)
    jobs = {j.id: j for j in build_scheduler(deps).get_jobs()}
    assert jobs[TICK_JOB].trigger.interval == timedelta(minutes=1)  # type: ignore[attr-defined]
    fields = {f.name: str(f) for f in jobs[HEARTBEAT_JOB].trigger.fields}  # type: ignore[attr-defined]
    assert fields["day_of_week"] == "*" and (fields["hour"], fields["minute"]) == ("18", "0")
    deps.close()


def test_a_missed_or_errored_job_notifies(data_dir: Path) -> None:
    got: list[Notice] = []
    deps = make_deps(*market(date(2026, 11, 10)), now=NOW)
    deps.notify = got.append
    listener = missed_job_listener(deps, static_push=lambda *a: None)
    listener(SimpleNamespace(job_id="weekly-advice", exception=None))
    listener(SimpleNamespace(job_id="daily-check", exception=RuntimeError("x")))
    assert [(n.kind, n.status) for n in got] == [("weekly", "missed"), ("daily", "failed")]
    deps.close()


def test_a_ping_follows_a_good_weekly_run_and_every_heartbeat_pass(data_dir: Path) -> None:
    pings: list[str] = []
    deps = make_deps(*market(date(2026, 11, 10)), now=NOW)
    ok = RunResult("ok", "weekly", "2026-11-09")
    guarded("weekly", "weekly", deps, lambda d, n: ok, ping=lambda: pings.append("w"))()
    guarded(
        "weekly", "weekly", deps, lambda d, n: RunResult("stopped", "weekly", "s"),
        ping=lambda: pings.append("bad"),
    )()  # fmt: skip
    guarded("hb", "heartbeat", deps, lambda d, n: None, ping=lambda: pings.append("h"))()
    assert pings == ["w", "h"]
    deps.close()


def test_ping_gets_the_url_ignores_failures_and_never_logs_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    url = "https://hc-ping.example/secret-uuid"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        raise httpx.ConnectError(f"cannot reach {request.url}")

    ping_dead_man(transport=httpx.MockTransport(handler))  # no URL set: no request
    assert seen == []
    monkeypatch.setenv("GUARD_PING_URL", url)
    with caplog.at_level(logging.DEBUG):
        ping_dead_man(transport=httpx.MockTransport(handler))
    assert seen == [url] and "secret-uuid" not in caplog.text


# ---- startup ----------------------------------------------------------------------------------
def test_startup_without_a_channel_logs_error_and_records_it(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    deps = make_deps(*market(date(2026, 11, 10)), now=NOW)
    got: list[Notice] = []
    deps.notify = got.append
    with caplog.at_level(logging.ERROR):
        run_startup(lambda: deps, channels=lambda: [], static_push=lambda *a: None)
    assert "no alert channel" in caplog.text
    conn = open_ops()
    assert run_store.get_control(conn, run_store.ALERTS_CONFIGURED_KEY) == "0"
    assert AlertStore(conn).rows()[0]["error"] == "not configured"
    conn.close()


def test_startup_sends_the_missing_alert_at_once_when_the_week_is_overdue(data_dir: Path) -> None:
    got: list[Notice] = []

    def factory() -> Deps:
        d = make_deps(*market(date(2026, 11, 10)), now=LATE)
        d.notify = got.append
        return d

    first = factory()
    _weekly(first.ops, "2026-11-09")
    first.close()
    run_startup(factory, channels=lambda: [object()], static_push=lambda *a: None)  # type: ignore[list-item]
    assert [(n.kind, n.status, n.slot) for n in got] == [("heartbeat", "missing", "2026-11-16")]


# ---- the status command -----------------------------------------------------------------------
def test_status_check_scheduler_fails_without_a_tick_or_with_a_stale_one(data_dir: Path) -> None:
    cli = Cli()
    assert cli("status", "--check")[0] == 0
    code, text = cli("status", "--check", "--scheduler")
    assert code == 1 and "scheduler" in text
    conn = open_ops()
    run_store.set_scheduler_tick(conn, NOW - timedelta(minutes=11))
    assert cli("status", "--check", "--scheduler")[0] == 1
    run_store.set_scheduler_tick(conn, NOW - timedelta(minutes=2))
    assert cli("status", "--check", "--scheduler")[0] == 0
    conn.close()


def test_status_shows_an_unconfigured_alert_path_prominently(data_dir: Path) -> None:
    conn = open_ops()
    run_store.set_control(conn, run_store.ALERTS_CONFIGURED_KEY, "0", NOW)
    conn.close()
    cli = Cli()
    code, text = cli("status")
    assert code == 0 and text.splitlines()[0].startswith("ALERT PROBLEM: not configured")
    assert cli("status", "--check")[0] == 0  # a fresh install is not unhealthy


def test_status_check_fails_when_the_last_alert_failed_everywhere(data_dir: Path) -> None:
    conn = open_ops()
    run_store.set_control(conn, run_store.ALERTS_CONFIGURED_KEY, "1", NOW)
    AlertStore(conn).record(
        kind="weekly", urgency="info", channel="telegram", ok=False, error="x", now=NOW
    )
    conn.close()
    code, text = Cli()("status", "--check")
    assert code == 1 and PROBLEM_FAILED_EVERYWHERE in text


def test_a_paused_week_is_accounted_for_by_the_check_and_the_heartbeat(data_dir: Path) -> None:
    from guard.notify.heartbeat import heartbeat_status
    from guard.readapi import status_view

    cli = Cli(LATE)
    deps = cli.__class__(LATE)
    conn = open_ops()
    _weekly(conn, "2026-11-09")
    _weekly(conn, "2026-11-16", "paused")
    d = make_deps(deps.prices, deps.macro, now=LATE)
    view = status_view(d.ops, d.config, LATE)
    assert not view.weekly_overdue and view.week_paused
    assert heartbeat_status(d.ops, LATE, d.config).state == "ok"
    assert cli("status", "--check")[0] == 0
    d.close()
    conn.close()


# ---- the warehouse ----------------------------------------------------------------------------
def test_lazy_warehouse_opens_on_first_use_only() -> None:
    opened: list[int] = []

    class Conn:
        def execute(self, q: str) -> str:
            return q

        def close(self) -> None:
            opened.append(-1)

    def opener() -> Conn:
        opened.append(1)
        return Conn()

    w = LazyWarehouse(opener)
    w.close()
    assert opened == []
    assert w.execute("x") == "x" and w.execute("y") == "y"
    w.close()
    assert opened == [1, -1]


def test_status_and_pause_never_open_the_warehouse(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("the warehouse was opened")

    monkeypatch.setattr("guard.run.deps.open_warehouse", forbidden)
    for argv in (["status"], ["status", "--check"], ["pause"], ["resume"], ["test-alert"]):
        main(argv, deps_factory=default_deps, out=io.StringIO())


def test_opening_the_warehouse_retries_with_backoff() -> None:
    import duckdb

    calls: list[int] = []
    sleeps: list[float] = []

    def opener() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise duckdb.IOException("Could not set lock on file")
        return "conn"

    assert open_with_retry(opener, attempts=5, sleep=sleeps.append) == "conn"
    assert sleeps == [0.5, 1.0]
    with pytest.raises(duckdb.IOException):
        open_with_retry(
            lambda: (_ for _ in ()).throw(duckdb.IOException("lock")),
            attempts=2,
            sleep=sleeps.append,
        )
