from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from guard.config import load_config
from guard.ingest import snapshot_job
from guard.ingest.model import Holding, Snapshot
from guard.ingest.snapshot_job import run_snapshot
from guard.ingest.t212 import T212Client, fetch_history
from guard.storage import open_ops
from guard.storage.ingest_store import (
    raw_history,
    read_snapshot,
    snapshot_days,
    snapshot_gaps,
    store_raw_history,
    write_snapshot,
)

from .conftest import FakeT212, gia_routes, history_routes, isa_routes

Make = Callable[..., tuple[T212Client, FakeT212, list[float]]]


def _snap(day: date, total: str, isin: str = "IE00BK5BQT80") -> Snapshot:
    h = Holding("isa", "trading212", isin, "T", "n", Decimal(1), Decimal(1), Decimal(total), False)
    return Snapshot(
        "isa",
        "trading212",
        day,
        datetime(2026, 11, 9, tzinfo=UTC),
        (h,),
        Decimal(0),
        Decimal(total),
    )


def test_second_snapshot_same_day_replaces_first(data_dir: Path) -> None:
    conn = open_ops()
    day = date(2026, 11, 9)
    write_snapshot(conn, _snap(day, "100"))
    write_snapshot(conn, _snap(day, "250", isin="LU1230136894"))
    snap = read_snapshot(conn, "isa", day)
    assert snap is not None
    assert snap.total == Decimal("250")
    assert [h.isin for h in snap.holdings] == ["LU1230136894"]
    assert snapshot_days(conn, "isa") == [day]


def test_missed_day_is_a_gap_not_backfilled(data_dir: Path) -> None:
    conn = open_ops()
    write_snapshot(conn, _snap(date(2026, 11, 6), "100"))
    write_snapshot(conn, _snap(date(2026, 11, 9), "110"))
    assert snapshot_gaps(conn, "isa") == [date(2026, 11, 7), date(2026, 11, 8)]
    assert snapshot_days(conn, "isa") == [date(2026, 11, 6), date(2026, 11, 9)]


def test_history_pages_stored_and_refetch_adds_no_duplicates(
    data_dir: Path, make_client: Make
) -> None:
    conn = open_ops()
    client, _, _ = make_client(history_routes())
    fetched = datetime(2026, 11, 9, tzinfo=UTC)
    for _ in range(2):
        history = fetch_history(client)
        new = {k: store_raw_history(conn, k, "isa", items, fetched) for k, items in history.items()}
    assert new == {"orders": 0, "dividends": 0, "transactions": 0}
    assert [o["order"]["id"] for o in raw_history(conn, "orders", "isa")] == [1001, 1002, 1003]
    assert len(raw_history(conn, "dividends", "isa")) == 2
    assert len(raw_history(conn, "transactions", "isa")) == 2


def _factory(make_client: Make) -> Callable[[str], T212Client]:
    routes = {"isa": isa_routes() | history_routes(), "gia": gia_routes() | history_routes()}

    def factory(account_id: str) -> T212Client:
        return make_client(routes[account_id])[0]

    return factory


def test_job_writes_once_per_slot(data_dir: Path, make_client: Make) -> None:
    cfg, _ = load_config()
    calls: list[str] = []
    inner = _factory(make_client)

    def factory(account_id: str) -> T212Client:
        calls.append(account_id)
        return inner(account_id)

    now = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)
    assert run_snapshot(now, config=cfg, client_for=factory) == "ok"
    assert run_snapshot(now, config=cfg, client_for=factory) == "skipped"
    assert calls == ["isa", "gia"]
    conn = open_ops()
    assert snapshot_days(conn, "isa") == [date(2026, 11, 9)]
    assert snapshot_days(conn, "gia") == [date(2026, 11, 9)]
    assert len(raw_history(conn, "orders", "gia")) == 3


def test_job_records_missed_day_as_gap(data_dir: Path, make_client: Make) -> None:
    cfg, _ = load_config()
    factory = _factory(make_client)
    run_snapshot(datetime(2026, 11, 6, 22, 30, tzinfo=UTC), config=cfg, client_for=factory)
    run_snapshot(datetime(2026, 11, 9, 22, 30, tzinfo=UTC), config=cfg, client_for=factory)
    assert snapshot_gaps(open_ops(), "isa") == [date(2026, 11, 7), date(2026, 11, 8)]


def test_job_stops_on_integrity_error_and_retries_next_call(
    data_dir: Path, make_client: Make
) -> None:
    cfg, _ = load_config()
    bad = isa_routes() | history_routes()
    bad["/api/v0/equity/account/summary"] = {"currency": "GBP", "totalValue": 1, "cash": {}}
    routes = {"isa": bad, "gia": gia_routes() | history_routes()}
    now = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)
    status = run_snapshot(now, config=cfg, client_for=lambda a: make_client(routes[a])[0])
    assert status == "stopped"
    assert snapshot_days(open_ops(), "isa") == []
    assert run_snapshot(now, config=cfg, client_for=_factory(make_client)) == "ok"


def test_slot_is_the_local_day(data_dir: Path, make_client: Make) -> None:
    cfg, _ = load_config()
    late = datetime(2026, 7, 1, 23, 30, tzinfo=UTC)  # 00:30 on 2 July in London (BST)
    run_snapshot(late, config=cfg, client_for=_factory(make_client))
    assert snapshot_days(open_ops(), "isa") == [date(2026, 7, 2)]


def test_manual_file_is_snapshotted(data_dir: Path, make_client: Make, tmp_path: Path) -> None:
    cfg, _ = load_config()
    f = tmp_path / "manual.yaml"
    f.write_text(
        "- {account_id: investengine-gia, platform: investengine, isin: GB00B3X7QG63,"
        " name: S&P 500, quantity: 2, value_gbp: 300}"
    )
    now = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)
    run_snapshot(now, config=cfg, client_for=_factory(make_client), manual_path=f)
    snap = read_snapshot(open_ops(), "investengine-gia", date(2026, 11, 9))
    assert snap is not None and snap.total == Decimal(300)


def test_scheduler_uses_configured_time_and_zone(data_dir: Path) -> None:
    cfg, _ = load_config()
    trigger = snapshot_job.daily_trigger(cfg.schedule)
    fields = {f.name: str(f) for f in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("22", "30")
    assert str(trigger.timezone) == "Europe/London"
