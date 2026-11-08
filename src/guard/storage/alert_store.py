"""SQLite access for alert delivery attempts and once-only alert markers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from guard.storage.run_store import ALERTS_CONFIGURED_KEY

PROBLEM_NOT_CONFIGURED = "not configured"
PROBLEM_FAILED_EVERYWHERE = "the last alert failed on every channel"


@dataclass(frozen=True)
class LastDelivery:
    channel: str
    ok: bool
    kind: str
    created_at: str


class AlertStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(
        self,
        *,
        kind: str,
        urgency: str,
        channel: str,
        ok: bool,
        error: str | None,
        now: datetime,
    ) -> None:
        self._conn.execute(
            "INSERT INTO alert_delivery (kind, urgency, channel, ok, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (kind, urgency, channel, int(ok), error, now.isoformat()),
        )

    def rows(self) -> list[dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM alert_delivery ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    def last_per_channel(self) -> list[LastDelivery]:
        cur = self._conn.execute(
            "SELECT channel, ok, kind, created_at FROM alert_delivery d"
            " WHERE id = (SELECT MAX(id) FROM alert_delivery WHERE channel = d.channel)"
            " AND channel != 'none' ORDER BY channel"
        )
        return [LastDelivery(r["channel"], bool(r["ok"]), r["kind"], r["created_at"]) for r in cur]

    def once_done(self, key: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM alert_once WHERE key = ?", (key,)).fetchone()
        return row is not None

    def mark_once(self, key: str, now: datetime) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO alert_once (key, created_at) VALUES (?, ?)",
            (key, now.isoformat()),
        )

    def latest(self) -> LastDelivery | None:
        """The most recent delivery attempt on any channel (channel ``none`` if unconfigured)."""
        row = self._conn.execute(
            "SELECT channel, ok, kind, created_at FROM alert_delivery ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return LastDelivery(row["channel"], bool(row["ok"]), row["kind"], row["created_at"])


def alert_problem(conn: sqlite3.Connection) -> str | None:
    """Why the owner might not be hearing from the system, or ``None`` when alerts work.

    ``not configured``: the worker found no channel at its last start. ``the last alert failed on
    every channel``: delivery tries every channel before giving up, so a failed latest attempt
    means all of them failed. Reads only, so a read-only connection is fine."""
    row = conn.execute(
        "SELECT value FROM control WHERE key = ?", (ALERTS_CONFIGURED_KEY,)
    ).fetchone()
    configured = None if row is None else row["value"] == "1"
    if configured is False:
        return PROBLEM_NOT_CONFIGURED
    last = AlertStore(conn).latest()
    if last is None or last.ok:
        return None
    if last.channel == "none":
        return None if configured else PROBLEM_NOT_CONFIGURED
    return PROBLEM_FAILED_EVERYWHERE
