"""SQLite access for weekly and daily runs: run rows, ladder state, advice, the pause switch
and the owner's responses. All SQL for these tables lives here.

Single statements commit on their own. ``record_run`` writes the run row, the new ladder state and
the advice in one transaction, so a run is either fully stored or not stored at all.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from guard.ladder import LadderState
from guard.storage._tx import transaction

RunStatus = Literal["ok", "paused", "stopped", "failed"]
Decision = Literal["followed", "changed", "ignored"]
DECISIONS: tuple[str, ...] = ("followed", "changed", "ignored")
PAUSED_KEY = "paused"


class UnknownRun(LookupError):
    """No run row has this id."""


class NotRespondable(ValueError):
    """Only an ``ok`` weekly run has advice the owner can respond to."""


class PausedError(RuntimeError):
    """The pause switch was on when the run came to commit; nothing was written."""


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    kind: str
    slot: str
    status: str
    stop_reason: str | None
    inputs_hash: str | None
    config_version: str | None
    code_version: str | None
    payload: str | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class Response:
    id: int
    run_id: str
    decision: str
    note: str | None
    recorded_at: str
    supersedes: int | None


def _stamp(when: datetime) -> str:
    return when.isoformat()


def run_id_for(kind: str, slot: str) -> str:
    return f"{kind}-{slot}"


def _record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        kind=row["kind"],
        slot=row["slot"],
        status=row["status"],
        stop_reason=row["stop_reason"],
        inputs_hash=row["inputs_hash"],
        config_version=row["config_version"],
        code_version=row["code_version"],
        payload=row["payload"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def get_run(conn: sqlite3.Connection, run_id: str) -> RunRecord | None:
    row = conn.execute("SELECT * FROM run WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else _record(row)


def get_run_for_slot(conn: sqlite3.Connection, kind: str, slot: str) -> RunRecord | None:
    row = conn.execute("SELECT * FROM run WHERE kind = ? AND slot = ?", (kind, slot)).fetchone()
    return None if row is None else _record(row)


def get_advice(conn: sqlite3.Connection, run_id: str) -> str | None:
    row = conn.execute("SELECT payload FROM advice WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else str(row["payload"])


def advice_hash(payload: str) -> str:
    """SHA-256 (hex) of a stored advice payload: the reference a response is tied to."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def advice_ref(conn: sqlite3.Connection, run_id: str) -> str | None:
    """The hash of the advice stored for ``run_id``, or ``None`` when there is none."""
    payload = get_advice(conn, run_id)
    return None if payload is None else advice_hash(payload)


