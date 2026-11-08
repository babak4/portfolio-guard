"""An optional out-of-process dead-man ping (healthchecks.io style).

When ``GUARD_PING_URL`` is set it is fetched after each good weekly run and each heartbeat pass.
If the pings stop, the outside service tells the owner, even when this whole host is down. A
failure to ping never affects a run, and the URL (which is a secret) is never logged.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("guard.notify")

PING_ENV = "GUARD_PING_URL"
TIMEOUT_SECONDS = 5.0


def ping_dead_man(*, transport: httpx.BaseTransport | None = None) -> None:
    """GET the ping URL if one is configured. Never raises."""
    url = os.environ.get(PING_ENV, "").strip()
    if not url:
        return
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            client.get(url).raise_for_status()
    except Exception as e:  # noqa: BLE001 - a ping problem must never break a run
        log.warning("dead-man ping failed: %s", type(e).__name__)
