from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from guard.config import load_config
from guard.market.base import CrossCheckError, DataIntegrityError, Observation, StaleDataError
from guard.market.store import MarketStore
from guard.run.live_series import build_live_levels
from guard.storage import open_warehouse

from .fakes import market

TODAY = date(2026, 11, 11)  # a Wednesday
LAST = date(2026, 11, 10)


@pytest.fixture
def store(data_dir: Path) -> MarketStore:
    return MarketStore(open_warehouse())


def test_builds_levels_and_stores_the_accepted_primary(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST)
    live = build_live_levels(cfg, prices, macro, store, TODAY)
    assert live.last_close_date == LAST
    assert live.days[-1] == LAST and len(live.days) == len(live.levels) > 250
    assert live.weekly_return == pytest.approx(live.levels[-1] / live.levels[-6] - 1)
    assert "yahoo" in live.sources[0]
    stored = store.load("VWRP:XLON")
    assert stored[-1].day == LAST and len(stored) == len(live.days)


def test_an_observation_dated_today_is_dropped(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, through=TODAY)
    assert prices[0].series["VWRP:XLON"][-1].day == TODAY
    live = build_live_levels(cfg, prices, macro, store, TODAY)
    assert live.last_close_date == LAST
    assert max(o.day for o in store.load("VWRP:XLON")) == LAST


def test_stale_primary_stops(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(date(2026, 11, 3))
    with pytest.raises(StaleDataError):
        build_live_levels(cfg, prices, macro, store, TODAY)
    assert store.load("VWRP:XLON") == []


def test_five_day_returns_that_disagree_stop_and_name_both_sources(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, secondary_shock={date(2026, 11, 5): 0.95})
    with pytest.raises(CrossCheckError) as exc:
        build_live_levels(cfg, prices, macro, store, TODAY)
    assert len(exc.value.sources) == 2
    assert "VWRP:XLON" in exc.value.sources[0] and "VT:XNYS" in exc.value.sources[1]
    assert store.load("VWRP:XLON") == []


def test_returns_within_tolerance_pass(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, secondary_shock={date(2026, 11, 5): 0.99})
    build_live_levels(cfg, prices, macro, store, TODAY)


def test_one_fx_source_is_not_enough(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST)
    with pytest.raises(DataIntegrityError):
        build_live_levels(cfg, prices, macro[:1], store, TODAY)


def test_an_implausible_jump_stops(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST)
    series = prices[0].series["VWRP:XLON"]
    series[-3] = Observation(series[-3].day, series[-3].value * Decimal("1.4"))
    with pytest.raises(DataIntegrityError, match="jump"):
        build_live_levels(cfg, prices, macro, store, TODAY)


def test_no_primary_data_stops(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST)
    prices[0].fail = True
    with pytest.raises(DataIntegrityError):
        build_live_levels(cfg, prices, macro, store, TODAY)
