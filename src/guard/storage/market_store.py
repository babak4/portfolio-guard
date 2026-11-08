"""DuckDB access for raw market payloads and cleaned series. Writes are idempotent."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb

from guard.storage._tx import transaction

Connection = duckdb.DuckDBPyConnection


def record_raw(conn: Connection, source_id: str, name: str, payload: bytes) -> bool:
    """Store a raw payload keyed by its sha256. Returns False when it was already stored."""
    digest = hashlib.sha256(payload).hexdigest()
    inserted = conn.execute(
        "INSERT INTO raw_payload VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING RETURNING sha256",
        [source_id, digest, name, len(payload), datetime.now(UTC).replace(tzinfo=None), payload],
    ).fetchone()
    return inserted is not None


def raw_hashes(conn: Connection, source_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT sha256 FROM raw_payload WHERE source_id = ? ORDER BY sha256", [source_id]
    ).fetchall()
    return [r[0] for r in rows]


def save_clean_series(
    conn: Connection,
    series_id: str,
    rows: Sequence[tuple[date, Decimal, bool]],
    sources: str,
) -> None:
    """Upsert (date, value, filled) rows for a series; re-saving the same rows changes nothing."""
    with transaction(conn):
        conn.executemany(
            "INSERT OR REPLACE INTO market_series VALUES (?, ?, ?, ?, ?)",
            [[series_id, d, v, filled, sources] for d, v, filled in rows],
        )


def load_clean_series(conn: Connection, series_id: str) -> list[tuple[date, Decimal, bool, str]]:
    rows = conn.execute(
        "SELECT obs_date, value, filled, sources FROM market_series "
        "WHERE series_id = ? ORDER BY obs_date",
        [series_id],
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]
