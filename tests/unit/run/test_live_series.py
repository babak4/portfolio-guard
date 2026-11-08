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


# --- the FX series: Frankfurter, compared with FRED on the dates both have -------------------


def test_fred_lagging_the_ecb_rate_by_days_still_passes(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, fred_last=date(2026, 11, 5))  # FRED ends 5 days earlier
    live = build_live_levels(cfg, prices, macro, store, TODAY)
    assert live.last_close_date == LAST
    assert live.sources[2] == "frankfurter+fred"


def test_fx_sources_that_disagree_on_a_shared_date_stop_and_name_both(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, fred_scale=1.02, fred_last=date(2026, 11, 5))
    with pytest.raises(DataIntegrityError) as exc:
        build_live_levels(cfg, prices, macro, store, TODAY)
    assert "frankfurter" in str(exc.value) and "fred" in str(exc.value)
    assert store.load("VWRP:XLON") == []


def test_fx_sources_with_no_shared_date_in_the_last_15_days_stop(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, fred_last=date(2026, 10, 20))
    with pytest.raises(DataIntegrityError) as exc:
        build_live_levels(cfg, prices, macro, store, TODAY)
    assert "frankfurter" in str(exc.value) and "fred" in str(exc.value)


# --- no lag allowance on the live series ------------------------------------------------------


def test_a_primary_one_trading_day_behind_stops(store: MarketStore) -> None:
    cfg, _ = load_config()
    assert cfg.data.stale_trading_days >= 1  # the shared rule still allows lag elsewhere
    prices, macro = market(date(2026, 11, 9))  # Monday's close on a Wednesday run
    with pytest.raises(StaleDataError):
        build_live_levels(cfg, prices, macro, store, TODAY)


def test_a_secondary_one_trading_day_behind_stops(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, secondary_last=date(2026, 11, 9))
    with pytest.raises(StaleDataError):
        build_live_levels(cfg, prices, macro, store, TODAY)


def test_a_required_close_that_is_missing_says_it_is_not_available_yet(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST)
    with pytest.raises(DataIntegrityError, match="today's close is not available yet"):
        build_live_levels(cfg, prices, macro, store, TODAY, required_close=TODAY)


# --- minimum history --------------------------------------------------------------------------


def test_too_few_closes_for_the_signals_stops_the_weekly_series(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, days_back=120)  # about 80 closes; the drawdown needs 260
    with pytest.raises(DataIntegrityError, match="history"):
        build_live_levels(cfg, prices, macro, store, TODAY)
    assert store.load("VWRP:XLON") == []


def test_the_daily_series_needs_only_the_volatility_window_and_a_week(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, days_back=120)
    live = build_live_levels(cfg, prices, macro, store, TODAY, history="daily")
    assert len(live.levels) < 260
    prices, macro = market(LAST, days_back=12)  # about 8 closes: under the 21 the volatility needs
    with pytest.raises(DataIntegrityError, match="history"):
        build_live_levels(cfg, prices, macro, store, TODAY, history="daily")


# --- the return cross-check can be advisory ---------------------------------------------------


def test_an_advisory_disagreement_does_not_raise_and_marks_the_series_unconfirmed(
    store: MarketStore,
) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST, secondary_shock={date(2026, 11, 5): 0.95})
    live = build_live_levels(cfg, prices, macro, store, TODAY, returns_check="advisory")
    assert live.unconfirmed is True and live.unconfirmed_note
    assert store.load("VWRP:XLON") == []  # an unconfirmed series is not stored


def test_an_agreeing_advisory_check_is_confirmed(store: MarketStore) -> None:
    cfg, _ = load_config()
    prices, macro = market(LAST)
    live = build_live_levels(cfg, prices, macro, store, TODAY, returns_check="advisory")
    assert live.unconfirmed is False and live.unconfirmed_note is None
