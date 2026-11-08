"""Shared HTTP helper: GET, hand the raw bytes to the sink, then let the caller parse."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import httpx

from guard.market.base import ProviderError, RawSink

USER_AGENT = "portfolio-guard/0.1 (personal, non-commercial)"

log = logging.getLogger("guard.market")


def default_client() -> httpx.Client:
    return httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def get_bytes(
    client: httpx.Client,
    url: str,
    params: Mapping[str, str] | None,
    *,
    source_id: str,
    name: str,
    sink: RawSink | None,
) -> bytes:
    """GET ``url`` and return the body after passing it to ``sink``.

    Failures raise ``ProviderError`` and log only the source, status or exception class: never the
    URL, parameters or headers.
    """
    try:
        resp = client.get(url, params=dict(params) if params else None)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        log.warning("%s: HTTP %s", source_id, status)
        raise ProviderError(f"{source_id}: HTTP {status}") from None
    except httpx.HTTPError as e:
        log.warning("%s: %s", source_id, type(e).__name__)
        raise ProviderError(f"{source_id}: {type(e).__name__}") from None
    if sink is not None:
        sink(source_id, name, resp.content)
    return resp.content


def to_decimal(text: str, what: str) -> Decimal:
    try:
        value = Decimal(text.strip())
    except InvalidOperation as e:
        raise ProviderError(f"unparsable {what}: {text!r}") from e
    if not value.is_finite():
        raise ProviderError(f"non-finite {what}: {text!r}")
    return value
