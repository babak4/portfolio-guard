"""The dead-man check: has this week's advice run happened?"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from guard.config import GuardConfig
from guard.storage import run_store
from guard.storage.alert_store import AlertStore

if TYPE_CHECKING:
    from guard.run.deps import Deps

KIND = "weekly"
DONE = ("ok", "paused")
HeartbeatState = Literal["ok", "missing", "alerted"]


@dataclass(frozen=True)
class HeartbeatStatus:
    state: HeartbeatState
    slot: str  # the Monday of the current London ISO week


def heartbeat_key(slot: str) -> str:
    return f"heartbeat:{slot}"


def heartbeat_status(conn: sqlite3.Connection, now: datetime, cfg: GuardConfig) -> HeartbeatStatus:
    """``ok`` when the week has an ``ok`` or ``paused`` weekly run, ``alerted`` when it has none
    but the missing-run alert was already delivered, else ``missing``."""
    today = now.astimezone(ZoneInfo(cfg.schedule.timezone)).date()
    slot = (today - timedelta(days=today.weekday())).isoformat()
    run = run_store.get_run_for_slot(conn, KIND, slot)
    if run is not None and run.status in DONE:
        return HeartbeatStatus("ok", slot)
    if AlertStore(conn).once_done(heartbeat_key(slot)):
        return HeartbeatStatus("alerted", slot)
    return HeartbeatStatus("missing", slot)


def heartbeat_job(deps: Deps, now: datetime) -> HeartbeatStatus:
    return heartbeat_status(deps.ops, now, deps.config)
