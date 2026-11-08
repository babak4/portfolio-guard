"""One transaction around a block, for sqlite3 and DuckDB connections alike."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def transaction(conn: Any) -> Iterator[None]:
    conn.execute("BEGIN")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
