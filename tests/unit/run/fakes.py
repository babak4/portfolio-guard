"""Offline stand-ins for providers and the Trading212 client, shared by the run tests."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from guard.market.base import Listing, Observation, ProviderError
from guard.market.calendar import is_trading_day

FX = 1.25  # USD per GBP in the fixtures


def level(day: date, shock: Mapping[date, float] | None = None) -> float:
    """A smooth upward series with a small wobble (volatility far below the threshold)."""
    n = (day - date(2025, 1, 1)).days
    base = 100.0 * (1 + 0.0004 * n) * (1 + 0.004 * math.sin(n / 3.0))
    if shock:
        for since, factor in shock.items():
            if day >= since:
                base *= factor
    return base


def trading_days(start: date, end: date, markets: tuple[str, ...]) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        if is_trading_day(d, markets):
            out.append(d)
        d += timedelta(days=1)
    return out


def dec(x: float) -> Decimal:
    return Decimal(repr(round(x, 6)))


@dataclass
class FakePrices:
    """Serves a closing series per listing id; ``skew`` multiplies one listing's level."""

    provider_id: str
    series: dict[str, list[Observation]] = field(default_factory=dict)
    fail: bool = False
    calls: int = 0

    def supports(self, listing: Listing) -> bool:
        return listing.listing_id in self.series

    def daily_closes(self, listing: Listing, start: date, end: date) -> list[Observation]:
        self.calls += 1
        if self.fail:
            raise ProviderError(f"{self.provider_id}: down")
        return [o for o in self.series[listing.listing_id] if start <= o.day <= end]


@dataclass
class FakeMacro:
    provider_id: str
    series_by_id: dict[str, list[Observation]] = field(default_factory=dict)

    def supports_series(self, series_id: str) -> bool:
        return series_id in self.series_by_id

    def series(self, series_id: str, start: date, end: date) -> list[Observation]:
        return [o for o in self.series_by_id[series_id] if start <= o.day <= end]


def market(
    last_day: date,
    *,
    shock: Mapping[date, float] | None = None,
    secondary_shock: Mapping[date, float] | None = None,
    through: date | None = None,
    primary_id: str = "VWRP:XLON",
    secondary_id: str = "VT:XNYS",
    fred_last: date | None = None,
    fred_scale: float = 1.0,
    secondary_last: date | None = None,
    days_back: int = 500,
) -> tuple[list[FakePrices], list[FakeMacro]]:
    """Providers for a market whose last close is ``last_day`` (observations after it absent,
    except that ``through`` extends the primary series to a later day, e.g. today).

    ``fred_last`` ends the FRED rate series earlier (it lags the ECB rate by days), ``fred_scale``
    multiplies its values, ``secondary_last`` ends the second ETF earlier and ``days_back`` sets
    how much history exists."""
    start = last_day - timedelta(days=days_back)
    end = through or last_day
    primary = [Observation(d, dec(level(d, shock))) for d in trading_days(start, end, ("uk",))]
    secondary = [
        Observation(d, dec(level(d, secondary_shock) * 0.9 * FX))
        for d in trading_days(start, secondary_last or last_day, ("us",))
    ]
    fx_a = [Observation(d, dec(FX)) for d in trading_days(start, last_day, ())]
    fx_b = [
        Observation(d, dec(FX * fred_scale)) for d in trading_days(start, fred_last or last_day, ())
    ]
    prices = [
        FakePrices("yahoo", {primary_id: primary, secondary_id: secondary}),
    ]
    macro = [
        FakeMacro("frankfurter", {"GBPUSD": fx_a}),
        FakeMacro("fred", {"DEXUSUK": fx_b}),
    ]
    return prices, macro


Hook = Callable[[str], Any]


def t212_client_factory(
    *, summary_isa_total: float | None = None, isa_cash: Mapping[str, Any] | None = None
) -> Callable[[str], Any]:
    """A ``client_for`` serving the Trading212 fixtures through a mock transport."""
    import httpx

    from guard.ingest.t212 import T212Client
    from tests.unit.ingest.conftest import (
        API,
        FakeT212,
        gia_routes,
        history_routes,
        isa_routes,
    )

    def client_for(account_id: str) -> T212Client:
        routes = (isa_routes() if account_id == "isa" else gia_routes()) | history_routes()
        if account_id == "isa" and summary_isa_total is not None:
            routes[f"{API}/equity/account/summary"] = {
                "id": 1,
                "currency": "GBP",
                "totalValue": summary_isa_total,
                "cash": {"availableToTrade": "500.0"},
                "investments": {},
            }
        if account_id == "isa" and isa_cash is not None:
            routes[f"{API}/equity/account/summary"] = {
                "id": 1,
                "currency": "GBP",
                "totalValue": 16500.0,
                "cash": dict(isa_cash),
                "investments": {},
            }
        return T212Client(
            "https://demo.trading212.com/api/v0",
            "key",
            "secret",
            transport=httpx.MockTransport(FakeT212(routes)),
            sleep=lambda _s: None,
        )

    return client_for


def make_deps(
    prices: list[FakePrices],
    macro: list[FakeMacro],
    *,
    summary_isa_total: float | None = None,
    isa_cash: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> Any:
    from guard.advice import load_registry
    from guard.config import load_config
    from guard.market.store import MarketStore
    from guard.run.deps import Deps
    from guard.storage import open_ops, open_warehouse

    config, version = load_config()
    warehouse = open_warehouse()
    del MarketStore
    return Deps(
        config=config,
        config_version=version,
        ops=open_ops(),
        warehouse=warehouse,
        client_for=t212_client_factory(summary_isa_total=summary_isa_total, isa_cash=isa_cash),
        manual_path=None,
        price_providers=prices,
        macro_providers=macro,
        funds=load_registry,
        clock=lambda: now or datetime(2026, 11, 11, 12, 0, 5, tzinfo=UTC),
        code_version="test-code",
    )


def step_for_return(day: date, previous: date, target: float) -> float:
    """The factor to apply from ``day`` so the close on ``day`` is ``target`` above ``previous``."""
    return (1 + target) * level(previous) / level(day)


def steps_for_week(
    first: date, second: date, start: date, end: date, target: float
) -> dict[date, float]:
    """Two equal steps (on ``first`` and ``second``) taking ``end`` to ``target`` against ``start``."""
    total = (1 + target) * level(start) / level(end)
    root = total**0.5
    return {first: root, second: root}
