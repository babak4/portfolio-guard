"""The dead-man check: has the expected week's advice run happened?

It runs every day. The slot it judges is the one the dashboard and the healthcheck judge: the
latest week whose run is already due (``expected_weekly_slot``), and "accounted for" means the
same thing everywhere (``run_store.weekly_slot_state``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from guard.config import GuardConfig
from guard.readapi.status import expected_weekly_slot
from guard.storage import run_store, writer_lock
from guard.storage.alert_store import AlertStore

if TYPE_CHECKING:
    from guard.run.deps import Deps

HeartbeatState = Literal["ok", "missing", "alerted", "running"]


@dataclass(frozen=True)
class HeartbeatStatus:
    state: HeartbeatState
    slot: str  # the Monday of the current London ISO week


def heartbeat_key(slot: str) -> str:
    return f"heartbeat:{slot}"


def heartbeat_status(conn: sqlite3.Connection, now: datetime, cfg: GuardConfig) -> HeartbeatStatus:
    """``ok`` when the expected week is accounted for (an ``ok`` or ``paused`` weekly run),
    ``alerted`` when it is not but the missing-run alert was already delivered, else ``missing``."""
    slot = expected_weekly_slot(cfg, now).isoformat()
    if run_store.weekly_slot_state(conn, slot) != "missing":
        return HeartbeatStatus("ok", slot)
    if AlertStore(conn).once_done(heartbeat_key(slot)):
        return HeartbeatStatus("alerted", slot)
    return HeartbeatStatus("missing", slot)


def heartbeat_job(deps: Deps, now: datetime) -> HeartbeatStatus:
    """The check, skipped (state ``running``, no alert) while a weekly run holds the run lock:
    a late run must not be reported missing. The next daily pass looks again."""
    with writer_lock("run") as free:
        if not free:
            slot = expected_weekly_slot(deps.config, now).isoformat()
            return HeartbeatStatus("running", slot)
        return heartbeat_status(deps.ops, now, deps.config)
