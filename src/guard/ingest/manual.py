"""Manual holdings for platforms without an API (for example InvestEngine), from YAML or CSV.

Each row carries ``account_id``, ``platform``, ``isin``, ``name``, ``quantity`` and ``value_gbp``,
and optionally ``ticker`` (used to match fixed satellites).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from guard.ingest.model import (
    ZERO,
    Holding,
    IngestIntegrityError,
    Snapshot,
    is_fixed_satellite,
    merge_same_isin,
    to_decimal,
)

_REQUIRED = ("account_id", "platform", "isin", "name", "quantity", "value_gbp")


def _rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    loaded = yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(loaded, list):
        raise IngestIntegrityError(f"{path.name}: expected a list of holdings")
    return loaded


def load_manual_holdings(path: Path, *, satellites: Iterable[str]) -> list[Holding]:
    satellites = tuple(satellites)
    holdings: list[Holding] = []
    for n, row in enumerate(_rows(path), start=1):
        if not isinstance(row, dict):
            raise IngestIntegrityError(f"{path.name} row {n}: expected a mapping")
        missing = [k for k in _REQUIRED if row.get(k) in (None, "")]
        if missing:
            raise IngestIntegrityError(f"{path.name} row {n}: missing {', '.join(missing)}")
        quantity = to_decimal(row["quantity"], f"{path.name} row {n} quantity", non_negative=True)
        value = to_decimal(row["value_gbp"], f"{path.name} row {n} value_gbp", non_negative=True)
        ticker = str(row.get("ticker") or "")
        holdings.append(
            Holding(
                account_id=str(row["account_id"]),
                platform=str(row["platform"]),
                isin=str(row["isin"]),
                ticker=ticker,
                name=str(row["name"]),
                quantity=quantity,
                price=value / quantity if quantity else ZERO,
                value_gbp=value,
                excluded=bool(ticker) and is_fixed_satellite(ticker, None, satellites),
            )
        )
    return list(merge_same_isin(holdings))


def snapshots_from_manual(
    holdings: Iterable[Holding], taken_at: datetime, day: date
) -> list[Snapshot]:
    """One snapshot per manual account; no cash, so the total is the sum of the lines."""
    by_account: dict[str, list[Holding]] = defaultdict(list)
    for h in holdings:
        by_account[h.account_id].append(h)
    return [
        Snapshot(
            account_id,
            lines[0].platform,
            day,
            taken_at,
            tuple(lines),
            ZERO,
            sum((h.value_gbp for h in lines), ZERO),
        )
        for account_id, lines in by_account.items()
    ]
