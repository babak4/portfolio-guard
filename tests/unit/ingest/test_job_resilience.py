from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from guard.config import load_config
from guard.ingest import snapshot_job
from guard.ingest.snapshot_job import build_scheduler, run_snapshot
from guard.ingest.t212 import T212Client
from guard.storage import open_ops, writer_lock
from guard.storage.ingest_store import snapshot_days

from .conftest import FakeT212, gia_routes, history_routes, isa_routes

Make = Callable[..., tuple[T212Client, FakeT212, list[float]]]
NOW = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)


def _factory(make_client: Make, broken: str | None = None) -> Callable[[str], T212Client]:
    routes = {"isa": isa_routes() | history_routes(), "gia": gia_routes() | history_routes()}
    if broken:
        routes[broken] = {}  # any request raises KeyError in the fake

    return lambda account_id: make_client(routes[account_id])[0]


def _run_row(slot: str = "2026-11-09") -> tuple[str, str | None] | None:
    row = (
        open_ops()
        .execute("SELECT status, stop_reason FROM run WHERE kind='snapshot' AND slot=?", (slot,))
        .fetchone()
    )
    return None if row is None else (row["status"], row["stop_reason"])


def test_write_failure_records_failed_and_retry_succeeds(
    data_dir: Path, make_client: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = load_config()
    real = snapshot_job.write_snapshot
    state = {"fail": True}

    def flaky(conn: object, snap: object) -> None:
        if state["fail"]:
            raise RuntimeError("disk full")
        real(conn, snap)  # type: ignore[arg-type]

    monkeypatch.setattr(snapshot_job, "write_snapshot", flaky)
    with pytest.raises(RuntimeError, match="disk full"):
        run_snapshot(NOW, config=cfg, client_for=_factory(make_client))
    assert _run_row() == ("failed", "RuntimeError")
    state["fail"] = False
    assert run_snapshot(NOW, config=cfg, client_for=_factory(make_client)) == "ok"
    assert _run_row() == ("ok", None)
    assert snapshot_days(open_ops(), "isa") == [date(2026, 11, 9)]


def test_second_account_failing_writes_nothing(data_dir: Path, make_client: Make) -> None:
    cfg, _ = load_config()
    with pytest.raises(KeyError):
        run_snapshot(NOW, config=cfg, client_for=_factory(make_client, broken="gia"))
    conn = open_ops()
    assert snapshot_days(conn, "isa") == []
    assert snapshot_days(conn, "gia") == []
    assert _run_row() == ("failed", "KeyError")


def test_stopped_run_row_has_status_and_reason(data_dir: Path, make_client: Make) -> None:
    cfg, _ = load_config()
    routes = {"isa": isa_routes() | history_routes(), "gia": gia_routes() | history_routes()}
    routes["isa"]["/api/v0/equity/account/summary"] = {"currency": "GBP", "totalValue": 1}
    status = run_snapshot(NOW, config=cfg, client_for=lambda a: make_client(routes[a])[0])
    assert status == "stopped"
    row = _run_row()
    assert row is not None and row[0] == "stopped" and "differ" in (row[1] or "")


def test_locked_when_another_holder_keeps_the_lock(
    data_dir: Path, make_client: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = load_config()
    monkeypatch.setattr(snapshot_job, "_LOCK_WAIT_SECONDS", 0.1)
    built: list[str] = []

    def factory(account_id: str) -> T212Client:
        built.append(account_id)
        return _factory(make_client)(account_id)

    with writer_lock() as got:
        assert got
        assert run_snapshot(NOW, config=cfg, client_for=factory) == "locked"
    assert built == []
    assert _run_row() is None
    assert snapshot_days(open_ops(), "isa") == []


def test_run_waits_briefly_for_a_busy_writer(
    data_dir: Path, make_client: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = load_config()
    monkeypatch.setattr(snapshot_job, "_LOCK_WAIT_SECONDS", 5.0)
    acquired, release = threading.Event(), threading.Event()

    def hold() -> None:
        with writer_lock():
            acquired.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    assert acquired.wait(5)
    threading.Timer(0.2, release.set).start()
    assert run_snapshot(NOW, config=cfg, client_for=_factory(make_client)) == "ok"
    t.join()


def test_scheduled_job_tolerates_a_late_start(data_dir: Path) -> None:
    cfg, _ = load_config()
    scheduler = build_scheduler(lambda now: None, cfg.schedule)
    job = scheduler.get_job("daily-snapshot")
    assert job is not None
    assert job.coalesce is True
    assert job.max_instances == 1
    assert job.misfire_grace_time == 90 * 60  # 22:30 to the end of the local day


def test_instruments_are_fetched_once_for_all_accounts(make_client, data_dir) -> None:
    cfg = load_config()[0]
    from datetime import UTC, datetime

    from guard.ingest.snapshot_job import _collect

    requested: list[str] = []
    clients = {}
    for account_id, routes in (("isa", isa_routes()), ("gia", gia_routes())):
        routes.update(history_routes())
        client, fake, _ = make_client(routes)
        clients[account_id] = (client, fake)

    def client_for(account_id: str):
        return clients[account_id][0]

    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    _collect(now, now.date(), cfg, client_for, None)
    for _client, fake in clients.values():
        requested.extend(
            str(r.url) for r in fake.requests if r.url.path.endswith("/equity/metadata/instruments")
        )
    assert len(requested) == 1
