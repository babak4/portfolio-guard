"""The advice page: one weekly run laid out for reading."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from guard.readapi import _payload as p
from guard.readapi._lights import light_for
from guard.readapi.models import AdviceView
from guard.storage import read_store, run_store


def advice_view(conn: sqlite3.Connection, run_id: str | None = None) -> AdviceView | None:
    """``run_id=None`` means the latest weekly run, whatever its status. While advice is paused
    the latest run's advice is withheld (as the command line does); a run asked for by id is
    always shown."""
    paused = run_store.get_paused(conn)
    run = (
        read_store.latest_run(conn, "weekly")
        if run_id is None
        else read_store.get_run(conn, run_id)
    )
    if run is None:
        return None
    row = next(
        (r for r in read_store.recent_runs(conn, kind=run.kind) if r.run_id == run.run_id), None
    )
    light = None if row is None else light_for(row)
    payload = p.parse(run.payload)
    withheld = run_id is None and paused
    if run.status != "ok" or payload is None or withheld:
        return AdviceView(run.run_id, run.slot, run.status, run.stop_reason, paused, False, light)
    cfg = p.archived_config(payload)
    advice = payload["advice"]
    funds = {f["symbol"]: bool(f.get("verified")) for f in payload["inputs"].get("funds", [])}
    held = {r["symbol"] for r in advice["allocation"]}
    return AdviceView(
        run_id=run.run_id,
        slot=run.slot,
        run_status=run.status,
        stop_reason=None,
        paused=paused,
        has_advice=True,
        light=light,
        advice_status=advice["status"],
        reasons=tuple(advice["reasons"]),
        trades=p.trades(payload),
        contributions=p.contributions(payload),
        allocation=p.allocation(payload),
        sleeve=p.sleeve(payload),
        flags=p.flags(payload),
        guardrails=p.guardrails(payload),
        decision=p.decision(payload),
        ladder_state=p.ladder_state(payload),
        signals=p.signals(payload, cfg),
        last_close_date=p.last_close(payload),
        advice_hash=run_store.advice_ref(conn, run.run_id),
        unverified_symbols=tuple(sorted(s for s in held if funds.get(s) is False)),
        portfolio_value=Decimal(advice["portfolio_value"]),
    )
