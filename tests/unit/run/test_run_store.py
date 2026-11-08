from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from guard.ladder import LadderState
from guard.storage import open_ops, run_store

T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn(data_dir: Path) -> sqlite3.Connection:
    return open_ops()


def _record(conn: sqlite3.Connection, status: str = "ok", slot: str = "2026-11-11", **kw: object):
    return run_store.record_run(
        conn,
        kind="weekly",
        slot=slot,
        status=status,  # type: ignore[arg-type]
        started_at=T0,
        finished_at=T0,
        **kw,  # type: ignore[arg-type]
    )


def test_ladder_state_round_trips_and_latest_wins(conn: sqlite3.Connection) -> None:
    assert run_store.latest_ladder_state(conn) is None
    a = LadderState(equity=0.9, step=0)
    b = LadderState(equity=0.75, step=1, streak=2, last_change_week=5, pending_equity=0.6)
    run_store.save_ladder_state(conn, 5, "weekly-a", a, T0)
    run_store.save_ladder_state(conn, 6, "weekly-b", b, T0)
    assert run_store.latest_ladder_state(conn) == (6, b)


def test_paused_defaults_false_and_toggles(conn: sqlite3.Connection) -> None:
    assert run_store.get_paused(conn) is False
    run_store.set_paused(conn, True, T0)
    assert run_store.get_paused(conn) is True
    run_store.set_paused(conn, False, T0)
    assert run_store.get_paused(conn) is False


def test_ok_run_is_idempotent_per_slot(conn: sqlite3.Connection) -> None:
    first = _record(conn, advice="{}", ladder=(1, LadderState(0.9, 0)), payload="p1")
    second = _record(conn, payload="p2")
    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM run WHERE kind='weekly'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM advice").fetchone()[0] == 1


def test_stopped_and_failed_rows_are_replaced_on_retry(conn: sqlite3.Connection) -> None:
    _record(conn, "stopped", stop_reason="stale")
    _record(conn, "failed", stop_reason="boom")
    ok = _record(conn, "ok", payload="x", advice="{}", ladder=(1, LadderState(0.9, 0)))
    assert ok.status == "ok"
    rows = conn.execute("SELECT status FROM run WHERE kind='weekly'").fetchall()
    assert [r["status"] for r in rows] == ["ok"]


def test_ok_run_commits_nothing_when_a_part_fails(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(run_store, "save_advice", broken)
    with pytest.raises(RuntimeError):
        _record(conn, advice="{}", ladder=(3, LadderState(0.5, 3)))
    assert conn.execute("SELECT COUNT(*) FROM run WHERE kind='weekly'").fetchone()[0] == 0
    assert run_store.latest_ladder_state(conn) is None


def test_last_successful_run_ignores_stopped(conn: sqlite3.Connection) -> None:
    assert run_store.last_successful_run(conn, "weekly") is None
    _record(conn, slot="2026-11-04")
    _record(conn, slot="2026-11-11", status="stopped", stop_reason="stale")
    last = run_store.last_successful_run(conn, "weekly")
    assert last is not None and last.slot == "2026-11-04"


def test_a_retry_updates_a_non_ok_row_even_after_a_journal_entry_points_at_it(
    conn: sqlite3.Connection,
) -> None:
    _record(conn, "stopped", stop_reason="stale")
    conn.execute(
        "INSERT INTO decision_journal (run_id, decision, recorded_at)"
        " VALUES ('weekly-2026-11-11', 'ignored', ?)",
        (T0.isoformat(),),
    )
    ok = _record(conn, "ok", payload="x", advice="{}", ladder=(1, LadderState(0.9, 0)))
    assert ok.status == "ok" and ok.payload == "x"
    assert conn.execute("SELECT COUNT(*) FROM run WHERE kind='weekly'").fetchone()[0] == 1


def test_responses_are_append_only_and_supersede(conn: sqlite3.Connection) -> None:
    run = _record(conn, advice="{}")
    first = run_store.record_response(conn, run.run_id, "followed", "did it", T0)
    second = run_store.record_response(conn, run.run_id, "changed", None, T0)
    rows = run_store.responses_for(conn, run.run_id)
    assert [r.decision for r in rows] == ["followed", "changed"]
    assert rows[0].id == first and rows[0].supersedes is None and rows[0].note == "did it"
    assert rows[1].id == second and rows[1].supersedes == first
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE decision_journal SET note='edited' WHERE id=?", (first,))


def test_a_response_is_tied_to_the_hash_of_the_advice_it_answers(conn: sqlite3.Connection) -> None:
    import hashlib

    run = _record(conn, advice='{"status":"advice"}')
    entry = run_store.record_response(conn, run.run_id, "followed", None, T0)
    ref = conn.execute("SELECT advice_ref FROM decision_journal WHERE id=?", (entry,)).fetchone()[0]
    assert ref == hashlib.sha256(b'{"status":"advice"}').hexdigest()
    assert run_store.advice_ref(conn, run.run_id) == ref


@pytest.mark.parametrize("status", ["stopped", "paused", "failed"])
def test_only_an_ok_weekly_run_takes_a_response(conn: sqlite3.Connection, status: str) -> None:
    run = _record(conn, status, stop_reason="x")
    with pytest.raises(run_store.NotRespondable, match="ok weekly"):
        run_store.record_response(conn, run.run_id, "followed", None, T0)


def test_a_daily_run_takes_no_response(conn: sqlite3.Connection) -> None:
    run = run_store.record_run(
        conn, kind="daily", slot="2026-11-10", status="ok", started_at=T0, finished_at=T0
    )
    with pytest.raises(run_store.NotRespondable):
        run_store.record_response(conn, run.run_id, "followed", None, T0)


def test_advice_rows_are_append_only(conn: sqlite3.Connection) -> None:
    run = _record(conn, advice="{}")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE advice SET payload='{\"x\":1}' WHERE run_id=?", (run.run_id,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM advice WHERE run_id=?", (run.run_id,))


def test_an_ok_run_row_cannot_be_edited_or_deleted_but_a_stopped_one_can(
    conn: sqlite3.Connection,
) -> None:
    ok = _record(conn, advice="{}")
    with pytest.raises(sqlite3.DatabaseError, match="ok run"):
        conn.execute("UPDATE run SET payload='tampered' WHERE run_id=?", (ok.run_id,))
    with pytest.raises(sqlite3.DatabaseError, match="ok run"):
        conn.execute("DELETE FROM run WHERE run_id=?", (ok.run_id,))
    stopped = _record(conn, "stopped", slot="2026-11-18", stop_reason="stale")
    conn.execute("UPDATE run SET stop_reason='again' WHERE run_id=?", (stopped.run_id,))


def test_record_run_commits_nothing_when_paused_is_demanded_and_the_switch_is_on(
    conn: sqlite3.Connection,
) -> None:
    run_store.set_paused(conn, True, T0)
    with pytest.raises(run_store.PausedError):
        _record(conn, advice="{}", ladder=(1, LadderState(0.9, 0)), refuse_if_paused=True)
    assert conn.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0
    assert run_store.latest_ladder_state(conn) is None


def test_response_for_unknown_run_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(run_store.UnknownRun):
        run_store.record_response(conn, "weekly-nope", "followed", None, T0)
    with pytest.raises(ValueError):
        run_store.record_response(conn, "weekly-nope", "maybe", None, T0)  # type: ignore[arg-type]
