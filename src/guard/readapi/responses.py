"""What the owner recorded against each piece of advice."""

from __future__ import annotations

import sqlite3

from guard.readapi.models import ResponseView
from guard.storage import read_store, run_store


def _slot(run_id: str) -> str:
    return run_id.split("-", 1)[1] if "-" in run_id else run_id


def responses_view(
    conn: sqlite3.Connection, run_id: str | None = None, limit: int = 200
) -> tuple[ResponseView, ...]:
    """Newest first. A response stays listed after a later one supersedes it, marked not current."""
    rows = (
        read_store.all_responses(conn, limit)
        if run_id is None
        else list(reversed(run_store.responses_for(conn, run_id)))
    )
    superseded = {r.supersedes for r in read_store.all_responses(conn, 10_000) if r.supersedes}
    return tuple(
        ResponseView(
            r.id,
            r.run_id,
            _slot(r.run_id),
            r.decision,
            r.note,
            r.recorded_at,
            r.id not in superseded,
        )
        for r in rows
    )