def latest_ladder_state(conn: sqlite3.Connection) -> tuple[int, LadderState] | None:
    """The most recent saved ``(week, state)``, or ``None`` before the first advised run."""
    row = conn.execute(
        "SELECT week, state_json FROM ladder_state ORDER BY week DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return int(row["week"]), LadderState(**json.loads(row["state_json"]))


def save_ladder_state(
    conn: sqlite3.Connection, week: int, run_id: str, state: LadderState, created_at: datetime
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ladder_state (week, run_id, state_json, created_at)"
        " VALUES (?, ?, ?, ?)",
        (week, run_id, json.dumps(asdict(state), sort_keys=True), _stamp(created_at)),
    )


def get_paused(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM control WHERE key = ?", (PAUSED_KEY,)).fetchone()
    return row is not None and row["value"] == "1"


def set_paused(conn: sqlite3.Connection, paused: bool, now: datetime) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO control (key, value, updated_at) VALUES (?, ?, ?)",
        (PAUSED_KEY, "1" if paused else "0", _stamp(now)),
    )


def save_advice(conn: sqlite3.Connection, run_id: str, payload: str, created_at: datetime) -> None:
    conn.execute(
        "INSERT INTO advice (run_id, payload, created_at) VALUES (?, ?, ?)",
        (run_id, payload, _stamp(created_at)),
    )


def record_run(
    conn: sqlite3.Connection,
    *,
    kind: str,
    slot: str,
    status: RunStatus,
    started_at: datetime,
    finished_at: datetime,
    stop_reason: str | None = None,
    inputs_hash: str | None = None,
    config_version: str | None = None,
    code_version: str | None = None,
    payload: str | None = None,
    advice: str | None = None,
    ladder: tuple[int, LadderState] | None = None,
    refuse_if_paused: bool = False,
) -> RunRecord:
    """Record the run of a ``(kind, slot)``.

    A slot already recorded ``ok`` is left alone and its row is returned. An earlier
    ``paused``, ``stopped`` or ``failed`` row is updated in place (never deleted: a journal entry
    may point at it). ``advice`` and ``ladder`` (week, new state) are stored in the same
    transaction as the run row. With ``refuse_if_paused`` the pause switch is read inside that
    transaction and ``PausedError`` is raised, with nothing written, when it is on.
    """
    run_id = run_id_for(kind, slot)
    with transaction(conn):
        existing = get_run_for_slot(conn, kind, slot)
        if existing is not None and existing.status == "ok":
            return existing
        if refuse_if_paused and get_paused(conn):
            raise PausedError(run_id)
        values = (
            status,
            stop_reason,
            inputs_hash,
            config_version,
            code_version,
            payload,
            _stamp(started_at),
            _stamp(finished_at),
        )
        if existing is not None:
            conn.execute(
                "UPDATE run SET status = ?, stop_reason = ?, inputs_hash = ?, config_version = ?,"
                " code_version = ?, payload = ?, started_at = ?, finished_at = ?"
                " WHERE kind = ? AND slot = ?",
                (*values, kind, slot),
            )
        else:
            conn.execute(
                "INSERT INTO run (status, stop_reason, inputs_hash, config_version, code_version,"
                " payload, started_at, finished_at, run_id, kind, slot)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, run_id, kind, slot),
            )
        if advice is not None:
            save_advice(conn, run_id, advice, finished_at)
        if ladder is not None:
            save_ladder_state(conn, ladder[0], run_id, ladder[1], finished_at)
    stored = get_run(conn, run_id)
    assert stored is not None
    return stored


def last_successful_run(conn: sqlite3.Connection, kind: str) -> RunRecord | None:
    row = conn.execute(
        "SELECT * FROM run WHERE kind = ? AND status = 'ok' ORDER BY slot DESC LIMIT 1", (kind,)
    ).fetchone()
    return None if row is None else _record(row)


def record_response(
    conn: sqlite3.Connection, run_id: str, decision: Decision, note: str | None, now: datetime
) -> int:
    """Append the owner's response to an ``ok`` weekly run, tied to the hash of the advice stored
    for it. A later response supersedes the earlier one; no row changes."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, not {decision!r}")
    with transaction(conn):
        run = get_run(conn, run_id)
        if run is None:
            raise UnknownRun(run_id)
        if run.kind != "weekly" or run.status != "ok":
            raise NotRespondable(
                f"{run_id} is a {run.status} {run.kind} run; only an ok weekly run has advice "
                "to respond to"
            )
        ref = advice_ref(conn, run_id)
        if ref is None:
            raise NotRespondable(f"{run_id} has no stored advice to respond to")
        previous: Any = conn.execute(
            "SELECT MAX(id) FROM decision_journal WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO decision_journal (run_id, advice_ref, decision, note, recorded_at,"
            " supersedes) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, ref, decision, note, _stamp(now), previous),
        )
    assert cur.lastrowid is not None
    return cur.lastrowid


def responses_for(conn: sqlite3.Connection, run_id: str) -> list[Response]:
    rows = conn.execute(
        "SELECT id, run_id, decision, note, recorded_at, supersedes FROM decision_journal"
        " WHERE run_id = ? ORDER BY id",
        (run_id,),
    )
    return [
        Response(r["id"], r["run_id"], r["decision"], r["note"], r["recorded_at"], r["supersedes"])
        for r in rows
    ]
