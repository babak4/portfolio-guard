"""Analytical store (DuckDB): raw and cleaned price history, index series, backtest results."""

from __future__ import annotations

from pathlib import Path

import duckdb

from guard.paths import subdir
from guard.storage._migrate import migration_files


def warehouse_path() -> Path:
    return subdir("warehouse") / "guard.duckdb"


def open_warehouse(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open the warehouse, creating it and applying pending migrations."""
    conn = duckdb.connect(str(path or warehouse_path()))
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name VARCHAR PRIMARY KEY)")
    applied = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    for f in migration_files("warehouse"):
        if f.name in applied:
            continue
        conn.execute("BEGIN")
        conn.execute(f.read_text("utf-8"))
        conn.execute("INSERT INTO schema_migrations VALUES (?)", [f.name])
        conn.execute("COMMIT")
    return conn
