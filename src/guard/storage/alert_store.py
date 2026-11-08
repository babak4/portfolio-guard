"""SQLite access for alert delivery attempts and once-only alert markers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any


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
