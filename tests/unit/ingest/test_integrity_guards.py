from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from guard.config import GuardConfig, load_config
from guard.ingest.manual import load_manual_holdings
from guard.ingest.model import IngestIntegrityError
from guard.ingest.t212 import T212Client, fetch_history, fetch_snapshot

from .conftest import API, FakeT212, fixture, gia_routes, history_routes, isa_routes

Make = Callable[..., tuple[T212Client, FakeT212, list[float]]]
NOW = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)


@pytest.fixture
def cfg(data_dir: object) -> GuardConfig:
    return load_config()[0]


def _run(cfg: GuardConfig, client: T212Client, account_id: str = "isa") -> Any:
    account = next(a for a in cfg.portfolio.accounts if a.id == account_id)
    return fetch_snapshot(
        client,
        account,
        satellites=cfg.portfolio.fixed_satellites,
        tolerance=Decimal(str(cfg.data.crosscheck_tolerance)),
        taken_at=NOW,
        day=date(2026, 11, 9),
    )


def _isa(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    routes = isa_routes()
    mutate(routes)
    return routes


def _summary_currency(r: dict[str, Any]) -> None:
    r[f"{API}/equity/account/summary"] = {**fixture("summary_isa.json"), "currency": "USD"}


def _no_wallet(r: dict[str, Any]) -> None:
    p2 = fixture("positions_isa_p2.json")
    del p2["items"][1]["walletImpact"]
    r[f"{API}/equity/positions?limit=50&cursor=p2"] = p2


def _usd_wallet(r: dict[str, Any]) -> None:
    p2 = fixture("positions_isa_p2.json")
    p2["items"][1]["walletImpact"]["currency"] = "USD"
    r[f"{API}/equity/positions?limit=50&cursor=p2"] = p2


def _no_ticker(r: dict[str, Any]) -> None:
    p1 = fixture("positions_isa_p1.json")
    del p1["items"][0]["instrument"]["ticker"]
    r[f"{API}/equity/positions?limit=50"] = p1


def _bad_number(r: dict[str, Any]) -> None:
    p1 = fixture("positions_isa_p1.json")
    p1["items"][0]["quantity"] = "abc"
    r[f"{API}/equity/positions?limit=50"] = p1


def _nan_number(r: dict[str, Any]) -> None:
    p1 = fixture("positions_isa_p1.json")
    p1["items"][0]["quantity"] = "NaN"
    r[f"{API}/equity/positions?limit=50"] = p1


def _inf_total(r: dict[str, Any]) -> None:
    r[f"{API}/equity/account/summary"] = {**fixture("summary_isa.json"), "totalValue": "Infinity"}


@pytest.mark.parametrize(
    "mutate",
    [
        _summary_currency,
        _no_wallet,
        _usd_wallet,
        _no_ticker,
        _bad_number,
        _nan_number,
        _inf_total,
    ],
)
def test_bad_payload_stops_the_run(
    make_client: Make, cfg: GuardConfig, mutate: Callable[[dict[str, Any]], None]
) -> None:
    client, _, _ = make_client(_isa(mutate))
    with pytest.raises(IngestIntegrityError):
        _run(cfg, client)


@pytest.mark.parametrize("kind", ["orders", "dividends", "transactions"])
def test_history_item_without_id_stops_the_run(make_client: Make, kind: str) -> None:
    routes = history_routes()
    page = fixture(f"{kind}_p1.json")
    items = page["items"] if isinstance(page, dict) else page
    items[0].pop("id", None)
    items[0].pop("reference", None)
    routes[f"{API}/equity/history/{kind}?limit=50"] = page
    client, _, _ = make_client(routes)
    with pytest.raises(IngestIntegrityError, match="without"):
        fetch_history(client)


def test_same_isin_twice_in_one_account_is_merged(make_client: Make, cfg: GuardConfig) -> None:
    routes = gia_routes()
    routes[f"{API}/equity/positions?limit=50"] = [
        {
            "instrument": {"ticker": "VWRPl_EQ", "name": "Vanguard FTSE All-World"},
            "quantity": q,
            "currentPrice": 10000.0,
        }
        for q in (30, 10)
    ]
    client, _, _ = make_client(routes)
    snap = _run(cfg, client, "gia")
    (h,) = snap.holdings
    assert (h.quantity, h.value_gbp, h.price) == (Decimal(40), Decimal(4000), Decimal(100))


def _manual(tmp_path: Path, rows: str) -> Path:
    f = tmp_path / "m.yaml"
    f.write_text(rows)
    return f


def test_manual_same_isin_in_one_account_is_merged(tmp_path: Path) -> None:
    f = _manual(
        tmp_path,
        "- {account_id: a, platform: p, isin: X1, name: n, quantity: 1, value_gbp: 100}\n"
        "- {account_id: a, platform: p, isin: X1, name: n, quantity: 3, value_gbp: 300}\n"
        "- {account_id: b, platform: p, isin: X1, name: n, quantity: 1, value_gbp: 100}\n",
    )
    holdings = load_manual_holdings(f, satellites=())
    assert [(h.account_id, h.quantity, h.value_gbp, h.price) for h in holdings] == [
        ("a", Decimal(4), Decimal(400), Decimal(100)),
        ("b", Decimal(1), Decimal(100), Decimal(100)),
    ]


def test_manual_merge_with_zero_quantity_keeps_larger_line_price(tmp_path: Path) -> None:
    from decimal import Decimal as D

    from guard.ingest.model import Holding, merge_same_isin

    def line(value: str, price: str) -> Holding:
        return Holding("a", "p", "X1", "", "n", D(0), D(price), D(value), False)

    (m,) = merge_same_isin([line("5", "7"), line("9", "8")])
    assert (m.quantity, m.value_gbp, m.price) == (D(0), D(14), D(8))


@pytest.mark.parametrize(
    ("quantity", "value"),
    [
        ("abc", "10"),
        ("NaN", "10"),
        ("Infinity", "10"),
        ("-1", "10"),
        ("1", "abc"),
        ("1", "NaN"),
        ("1", "-Infinity"),
        ("1", "-10"),
    ],
)
def test_manual_bad_numbers_raise_integrity_error(
    tmp_path: Path, quantity: str, value: str
) -> None:
    f = _manual(
        tmp_path,
        f"- {{account_id: a, platform: p, isin: X1, name: n, quantity: '{quantity}',"
        f" value_gbp: '{value}'}}\n",
    )
    with pytest.raises(IngestIntegrityError):
        load_manual_holdings(f, satellites=())
