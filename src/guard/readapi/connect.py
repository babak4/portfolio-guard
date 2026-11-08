"""How the dashboard reaches the data: read-only, and absent until the first run."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from guard.storage.read_store import open_readonly


@contextmanager
def reader() -> Iterator[sqlite3.Connection | None]:
    """A read-only connection to the ops database, or ``None`` when it does not exist yet."""
    try:
        conn = open_readonly()
    except FileNotFoundError:
        yield None
        return
    try:
        yield conn
    finally:
        conn.close()
