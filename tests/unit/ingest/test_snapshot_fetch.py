from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from guard.config import GuardConfig, load_config
from guard.ingest.model import IngestIntegrityError, portfolio_view
from guard.ingest.t212 import T212Client, fetch_snapshot

from .conftest import API, FakeT212, fixture, gia_routes, isa_routes

Make = Callable[..., tuple[T212Client, FakeT212, list[float]]]
NOW = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)


@pytest.fixture
def cfg(data_dir: object) -> GuardConfig:
    return load_config()[0]


def _snap(cfg: GuardConfig, client: T212Client, account_id: str) -> Any:
    account = next(a for a in cfg.portfolio.accounts if a.id == account_id)
    return fetch_snapshot(
        client,
        account,
        satellites=cfg.portfolio.fixed_satellites,
        tolerance=Decimal(str(cfg.data.crosscheck_tolerance)),
        taken_at=NOW,
        day=date(2026, 11, 9),
    )


def test_holdings_key_on_isin_with_labels(make_client: Make, cfg: GuardConfig) -> None:
    client, _, _ = make_client(isa_routes())
    snap = _snap(cfg, client, "isa")
    assert {h.isin for h in snap.holdings} == {
        "IE00BK5BQT80",
        "LU1230136894",
        "US0846707026",
    }
    assert all(h.account_id == "isa" and h.platform == "trading212" for h in snap.holdings)
    assert snap.cash == Decimal("500.0")
    assert snap.total == Decimal("16500.0")


def test_gbx_line_is_normalised_to_pounds(make_client: Make, cfg: GuardConfig) -> None:
    client, _, _ = make_client(isa_routes())
    snap = _snap(cfg, client, "isa")
    vwrp = next(h for h in snap.holdings if h.isin == "IE00BK5BQT80")
    assert vwrp.price == Decimal("100")
    assert vwrp.value_gbp == Decimal("10000")
    assert sum((h.value_gbp for h in snap.holdings), Decimal(0)) + snap.cash == snap.total


def test_string_and_numeric_values_both_parse(make_client: Make, cfg: GuardConfig) -> None:
    client, _, _ = make_client(isa_routes())
    snap = _snap(cfg, client, "isa")
    csh2 = next(h for h in snap.holdings if h.isin == "LU1230136894")
    assert csh2.value_gbp == Decimal("2000")


def test_mismatch_beyond_tolerance_stops_the_run(make_client: Make, cfg: GuardConfig) -> None:
    routes = isa_routes()
    routes[f"{API}/equity/account/summary"] = {**fixture("summary_isa.json"), "totalValue": 17500.0}
    client, _, _ = make_client(routes)
    with pytest.raises(IngestIntegrityError, match="total"):
        _snap(cfg, client, "isa")


def test_pence_misread_as_pounds_is_caught(make_client: Make, cfg: GuardConfig) -> None:
    routes = isa_routes()
    instruments = fixture("instruments.json")
    instruments[0]["currencyCode"] = "GBP"  # the GBX line would now be worth 100x too much
    routes[f"{API}/equity/metadata/instruments"] = instruments
    client, _, _ = make_client(routes)
    with pytest.raises(IngestIntegrityError):
        _snap(cfg, client, "isa")


def test_unknown_instrument_stops_the_run(make_client: Make, cfg: GuardConfig) -> None:
    routes = isa_routes()
    routes[f"{API}/equity/metadata/instruments"] = fixture("instruments.json")[:1]
    client, _, _ = make_client(routes)
    with pytest.raises(IngestIntegrityError, match="ISIN"):
        _snap(cfg, client, "isa")


def test_brk_b_is_held_but_excluded_from_tactical_base(make_client: Make, cfg: GuardConfig) -> None:
    client, _, _ = make_client(isa_routes())
    snap = _snap(cfg, client, "isa")
    brk = next(h for h in snap.holdings if h.isin == "US0846707026")
    assert brk.excluded
    assert not any(h.excluded for h in snap.holdings if h.isin != "US0846707026")
    view = portfolio_view([snap])
    assert view.tactical_base == Decimal("12000")
    assert view.total == Decimal("16500.0")


def test_isa_and_gia_lines_stay_separate_then_combine(make_client: Make, cfg: GuardConfig) -> None:
    isa_client, _, _ = make_client(isa_routes())
    gia_client, _, _ = make_client(gia_routes())
    isa, gia = _snap(cfg, isa_client, "isa"), _snap(cfg, gia_client, "gia")
    assert {h.account_id for h in isa.holdings if h.isin == "IE00BK5BQT80"} == {"isa"}
    assert {h.account_id for h in gia.holdings if h.isin == "IE00BK5BQT80"} == {"gia"}
    view = portfolio_view([isa, gia])
    vwrp = next(ln for ln in view.lines if ln.isin == "IE00BK5BQT80")
    assert vwrp.value_gbp == Decimal("14000")
    assert vwrp.quantity == Decimal("140")
    assert vwrp.accounts == ("isa", "gia")
