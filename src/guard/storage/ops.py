"""Operational store (SQLite, WAL): runs, snapshots, ladder state, audit, decision journal."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from guard.paths import subdir
from guard.storage._migrate import migration_files


def ops_path() -> Path:
    return subdir("state") / "ops.sqlite"


def open_ops(path: Path | None = None) -> sqlite3.Connection:
    """Open the ops database, creating it and applying pending migrations."""
    conn = sqlite3.connect(path or ops_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
    applied = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    for f in migration_files("ops"):
        if f.name in applied:
            continue
        conn.executescript("BEGIN;\n" + f.read_text("utf-8") + "\nCOMMIT;")
        conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (f.name,))
    return conn
