from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from guard.ingest.t212 import T212Client

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "t212"
API = "/api/v0"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text("utf-8"))


Routes = dict[str, Any]


class FakeT212:
    """Serves fixture bodies by path and query; records every request."""

    def __init__(self, routes: Routes) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = request.url.raw_path.decode()
        body = self.routes[key]
        if isinstance(body, list) and body and isinstance(body[0], httpx.Response):
            return body.pop(0)
        return httpx.Response(200, json=body)


def isa_routes() -> Routes:
    return {
        f"{API}/equity/metadata/instruments": fixture("instruments.json"),
        f"{API}/equity/positions?limit=50": fixture("positions_isa_p1.json"),
        f"{API}/equity/positions?limit=50&cursor=p2": fixture("positions_isa_p2.json"),
        f"{API}/equity/account/summary": fixture("summary_isa.json"),
    }


def gia_routes() -> Routes:
    return {
        f"{API}/equity/metadata/instruments": fixture("instruments.json"),
        f"{API}/equity/positions?limit=50": fixture("positions_gia.json"),
        f"{API}/equity/account/summary": fixture("summary_gia.json"),
    }


def history_routes() -> Routes:
    routes: Routes = {}
    for kind in ("orders", "dividends", "transactions"):
        routes[f"{API}/equity/history/{kind}?limit=50"] = fixture(f"{kind}_p1.json")
        routes[f"{API}/equity/history/{kind}?limit=50&cursor=p2"] = fixture(f"{kind}_p2.json")
    return routes


@pytest.fixture
def make_client() -> Callable[..., tuple[T212Client, FakeT212, list[float]]]:
    def build(routes: Routes, **kw: Any) -> tuple[T212Client, FakeT212, list[float]]:
        fake = FakeT212(routes)
        slept: list[float] = []
        client = T212Client(
            "https://demo.trading212.com/api/v0",
            "key",
            "secret",
            transport=httpx.MockTransport(fake),
            sleep=slept.append,
            clock=kw.pop("clock", lambda: 1000.0),
            **kw,
        )
        return client, fake, slept

    return build
