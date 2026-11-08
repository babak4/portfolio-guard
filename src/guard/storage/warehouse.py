"""Analytical store (DuckDB): raw and cleaned price history, index series, backtest results."""

from __future__ import annotations

from pathlib import Path

import duckdb

from guard.paths import subdir
from guard.storage._migrate import migration_files
from guard.storage.lock import writer_lock


def warehouse_path() -> Path:
    return subdir("warehouse") / "guard.duckdb"


_MIGRATE_WAIT_SECONDS = 60.0


def _applied(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}


def open_warehouse(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open the warehouse, creating it and applying pending migrations.

    Each migration and its bookkeeping row commit together, under the ``migrate`` lock.
    """
    conn = duckdb.connect(str(path or warehouse_path()))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name VARCHAR PRIMARY KEY)")
        files = migration_files("warehouse")
        if any(f.name not in _applied(conn) for f in files):
            with writer_lock("migrate", wait_seconds=_MIGRATE_WAIT_SECONDS) as acquired:
                if not acquired:
                    raise RuntimeError("another process is applying migrations")
                applied = _applied(conn)
                for f in files:
                    if f.name in applied:
                        continue
                    conn.execute("BEGIN")
                    try:
                        conn.execute(f.read_text("utf-8"))
                        conn.execute("INSERT INTO schema_migrations VALUES (?)", [f.name])
                        conn.execute("COMMIT")
                    except BaseException:
                        conn.execute("ROLLBACK")
                        raise
    except BaseException:
        conn.close()
        raise
    return conn
