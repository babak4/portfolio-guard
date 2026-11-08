"""DuckDB access for raw market payloads and cleaned series. Writes are idempotent."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb

Connection = duckdb.DuckDBPyConnection


def record_raw(conn: duckdb.DuckDBPyConnection, source_id: str, name: str, payload: bytes) -> bool:
    """Store a raw payload keyed by its sha256. Returns False when it was already stored."""
    digest = hashlib.sha256(payload).hexdigest()
    exists = conn.execute(
        "SELECT 1 FROM raw_payload WHERE source_id = ? AND sha256 = ?", [source_id, digest]
    ).fetchone()
    if exists:
        return False
    conn.execute(
        "INSERT INTO raw_payload VALUES (?, ?, ?, ?, ?, ?)",
        [source_id, digest, name, len(payload), datetime.now(UTC).replace(tzinfo=None), payload],
    )
    return True


def raw_hashes(conn: duckdb.DuckDBPyConnection, source_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT sha256 FROM raw_payload WHERE source_id = ? ORDER BY sha256", [source_id]
    ).fetchall()
    return [r[0] for r in rows]


def save_clean_series(
    conn: duckdb.DuckDBPyConnection,
    series_id: str,
    rows: Sequence[tuple[date, Decimal, bool]],
    sources: str,
) -> None:
    """Upsert (date, value, filled) rows for a series; re-saving the same rows changes nothing."""
    conn.execute("BEGIN")
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO market_series VALUES (?, ?, ?, ?, ?)",
            [[series_id, d, v, filled, sources] for d, v, filled in rows],
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def load_clean_series(
    conn: duckdb.DuckDBPyConnection, series_id: str
) -> list[tuple[date, Decimal, bool, str]]:
    rows = conn.execute(
        "SELECT obs_date, value, filled, sources FROM market_series "
        "WHERE series_id = ? ORDER BY obs_date",
        [series_id],
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]
