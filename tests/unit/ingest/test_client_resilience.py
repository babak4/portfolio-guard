from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from guard.ingest import t212
from guard.ingest.model import IngestIntegrityError
from guard.ingest.t212 import T212Client

from .conftest import API, FakeT212

Make = Callable[..., tuple[T212Client, FakeT212, list[float]]]
PATH = f"{API}/equity/account/summary"
OK = httpx.Response(200, json={"ok": 1})


def _routes(*responses: httpx.Response) -> dict[str, Any]:
    return {PATH: list(responses)}


def test_503_then_200_succeeds_with_backoff(make_client: Make) -> None:
    client, fake, slept = make_client(_routes(httpx.Response(503), OK))
    assert client.get(PATH) == {"ok": 1}
    assert slept == [1.0]
    assert len(fake.requests) == 2


@pytest.mark.parametrize("status", [500, 502, 504])
def test_other_5xx_statuses_are_retried(make_client: Make, status: int) -> None:
    client, _, slept = make_client(_routes(httpx.Response(status), OK))
    assert client.get(PATH) == {"ok": 1}
    assert slept == [1.0]


def test_persistent_503_raises_after_the_cap(make_client: Make) -> None:
    client, fake, slept = make_client(_routes(*[httpx.Response(503) for _ in range(20)]))
    with pytest.raises(httpx.HTTPStatusError, match="503"):
        client.get(PATH)
    assert len(fake.requests) == t212._MAX_RETRIES + 1
    assert slept == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]


def test_transport_error_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("slow", request=request)
        return OK

    slept: list[float] = []
    client = T212Client(
        "https://demo.trading212.com/api/v0",
        "k",
        "s",
        transport=httpx.MockTransport(handler),
        sleep=slept.append,
    )
    assert client.get(PATH) == {"ok": 1}
    assert slept == [1.0]


def test_persistent_transport_error_raises_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    slept: list[float] = []
    client = T212Client(
        "https://demo.trading212.com/api/v0",
        "k",
        "s",
        transport=httpx.MockTransport(handler),
        sleep=slept.append,
    )
    with pytest.raises(t212.T212RequestError, match="attempts"):
        client.get(PATH)
    assert len(slept) == t212._MAX_RETRIES


@pytest.mark.parametrize("bad", ["Wed, 21 Oct 2026 07:28:00 GMT", "nan", "-5", "inf"])
def test_malformed_retry_after_falls_back_to_backoff(make_client: Make, bad: str) -> None:
    client, _, slept = make_client(_routes(httpx.Response(429, headers={"Retry-After": bad}), OK))
    client.get(PATH)
    assert slept == [1.0]


def test_retry_after_is_capped(make_client: Make) -> None:
    client, _, slept = make_client(_routes(httpx.Response(503, headers={"Retry-After": "900"}), OK))
    client.get(PATH)
    assert slept == [120.0]


def test_backoff_doubles_and_caps(make_client: Make, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t212, "_MAX_RETRIES", 9)
    client, _, slept = make_client(_routes(*[httpx.Response(503) for _ in range(10)]))
    with pytest.raises(httpx.HTTPStatusError):
        client.get(PATH)
    assert slept == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 120.0, 120.0]


@pytest.mark.parametrize(
    "responses",
    [
        [httpx.Response(401)] * 1,
        [httpx.Response(429)] * 20,
        [httpx.Response(503)] * 20,
    ],
)
def test_credentials_never_appear_in_logs_or_errors(
    make_client: Make, caplog: pytest.LogCaptureFixture, responses: list[httpx.Response]
) -> None:
    caplog.set_level(logging.DEBUG)
    client, _, _ = make_client(_routes(*responses))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        client.get(PATH)
    text = caplog.text + str(exc.value) + repr(exc.value)
    assert "secret" not in text.replace("Secret", "")
    assert "key:secret" not in text
    assert "c2VjcmV0" not in text  # base64 of the secret


@pytest.mark.parametrize(
    "url",
    [
        "http://live.trading212.com/api/v0",
        "https://evil.example.com/api/v0",
        "https://live.trading212.com.evil.example/api/v0",
        "ftp://demo.trading212.com/api/v0",
    ],
)
def test_unknown_or_insecure_base_url_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="base URL"):
        T212Client(url, "k", "s")


@pytest.mark.parametrize("url", [t212.LIVE_BASE_URL, t212.DEMO_BASE_URL])
def test_known_hosts_are_accepted(url: str) -> None:
    T212Client(url, "k", "s").close()


def test_any_url_is_accepted_with_an_injected_transport() -> None:
    T212Client("http://test.invalid/api/v0", "k", "s", transport=httpx.MockTransport(lambda r: OK))


def _paged(next_for: Callable[[int], str | None]) -> T212Client:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, json={"items": [calls["n"]], "nextPagePath": next_for(calls["n"])}
        )

    return T212Client(
        "https://demo.trading212.com/api/v0",
        "k",
        "s",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )


FIRST = f"{API}/equity/positions?limit=50"


def test_self_referencing_next_page_raises() -> None:
    client = _paged(lambda n: FIRST)
    with pytest.raises(IngestIntegrityError, match="repeat"):
        client.get_all(FIRST)


def test_endless_pagination_raises_at_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(t212, "_MAX_PAGES", 5)
    client = _paged(lambda n: f"{FIRST}&cursor=c{n}")
    with pytest.raises(IngestIntegrityError, match="pages"):
        client.get_all(FIRST)
