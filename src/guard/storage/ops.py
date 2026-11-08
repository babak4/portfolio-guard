"""Operational store (SQLite, WAL): runs, snapshots, ladder state, audit, decision journal."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from guard.paths import subdir
from guard.storage._migrate import migration_files
from guard.storage.lock import writer_lock


def ops_path() -> Path:
    return subdir("state") / "ops.sqlite"


_MIGRATE_WAIT_SECONDS = 60.0


def _applied(conn: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}


def open_ops(path: Path | None = None) -> sqlite3.Connection:
    """Open the ops database, creating it and applying pending migrations.

    Each migration and its bookkeeping row commit together, under the ``migrate`` lock so two
    first opens cannot both apply it.
    """
    conn = sqlite3.connect(path or ops_path(), isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
        files = migration_files("ops")
        if any(f.name not in _applied(conn) for f in files):
            with writer_lock("migrate", wait_seconds=_MIGRATE_WAIT_SECONDS) as acquired:
                if not acquired:
                    raise RuntimeError("another process is applying migrations")
                applied = _applied(conn)
                for f in files:
                    if f.name in applied:
                        continue
                    name = f.name.replace("'", "''")
                    script = (
                        f"BEGIN;\n{f.read_text('utf-8')}\n"
                        f"INSERT INTO schema_migrations (name) VALUES ('{name}');\nCOMMIT;"
                    )
                    try:
                        conn.executescript(script)
                    except BaseException:
                        if conn.in_transaction:
                            conn.execute("ROLLBACK")
                        raise
    except BaseException:
        conn.close()
        raise
    return conn
