"""Everything a run needs from the outside world, in one place so tests can inject fakes."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from guard.advice import FundRegistry, load_registry
from guard.config import GuardConfig, load_config
from guard.ingest.t212 import LIVE_BASE_URL, T212Client, client_for_account
from guard.market.base import MacroProvider, PriceProvider
from guard.market.providers.frankfurter import FrankfurterProvider
from guard.market.providers.fred import FredProvider
from guard.market.providers.stooq import StooqProvider
from guard.market.providers.yahoo import YahooProvider
from guard.market.store import MarketStore
from guard.paths import data_dir
from guard.run.audit import code_version
from guard.run.results import Notice
from guard.storage import open_ops, open_warehouse

log = logging.getLogger("guard.run")


def log_notice(notice: Notice) -> None:
    """The default notifier: a warning in the log. The alert layer replaces it."""
    log.warning("%s %s %s: %s", notice.kind, notice.slot, notice.status, notice.message)


@dataclass
class Deps:
    config: GuardConfig
    config_version: str
    ops: sqlite3.Connection
    warehouse: duckdb.DuckDBPyConnection
    client_for: Callable[[str], T212Client]
    manual_path: Path | None
    price_providers: Sequence[PriceProvider]
    macro_providers: Sequence[MacroProvider]
    funds: Callable[[], FundRegistry]
    clock: Callable[[], datetime]
    code_version: str = field(default_factory=code_version)
    notify: Callable[[Notice], None] = log_notice

    def close(self) -> None:
        self.ops.close()
        self.warehouse.close()


def default_deps() -> Deps:
    """Real providers and Trading212 clients. ``GUARD_T212_BASE_URL`` picks the host."""
    config, version = load_config()
    ops = open_ops()
    warehouse = open_warehouse()
    sink = MarketStore(warehouse).raw_sink
    base_url = os.environ.get("GUARD_T212_BASE_URL", LIVE_BASE_URL)
    manual = data_dir() / "manual_holdings.yaml"
    return Deps(
        config=config,
        config_version=version,
        ops=ops,
        warehouse=warehouse,
        client_for=lambda account_id: client_for_account(account_id, base_url),
        manual_path=manual if manual.exists() else None,
        price_providers=[YahooProvider(sink=sink), StooqProvider(sink=sink)],
        macro_providers=[FrankfurterProvider(sink=sink), FredProvider(sink=sink)],
        funds=load_registry,
        clock=lambda: datetime.now(UTC),
    )
