"""Single-writer discipline across threads and processes.

``writer_lock`` yields ``True`` when acquired and ``False`` when the lock is held. With
``wait_seconds`` it polls, so a short dashboard write retries instead of failing.
"""

from __future__ import annotations

import fcntl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from guard.paths import subdir

__all__ = ["writer_lock"]

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()
_POLL_SECONDS = 0.05


def _thread_lock(name: str) -> threading.Lock:
    with _GUARD:
        return _THREAD_LOCKS.setdefault(name, threading.Lock())


@contextmanager
def writer_lock(name: str = "writer", wait_seconds: float = 0.0) -> Iterator[bool]:
    deadline = time.monotonic() + wait_seconds
    tlock = _thread_lock(name)
    while not tlock.acquire(blocking=False):
        if time.monotonic() >= deadline:
            yield False
            return
        time.sleep(_POLL_SECONDS)
    try:
        path = subdir("state") / f"{name}.lock"
        with path.open("a+") as fh:
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(_POLL_SECONDS)
            try:
                yield True
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        tlock.release()
