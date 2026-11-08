from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from guard.storage import open_ops, open_warehouse, writer_lock


def _insert_run(conn: sqlite3.Connection, run_id: str = "r1") -> None:
    conn.execute(
        "INSERT INTO run (run_id, kind, slot, status, started_at) VALUES (?, 'weekly', ?, 'ok', 't')",
        (run_id, run_id),
    )


def test_journal_rejects_update_and_delete(data_dir: Path) -> None:
    conn = open_ops()
    _insert_run(conn)
    conn.execute(
        "INSERT INTO decision_journal (run_id, decision, recorded_at) VALUES ('r1', 'followed', 't')"
    )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE decision_journal SET decision = 'ignored'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM decision_journal")


def test_journal_rejects_unknown_decision(data_dir: Path) -> None:
    conn = open_ops()
    _insert_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO decision_journal (run_id, decision, recorded_at) VALUES ('r1', 'maybe', 't')"
        )


def test_run_slot_is_unique_per_kind(data_dir: Path) -> None:
    conn = open_ops()
    _insert_run(conn, "a")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO run (run_id, kind, slot, status, started_at) VALUES ('b', 'weekly', 'a', 'ok', 't')"
        )


def test_migrations_apply_once_and_reopen_is_clean(data_dir: Path) -> None:
    from guard.storage._migrate import migration_files

    open_ops().close()
    conn = open_ops()
    names = [r["name"] for r in conn.execute("SELECT name FROM schema_migrations")]
    assert names == [f.name for f in migration_files("ops")]
    wh = open_warehouse()
    expected = [f.name for f in migration_files("warehouse")]
    assert [
        r[0] for r in wh.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
    ] == expected
    wh.close()
    wh = open_warehouse()
    assert wh.execute("SELECT count(*) FROM schema_migrations").fetchone() == (len(expected),)


def test_second_process_cannot_take_the_writer_lock(data_dir: Path) -> None:
    code = textwrap.dedent(
        """
        import sys
        from guard.storage import writer_lock
        with writer_lock("t") as got:
            print("held" if got else "denied", flush=True)
            sys.stdin.readline()
        """
    )
    env = {**__import__("os").environ, "GUARD_DATA_DIR": str(data_dir)}
    holder = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with writer_lock("t") as got:
            assert got is False
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)
    with writer_lock("t") as got:
        assert got is True


def test_waiting_writer_succeeds_once_lock_is_released(data_dir: Path) -> None:
    released = threading.Event()
    acquired = threading.Event()

    def hold() -> None:
        with writer_lock("w") as got:
            assert got
            acquired.set()
            released.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    assert acquired.wait(5)
    threading.Timer(0.2, released.set).start()
    with writer_lock("w", wait_seconds=3) as got:
        assert got is True
    t.join()


def _fake_migration(tmp_path: Path, name: str, sql: str) -> Path:
    d = tmp_path / "migs"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(sql)
    return f


def test_transaction_rolls_back_on_exception() -> None:
    from guard.storage._tx import transaction

    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE t (x)")
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO t VALUES (1)")
        raise RuntimeError("boom")
    assert conn.execute("SELECT count(*) FROM t").fetchone() == (0,)
    with transaction(conn):
        conn.execute("INSERT INTO t VALUES (2)")
    assert conn.execute("SELECT count(*) FROM t").fetchone() == (1,)


def test_failing_ops_migration_leaves_nothing_and_is_retried(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from guard.storage import ops

    bad = _fake_migration(
        tmp_path, "9001_x.sql", "CREATE TABLE half (x INTEGER);\nINSERT INTO missing VALUES (1);"
    )
    monkeypatch.setattr(ops, "migration_files", lambda kind: [bad])
    with pytest.raises(sqlite3.Error):
        open_ops()
    raw = sqlite3.connect(ops.ops_path())
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='half'").fetchall() == []
    assert raw.execute("SELECT count(*) FROM schema_migrations").fetchone() == (0,)
    raw.close()
    bad.write_text("CREATE TABLE half (x INTEGER);")
    conn = open_ops()
    assert conn.execute("SELECT name FROM schema_migrations").fetchall()[0]["name"] == "9001_x.sql"
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='half'").fetchall()


def test_failing_ops_migration_closes_its_connection(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from guard.storage import ops

    bad = _fake_migration(tmp_path, "9001_x.sql", "SELECT * FROM missing;")
    monkeypatch.setattr(ops, "migration_files", lambda kind: [bad])
    opened: list[sqlite3.Connection] = []
    real = sqlite3.connect

    def spy(*a: object, **k: object) -> sqlite3.Connection:
        c = real(*a, **k)  # type: ignore[call-overload]
        opened.append(c)
        return c

    monkeypatch.setattr(ops.sqlite3, "connect", spy)
    with pytest.raises(sqlite3.Error):
        open_ops()
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_failing_warehouse_migration_leaves_nothing_and_is_retried(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    from guard.storage import warehouse

    bad = _fake_migration(
        tmp_path, "9001_x.sql", "CREATE TABLE half (x INTEGER);\nINSERT INTO missing VALUES (1);"
    )
    monkeypatch.setattr(warehouse, "migration_files", lambda kind: [bad])
    with pytest.raises(duckdb.Error):
        open_warehouse()
    probe = duckdb.connect(str(warehouse.warehouse_path()))
    assert probe.execute("SELECT count(*) FROM schema_migrations").fetchone() == (0,)
    assert probe.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name='half'"
    ).fetchone() == (0,)
    probe.close()
    bad.write_text("CREATE TABLE half (x INTEGER);")
    wh = open_warehouse()
    assert wh.execute("SELECT name FROM schema_migrations").fetchall() == [("9001_x.sql",)]


@pytest.mark.parametrize("kind", ["ops", "warehouse"])
def test_applying_migrations_takes_the_migrate_lock(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from contextlib import contextmanager

    import guard.storage.lock as lock_mod
    from guard.storage import ops, warehouse

    module = ops if kind == "ops" else warehouse
    names: list[str] = []

    @contextmanager
    def spy(name: str = "writer", wait_seconds: float = 0.0):  # type: ignore[no-untyped-def]
        names.append(name)
        with lock_mod.writer_lock(name, wait_seconds) as got:
            yield got

    monkeypatch.setattr(module, "writer_lock", spy)
    (open_ops if kind == "ops" else open_warehouse)()
    assert names == ["migrate"]
