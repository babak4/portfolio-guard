from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from guard.storage import open_ops, run_store
from guard.storage import read_store as rs

from .conftest import NOW, RUN_ID, add_run


def test_readonly_connection_cannot_write(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO control (key, value, updated_at) VALUES ('x', '1', 'now')")


def test_open_readonly_missing_file_is_a_clear_error(data_dir) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(FileNotFoundError):
        rs.open_readonly(data_dir / "state" / "nothing.sqlite")


def test_recent_runs_carry_status_kind_slot_and_light_inputs(conn: sqlite3.Connection) -> None:
    rows = rs.recent_runs(conn)
    weekly = [r for r in rows if r.kind == "weekly"]
    assert [r.run_id for r in weekly] == [RUN_ID]
    r = weekly[0]
    assert (r.status, r.slot, r.stop_reason) == ("ok", "2026-11-09", None)
    assert r.ladder_kind == "hold" and r.advice_status == "advice"
    assert r.trades == 0 and r.warn_flags == 1  # the unverified fee figure warns
    assert "payload" not in r.__dict__  # the list stays light


def test_recent_runs_filters_by_kind_and_limit(conn: sqlite3.Connection) -> None:
    add_run("stopped", "2026-11-16", reason="stale")
    assert [r.slot for r in rs.recent_runs(conn, limit=1)] == ["2026-11-16"]
    assert all(r.kind == "snapshot" for r in rs.recent_runs(conn, kind="snapshot"))


def test_run_payload_and_missing(conn: sqlite3.Connection) -> None:
    assert '"kind":"weekly"' in (rs.run_payload(conn, RUN_ID) or "")
    assert rs.run_payload(conn, "weekly-1999-01-04") is None


def test_ladder_history(conn: sqlite3.Connection) -> None:
    rows = rs.ladder_history(conn)
    assert len(rows) == 1 and rows[0].run_id == RUN_ID and rows[0].state["equity"] == 0.9


def test_snapshot_totals_per_account(conn: sqlite3.Connection) -> None:
    rows = rs.snapshot_totals(conn)
    assert {r.account_id for r in rows} == {"isa", "gia"}
    assert all(isinstance(r.total, Decimal) for r in rows)


def test_responses_all(conn: sqlite3.Connection) -> None:
    c = open_ops()
    run_store.record_response(c, RUN_ID, "followed", "done", NOW)
    c.close()
    rows = rs.all_responses(conn)
    assert [(r.run_id, r.decision) for r in rows] == [(RUN_ID, "followed")]


def test_alert_deliveries_tolerates_a_missing_table(data_dir, recorded) -> None:  # type: ignore[no-untyped-def]
    from guard.storage.ops import ops_path

    c = open_ops()
    c.execute("DROP TABLE alert_delivery")  # a database that predates the alert migration
    c.close()
    ro = rs.open_readonly(ops_path())
    assert rs.recent_alert_deliveries(ro) == []
    ro.close()


def test_alert_deliveries_read_newest_first(conn: sqlite3.Connection) -> None:
    c = open_ops()
    for channel in ("telegram", "ntfy"):
        c.execute(
            "INSERT INTO alert_delivery (kind, urgency, channel, ok, created_at)"
            " VALUES ('weekly', 'info', ?, 1, 'now')",
            (channel,),
        )
    c.close()
    assert [r["channel"] for r in rs.recent_alert_deliveries(conn)] == ["ntfy", "telegram"]
