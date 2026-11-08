"""Portfolio page: account totals over time and the latest allocation against its target."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal

from guard.readapi import _payload as p
from guard.readapi.models import AccountSeries, PortfolioView, TotalPoint
from guard.storage import read_store, run_store


def portfolio_view(conn: sqlite3.Connection) -> PortfolioView:
    by_account: dict[str, list[TotalPoint]] = defaultdict(list)
    for s in read_store.snapshot_totals(conn):
        by_account[s.account_id].append(TotalPoint(date.fromisoformat(s.day), s.total, s.cash))
    series = tuple(AccountSeries(a, tuple(pts)) for a, pts in sorted(by_account.items()))

    totals: dict[date, list[TotalPoint]] = defaultdict(list)
    for s_ in series:
        for pt in s_.points:
            totals[pt.day].append(pt)
    combined = tuple(
        TotalPoint(
            d,
            sum((pt.total for pt in pts), Decimal(0)),
            sum((pt.cash for pt in pts), Decimal(0)),
        )
        for d, pts in sorted(totals.items())
        if len(pts) == len(series)  # a day missing one account would understate the total
    )

    last = run_store.last_successful_run(conn, "weekly")
    payload = None if last is None else p.parse(last.payload)
    return PortfolioView(
        series=series,
        combined=combined,
        allocation=() if payload is None else p.allocation(payload),
        allocation_run_id=None if last is None or payload is None else last.run_id,
        sleeve=None if payload is None else p.sleeve(payload),
    )
