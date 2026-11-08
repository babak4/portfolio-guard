from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from guard.ingest.t212 import ReadOnlyViolation, T212Client

from .conftest import API, FakeT212, fixture, history_routes, isa_routes

Make = Callable[..., tuple[T212Client, FakeT212, list[float]]]


def test_two_page_positions_merge_into_one_list(make_client: Make) -> None:
    client, fake, _ = make_client(isa_routes())
    items = client.get_all(f"{API}/equity/positions?limit=50")
    tickers = [i["instrument"]["ticker"] for i in items]
    assert tickers == ["VWRPl_EQ", "CSH2l_EQ", "BRKB_US_EQ"]
    assert len(fake.requests) == 2


def test_basic_auth_header_uses_key_and_secret(make_client: Make) -> None:
    client, fake, _ = make_client(isa_routes())
    client.get(f"{API}/equity/account/summary")
    expected = "Basic " + base64.b64encode(b"key:secret").decode()
    assert fake.requests[0].headers["Authorization"] == expected


def test_429_retry_after_is_honoured(make_client: Make) -> None:
    path = f"{API}/equity/account/summary"
    routes: dict[str, Any] = {
        path: [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    }
    client, fake, slept = make_client(routes)
    assert client.get(path) == {"ok": 1}
    assert slept == [7.0]
    assert len(fake.requests) == 2


def test_429_without_retry_after_uses_ratelimit_reset(make_client: Make) -> None:
    path = f"{API}/equity/account/summary"
    routes: dict[str, Any] = {
        path: [
            httpx.Response(429, headers={"x-ratelimit-reset": "1012"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    }
    client, _, slept = make_client(routes, clock=lambda: 1000.0)
    client.get(path)
    assert slept == [12.0]


def test_spent_ratelimit_header_pauses_until_reset(make_client: Make) -> None:
    path = f"{API}/equity/account/summary"
    routes: dict[str, Any] = {
        path: [
            httpx.Response(
                200,
                json={"ok": 1},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1003"},
            )
        ]
    }
    client, _, slept = make_client(routes, clock=lambda: 1000.0)
    client.get(path)
    assert slept == [3.0]


def test_exhausted_retries_raise(make_client: Make) -> None:
    path = f"{API}/equity/account/summary"
    routes: dict[str, Any] = {path: [httpx.Response(429) for _ in range(20)]}
    client, _, _ = make_client(routes)
    with pytest.raises(httpx.HTTPStatusError):
        client.get(path)


@pytest.mark.parametrize("method", ["POST", "DELETE", "PUT", "PATCH"])
def test_non_get_raises_and_sends_nothing(make_client: Make, method: str) -> None:
    client, fake, _ = make_client(isa_routes())
    with pytest.raises(ReadOnlyViolation):
        client.request(method, f"{API}/equity/orders/market")
    assert fake.requests == []


def test_path_outside_allowlist_raises(make_client: Make) -> None:
    client, fake, _ = make_client(isa_routes())
    with pytest.raises(ReadOnlyViolation):
        client.get(f"{API}/equity/orders/limit")
    assert fake.requests == []


def test_history_pages_follow_next_page_path_until_null(make_client: Make) -> None:
    client, fake, _ = make_client(history_routes())
    items = client.get_all(f"{API}/equity/history/orders?limit=50")
    assert [i["id"] for i in items] == [1001, 1002, 1003]
    assert fixture("orders_p2.json")["nextPagePath"] is None
    assert len(fake.requests) == 2
