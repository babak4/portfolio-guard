"""SQLite access for portfolio snapshots, snapshot gaps, raw history and snapshot run records."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from guard.ingest.model import Holding, Snapshot
from guard.storage._tx import transaction

RunStatus = Literal["ok", "stopped", "failed"]


def write_snapshot(conn: sqlite3.Connection, snap: Snapshot) -> None:
    """Store a snapshot, replacing any earlier one for the same account and day.

    Days between the previous snapshot and this one that have no snapshot are recorded as gaps.
    """
    day = snap.day.isoformat()
    prior = conn.execute(
        "SELECT MAX(day) FROM snapshot WHERE account_id = ? AND day < ?", (snap.account_id, day)
    ).fetchone()[0]
    with transaction(conn):
        conn.execute(
            "DELETE FROM snapshot WHERE account_id = ? AND day = ?", (snap.account_id, day)
        )
        conn.execute(
            "INSERT INTO snapshot (account_id, day, platform, taken_at, cash, total)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                snap.account_id,
                day,
                snap.platform,
                snap.taken_at.isoformat(),
                str(snap.cash),
                str(snap.total),
            ),
        )
        conn.executemany(
            "INSERT INTO snapshot_holding (account_id, day, isin, platform, ticker, name,"
            " quantity, price, value_gbp, excluded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    h.account_id,
                    day,
                    h.isin,
                    h.platform,
                    h.ticker,
                    h.name,
                    str(h.quantity),
                    str(h.price),
                    str(h.value_gbp),
                    int(h.excluded),
                )
                for h in snap.holdings
            ],
        )
        if prior is not None:
            first_missing = date.fromisoformat(prior) + timedelta(days=1)
            conn.executemany(
                "INSERT OR IGNORE INTO snapshot_gap (account_id, day) VALUES (?, ?)",
                [
                    (snap.account_id, (first_missing + timedelta(days=n)).isoformat())
                    for n in range((snap.day - first_missing).days)
                ],
            )


def read_snapshot(conn: sqlite3.Connection, account_id: str, day: date) -> Snapshot | None:
    row = conn.execute(
        "SELECT platform, taken_at, cash, total FROM snapshot WHERE account_id = ? AND day = ?",
        (account_id, day.isoformat()),
    ).fetchone()
    if row is None:
        return None
    holdings = tuple(
        Holding(
            account_id=account_id,
            platform=h["platform"],
            isin=h["isin"],
            ticker=h["ticker"],
            name=h["name"],
            quantity=Decimal(h["quantity"]),
            price=Decimal(h["price"]),
            value_gbp=Decimal(h["value_gbp"]),
            excluded=bool(h["excluded"]),
        )
        for h in conn.execute(
            "SELECT * FROM snapshot_holding WHERE account_id = ? AND day = ? ORDER BY isin",
            (account_id, day.isoformat()),
        )
    )
    return Snapshot(
        account_id=account_id,
        platform=row["platform"],
        day=day,
        taken_at=datetime.fromisoformat(row["taken_at"]),
        holdings=holdings,
        cash=Decimal(row["cash"]),
        total=Decimal(row["total"]),
    )


def snapshot_days(conn: sqlite3.Connection, account_id: str) -> list[date]:
    rows = conn.execute("SELECT day FROM snapshot WHERE account_id = ? ORDER BY day", (account_id,))
    return [date.fromisoformat(r["day"]) for r in rows]


def snapshot_gaps(conn: sqlite3.Connection, account_id: str) -> list[date]:
    rows = conn.execute(
        "SELECT day FROM snapshot_gap WHERE account_id = ? ORDER BY day", (account_id,)
    )
    return [date.fromisoformat(r["day"]) for r in rows]


def store_raw_history(
    conn: sqlite3.Connection,
    kind: str,
    account_id: str,
    items: Iterable[tuple[str, dict[str, Any]]],
    fetched_at: datetime,
) -> int:
    """Store ``(external_id, item)`` pairs once each; returns how many were new."""
    before = conn.total_changes
    with transaction(conn):
        conn.executemany(
            "INSERT OR IGNORE INTO raw_history (kind, account_id, external_id, payload, fetched_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (
                    kind,
                    account_id,
                    ext,
                    json.dumps(item, default=str, sort_keys=True),
                    fetched_at.isoformat(),
                )
                for ext, item in items
            ],
        )
    return conn.total_changes - before


def raw_history(conn: sqlite3.Connection, kind: str, account_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT payload FROM raw_history WHERE kind = ? AND account_id = ? ORDER BY external_id",
        (kind, account_id),
    )
    return [json.loads(r["payload"]) for r in rows]


def snapshot_run_done(conn: sqlite3.Connection, slot: str) -> bool:
    row = conn.execute(
        "SELECT status FROM run WHERE kind = 'snapshot' AND slot = ?", (slot,)
    ).fetchone()
    return row is not None and row["status"] == "ok"


def record_snapshot_run(
    conn: sqlite3.Connection,
    slot: str,
    status: RunStatus,
    stop_reason: str | None,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO run (run_id, kind, slot, status, stop_reason, started_at,"
        " finished_at) VALUES (?, 'snapshot', ?, ?, ?, ?, ?)",
        (
            f"snapshot-{slot}",
            slot,
            status,
            stop_reason,
            started_at.isoformat(),
            finished_at.isoformat(),
        ),
    )
