"""The only writes the dashboard may make: the pause switch and the owner's responses.

Each write takes the same ``run`` lock the weekly and daily jobs hold, waiting for a running job
instead of failing, and turns the store's errors into a message for the owner.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from guard.storage import open_ops, run_store, writer_lock

ConnFactory = Callable[[], sqlite3.Connection]
DEFAULT_WAIT_SECONDS = 30.0
default_conn_factory: ConnFactory = open_ops

__all__ = ["ActionResult", "default_conn_factory", "pause", "respond", "resume"]


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


def _write(
    conn_factory: ConnFactory,
    wait_seconds: float,
    work: Callable[[sqlite3.Connection, datetime], str],
) -> ActionResult:
    with writer_lock("run", wait_seconds=wait_seconds) as acquired:
        if not acquired:
            return ActionResult(
                False, "A job is running right now. Nothing was changed; try again in a minute."
            )
        conn = conn_factory()
        try:
            return ActionResult(True, work(conn, datetime.now(UTC)))
        except run_store.UnknownRun as e:
            return ActionResult(False, f"Run {e} was not found.")
        except run_store.NotRespondable as e:
            return ActionResult(False, f"There is no advice to respond to: {e}")
        except ValueError as e:
            return ActionResult(False, str(e))
        finally:
            conn.close()


def pause(conn_factory: ConnFactory, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> ActionResult:
    def work(conn: sqlite3.Connection, now: datetime) -> str:
        run_store.set_paused(conn, True, now)
        return "Advice is paused. No new advice is produced until you resume."

    return _write(conn_factory, wait_seconds, work)


def resume(conn_factory: ConnFactory, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> ActionResult:
    def work(conn: sqlite3.Connection, now: datetime) -> str:
        run_store.set_paused(conn, False, now)
        return "Advice is on again from the next run."

    return _write(conn_factory, wait_seconds, work)


def respond(
    conn_factory: ConnFactory,
    run_id: str,
    decision: run_store.Decision,
    note: str | None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> ActionResult:
    """Record ``followed``, ``changed`` or ``ignored`` for a run. A later response replaces the
    earlier one as the current answer; the earlier row stays in the journal."""
    text = (note or "").strip() or None

    def work(conn: sqlite3.Connection, now: datetime) -> str:
        run_store.record_response(conn, run_id, decision, text, now)
        return f"Recorded: {decision}."

    return _write(conn_factory, wait_seconds, work)
