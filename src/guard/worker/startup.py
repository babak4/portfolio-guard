"""What the worker does before it starts waiting: check alerts can reach the owner, then look for
a week that is already overdue."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import closing

from guard.notify.deliver import NOT_CONFIGURED, Channel, channels_from_secrets
from guard.notify.heartbeat import heartbeat_job
from guard.run.deps import Deps
from guard.storage import run_store
from guard.storage.alert_store import AlertStore
from guard.worker.schedule import StaticPush, _static_push, guarded, tick_job

log = logging.getLogger("guard.worker")


def run_startup(
    deps_factory: Callable[[], Deps],
    *,
    channels: Callable[[], Sequence[Channel]] = channels_from_secrets,
    static_push: StaticPush = _static_push,
) -> None:
    """Record whether any alert channel exists (an ERROR in the log when none does, plus a
    delivery row and a control flag the dashboard and ``status`` show), write the first scheduler
    tick, and send the missing-week alert at once when the expected week has no run."""
    try:
        configured = bool(channels())
    except Exception as e:  # noqa: BLE001
        log.error("could not read the alert settings: %s", type(e).__name__)
        configured = False
    if not configured:
        log.error(
            "no alert channel is configured: nothing can reach your phone. "
            "See the Alerts section of the README."
        )
    try:
        with closing(deps_factory()) as deps:
            now = deps.clock()
            flag = "1" if configured else "0"
            run_store.set_control(deps.ops, run_store.ALERTS_CONFIGURED_KEY, flag, now)
            if not configured:
                AlertStore(deps.ops).record(
                    kind="startup",
                    urgency="urgent",
                    channel="none",
                    ok=False,
                    error=NOT_CONFIGURED,
                    now=now,
                )
    except Exception as e:  # noqa: BLE001 - the worker still starts
        log.error("could not record the alert settings: %s", type(e).__name__)
    tick_job(deps_factory)()
    guarded("heartbeat", "heartbeat", deps_factory, heartbeat_job, static_push=static_push)()
