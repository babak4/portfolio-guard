from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from guard.config import load_config
from guard.config.models import GuardConfig
from guard.run.weekly import run_weekly
from guard.storage import open_ops, run_store
from tests.unit.run.fakes import make_deps, market

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)  # Wednesday noon, the weekly run time
RUN_ID = "weekly-2026-11-09"


@pytest.fixture
def cfg(data_dir: Path) -> GuardConfig:
    return load_config()[0]


@pytest.fixture
def recorded(data_dir: Path) -> Iterator[None]:
    """A data directory holding one ok weekly run, with its snapshots."""
    prices, macro = market(date(2026, 11, 10))
    deps = make_deps(prices, macro)
    assert run_weekly(deps, NOW).status == "ok"
    deps.close()
    yield


@pytest.fixture
def conn(recorded: None) -> Iterator[sqlite3.Connection]:
    from guard.storage.ops import ops_path
    from guard.storage.read_store import open_readonly

    c = open_readonly(ops_path())
    yield c
    c.close()


@pytest.fixture
def empty_conn(data_dir: Path) -> Iterator[sqlite3.Connection]:
    from guard.storage.ops import ops_path
    from guard.storage.read_store import open_readonly

    open_ops().close()
    c = open_readonly(ops_path())
    yield c
    c.close()


def add_run(
    status: str,
    slot: str,
    kind: str = "weekly",
    reason: str | None = None,
    payload: str | None = None,
) -> None:
    c = open_ops()
    try:
        run_store.record_run(
            c,
            kind=kind,
            slot=slot,
            status=status,  # type: ignore[arg-type]
            started_at=NOW,
            finished_at=NOW,
            stop_reason=reason,
            payload=payload,
        )
    finally:
        c.close()
