"""History and audit: every run, one run's stored record, and its replay."""

from __future__ import annotations

import sqlite3

from guard.readapi import _payload as p
from guard.readapi._lights import summary
from guard.readapi.models import HistoryView, LadderEntry, ReplayView, RunDetail
from guard.readapi.responses import responses_view
from guard.run.replay import replay
from guard.storage import read_store, run_store


def history_view(conn: sqlite3.Connection, limit: int = 200) -> HistoryView:
    return HistoryView(
        runs=tuple(summary(r) for r in read_store.recent_runs(conn, limit=limit)),
        ladder=tuple(
            LadderEntry(
                r.week, r.run_id, float(r.state["equity"]), int(r.state["step"]), r.created_at
            )
            for r in read_store.ladder_history(conn, limit)
        ),
    )


def _replay(conn: sqlite3.Connection, run_id: str, current_code_version: str | None) -> ReplayView:
    try:
        result = replay(conn, run_id, current_code_version=current_code_version)
    except Exception as e:  # noqa: BLE001 - an unreplayable record is a result, not a crash
        return ReplayView("unavailable", f"{type(e).__name__}: {e}")
    return ReplayView(result.status, result.message, tuple(sorted(result.diff)))


def run_detail(
    conn: sqlite3.Connection, run_id: str, *, current_code_version: str | None = None
) -> RunDetail | None:
    run = run_store.get_run(conn, run_id)
    if run is None:
        return None
    payload = p.parse(run.payload) if run.kind == "weekly" else p.parse(run.payload)
    replayed = (
        _replay(conn, run_id, current_code_version)
        if run.kind == "weekly" and run.status == "ok"
        else None
    )
    return RunDetail(
        run_id=run.run_id,
        kind=run.kind,
        slot=run.slot,
        status=run.status,
        reason=run.stop_reason,
        config_version=run.config_version,
        code_version=run.code_version,
        inputs_hash=run.inputs_hash,
        advice_hash=run_store.advice_ref(conn, run_id),
        payload=payload,
        payload_json=run.payload,
        replay=replayed,
        responses=responses_view(conn, run_id),
    )
