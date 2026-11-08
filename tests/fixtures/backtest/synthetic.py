"""Seeded synthetic market data for backtest tests: a random walk with a built-in crash."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
from tests.unit.backtest.helpers import weekdays

from guard.market.base import Observation
from guard.market.history import History, HistoryPoint


def world(
    n: int = 3000, seed: int = 7, start: date = date(2007, 1, 1), crash_at: float = 0.2
) -> tuple[dict[date, float], dict[date, float]]:
    """Equity and safe GBP levels on ``n`` weekdays. Equity falls about 35% over 60 days."""
    days = weekdays(start, n)
    rng = np.random.default_rng(seed)
    eq_ret = rng.normal(0.0004, 0.009, n)
    c = int(n * crash_at)
    eq_ret[c : c + 60] = np.log(0.65) / 60 + rng.normal(0, 0.004, 60)
    sf_ret = rng.normal(0.00012, 0.0004, n)
    eq = 100.0 * np.exp(np.cumsum(eq_ret))
    sf = 100.0 * np.exp(np.cumsum(sf_ret))
    return dict(zip(days, map(float, eq), strict=True)), dict(
        zip(days, map(float, sf), strict=True)
    )


def observations(series: dict[date, float], scale: float = 1.0) -> list[Observation]:
    return [Observation(d, Decimal(repr(v * scale))) for d, v in sorted(series.items())]


def history(
    series: dict[date, float], proxy_until: date | None = None, source: str = "etf_a"
) -> History:
    """A History over ``series``; points before ``proxy_until`` are tagged proxy."""
    pts = []
    for d, v in sorted(series.items()):
        proxy = proxy_until is not None and d < proxy_until
        pts.append(HistoryPoint(d, v, "proxy" if proxy else "etf", "stand_in" if proxy else source))
    return History(tuple(pts), {"sources": []})
