"""Read-only SQL for the dashboard. Nothing here writes.

``open_readonly`` opens the ops database with ``mode=ro`` and ``query_only``, so the dashboard
process cannot change it even by mistake. Run lists carry the few fields the status light needs,
pulled out of the stored payload by SQLite's JSON functions, so a list never loads whole payloads.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from guard.storage.run_store import Response, RunRecord, _record
from guard.storage.run_store import get_run as get_run

__all__ = [
    "LadderRow",
    "RunRow",
    "SnapshotTotal",
    "all_responses",
    "get_run",
    "ladder_history",
    "latest_run",
    "open_readonly",
    "recent_alert_deliveries",
    "recent_runs",
    "run_payload",
    "snapshot_totals",
]


def open_readonly(path: Path | None = None) -> sqlite3.Connection:
    """The ops database, read-only. Raises ``FileNotFoundError`` when it does not exist yet."""
    if path is None:
        from guard.storage.ops import ops_path

        path = ops_path()
    if not path.exists():
        raise FileNotFoundError(str(path))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@dataclass(frozen=True)
class RunRow:
    """A run without its payload. The last six fields are what the status light is judged on;
    they are zero or ``None`` for runs that stored no payload."""

    run_id: str
    kind: str
    slot: str
    status: str
    stop_reason: str | None
    config_version: str | None
    code_version: str | None
    started_at: str
    finished_at: str | None
    ladder_kind: str | None
    advice_status: str | None
    trades: int
    warn_flags: int
    event_count: int
    fast_path: bool


_RUN_ROWS = """
SELECT run_id, kind, slot, status, stop_reason, config_version, code_version, started_at,
       finished_at,
       json_extract(payload, '$.decision.kind') AS ladder_kind,
       json_extract(payload, '$.advice.status') AS advice_status,
       COALESCE(json_array_length(payload, '$.advice.trades'), 0) AS trades,
       (SELECT COUNT(*) FROM json_each(payload, '$.advice.flags')
         WHERE json_extract(value, '$.severity') = 'warn') AS warn_flags,
       COALESCE(json_array_length(payload, '$.events'), 0) AS event_count,
       EXISTS (SELECT 1 FROM json_each(payload, '$.events')
                WHERE json_extract(value, '$.fast_path') = 1) AS fast_path
FROM run
"""


def recent_runs(
    conn: sqlite3.Connection, *, limit: int = 200, kind: str | None = None
) -> list[RunRow]:
    """Newest slot first; ties (same slot, different kinds) by kind."""
    where, params = ("WHERE kind = ?", (kind, limit)) if kind else ("", (limit,))
    rows = conn.execute(f"{_RUN_ROWS} {where} ORDER BY slot DESC, kind LIMIT ?", params)
    return [
        RunRow(
            r["run_id"],
            r["kind"],
            r["slot"],
            r["status"],
            r["stop_reason"],
            r["config_version"],
            r["code_version"],
            r["started_at"],
            r["finished_at"],
            r["ladder_kind"],
            r["advice_status"],
            int(r["trades"]),
            int(r["warn_flags"]),
            int(r["event_count"]),
            bool(r["fast_path"]),
        )
        for r in rows
    ]


def latest_run(conn: sqlite3.Connection, kind: str) -> RunRecord | None:
    """The newest run of a kind whatever its status, with its payload."""
    row = conn.execute(
        "SELECT * FROM run WHERE kind = ? ORDER BY slot DESC LIMIT 1", (kind,)
    ).fetchone()
    return None if row is None else _record(row)


def run_payload(conn: sqlite3.Connection, run_id: str) -> str | None:
    row = conn.execute("SELECT payload FROM run WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None or row["payload"] is None else str(row["payload"])


@dataclass(frozen=True)
class LadderRow:
    week: int
    run_id: str | None
    state: dict[str, Any]
    created_at: str


def ladder_history(conn: sqlite3.Connection, limit: int = 200) -> list[LadderRow]:
    rows = conn.execute(
        "SELECT week, run_id, state_json, created_at FROM ladder_state ORDER BY week DESC LIMIT ?",
        (limit,),
    )
    return [
        LadderRow(r["week"], r["run_id"], json.loads(r["state_json"]), r["created_at"])
        for r in rows
    ]


def all_responses(conn: sqlite3.Connection, limit: int = 200) -> list[Response]:
    """Every response, newest first. A response is superseded when a later row names it."""
    rows = conn.execute(
        "SELECT id, run_id, decision, note, recorded_at, supersedes FROM decision_journal"
        " ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [
        Response(r["id"], r["run_id"], r["decision"], r["note"], r["recorded_at"], r["supersedes"])
        for r in rows
    ]


@dataclass(frozen=True)
class SnapshotTotal:
    account_id: str
    day: str
    total: Decimal
    cash: Decimal


def snapshot_totals(conn: sqlite3.Connection) -> list[SnapshotTotal]:
    rows = conn.execute(
        "SELECT account_id, day, total, cash FROM snapshot ORDER BY day, account_id"
    )
    return [
        SnapshotTotal(r["account_id"], r["day"], Decimal(r["total"]), Decimal(r["cash"]))
        for r in rows
    ]


def recent_alert_deliveries(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """The newest alert delivery rows, as plain dicts; empty when the table does not exist yet."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alert_delivery'"
    ).fetchone()
    if exists is None:
        return []
    rows = conn.execute("SELECT * FROM alert_delivery ORDER BY rowid DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
