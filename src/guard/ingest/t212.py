"""Read-only Trading212 client and the one place its payloads are parsed.

Only GET is ever issued, only to the paths allowed below. The payload field names are assumptions
checked against the demo host; they are listed in ``docs/queries/t212-payload-shapes.md``.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx

from guard.config.models import Account
from guard.ingest.model import (
    ZERO,
    Holding,
    IngestIntegrityError,
    Snapshot,
    is_fixed_satellite,
    merge_same_isin,
    to_decimal,
)
from guard.secrets import get_secret

LIVE_BASE_URL = "https://live.trading212.com/api/v0"
DEMO_BASE_URL = "https://demo.trading212.com/api/v0"
PAGE_SIZE = 50

POSITIONS = f"/equity/positions?limit={PAGE_SIZE}"
SUMMARY = "/equity/account/summary"
INSTRUMENTS = "/equity/metadata/instruments"
HISTORY: dict[str, tuple[str, str]] = {  # kind -> (first page path, id field)
    "orders": (f"/equity/history/orders?limit={PAGE_SIZE}", "id"),
    "dividends": (f"/equity/history/dividends?limit={PAGE_SIZE}", "reference"),
    "transactions": (f"/equity/history/transactions?limit={PAGE_SIZE}", "reference"),
}

_ALLOWED_PATHS = frozenset(
    urlsplit(p).path for p in (POSITIONS, SUMMARY, INSTRUMENTS, *(v[0] for v in HISTORY.values()))
)
_MAX_PAGES = 500
_MAX_RETRIES = 6
_MAX_WAIT = 120.0
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_KNOWN_HOSTS = frozenset({"live.trading212.com", "demo.trading212.com"})


class ReadOnlyViolation(ValueError):
    """A request other than GET, or to a path outside the read-only allowlist."""


class T212RequestError(RuntimeError):
    """The request kept failing at the transport level (timeouts, connection errors)."""


def _check_base_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if parts.scheme != "https" or parts.hostname not in _KNOWN_HOSTS:
        raise ValueError(
            "base URL must be https on live.trading212.com or demo.trading212.com, "
            f"not {parts.scheme}://{parts.hostname}"
        )


class T212Client:
    """HTTP Basic client (key as username, secret as password)."""

    def __init__(
        self,
        base_url: str,
        key: str,
        secret: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if transport is None:
            _check_base_url(base_url)
        parts = urlsplit(base_url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._prefix = parts.path.rstrip("/")
        self._sleep = sleep
        self._clock = clock
        self._http = httpx.Client(
            auth=httpx.BasicAuth(key, secret),
            headers={"Accept": "application/json"},
            transport=transport,
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def _url(self, path: str) -> tuple[str, str]:
        """Full URL and the path relative to the API base (``nextPagePath`` carries the prefix)."""
        if path.startswith(self._prefix + "/"):
            return self._origin + path, path[len(self._prefix) :]
        return self._origin + self._prefix + path, path

    def _wait_seconds(
        self, headers: Mapping[str, str], backoff: float, honour_retry_after: bool = True
    ) -> float:
        retry_after = headers.get("Retry-After") if honour_retry_after else None
        reset = headers.get("x-ratelimit-reset")
        try:
            if retry_after is not None:
                seconds = float(retry_after)
                if math.isfinite(seconds) and seconds >= 0:
                    return min(seconds, _MAX_WAIT)
            elif reset is not None:
                seconds = float(reset) - self._clock()
                if math.isfinite(seconds):
                    return min(max(seconds, 0.0), _MAX_WAIT)
        except ValueError:
            pass
        return min(backoff, _MAX_WAIT)

    def request(self, method: str, path: str) -> Any:
        if method != "GET":
            raise ReadOnlyViolation(f"only GET is allowed, not {method}")
        url, relative = self._url(path)
        path_only = urlsplit(relative).path
        if path_only not in _ALLOWED_PATHS:
            raise ReadOnlyViolation(f"path not in read-only allowlist: {path_only}")
        backoff = 1.0
        for attempt in range(_MAX_RETRIES + 1):
            last = attempt == _MAX_RETRIES
            try:
                resp = self._http.get(url)
            except httpx.TransportError as e:
                if last:
                    raise T212RequestError(
                        f"request failed after {attempt + 1} attempts: {type(e).__name__}"
                    ) from e
                self._sleep(min(backoff, _MAX_WAIT))
                backoff *= 2
                continue
            if resp.status_code in _RETRY_STATUSES and not last:
                self._sleep(self._wait_seconds(resp.headers, backoff))
                backoff *= 2
                continue
            resp.raise_for_status()
            if resp.headers.get("x-ratelimit-remaining") == "0":
                self._sleep(self._wait_seconds(resp.headers, 0.0, honour_retry_after=False))
            return json.loads(resp.text, parse_float=Decimal)
        raise AssertionError("unreachable")  # pragma: no cover

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def get_all(self, path: str) -> list[Any]:
        """Items from every page, following ``nextPagePath`` until it is null.

        A bare list is a single page.
        """
        items: list[Any] = []
        seen: set[str] = set()
        next_path: str | None = path
        while next_path is not None:
            if next_path in seen:
                raise IngestIntegrityError(f"pagination repeats a page: {next_path}")
            if len(seen) >= _MAX_PAGES:
                raise IngestIntegrityError(f"pagination exceeds {_MAX_PAGES} pages")
            seen.add(next_path)
            doc = self.get(next_path)
            if isinstance(doc, list):
                items.extend(doc)
                break
            items.extend(doc["items"])
            next_path = doc.get("nextPagePath") or None
        return items


def client_for_account(account_id: str, base_url: str = LIVE_BASE_URL) -> T212Client:
    return T212Client(
        base_url,
        get_secret(f"guard_t212_key_{account_id}"),
        get_secret(f"guard_t212_secret_{account_id}"),
    )


def _dec(value: Any, field: str) -> Decimal:
    """Numbers arrive as JSON numbers or strings; both are accepted."""
    if value is None or isinstance(value, bool):
        raise IngestIntegrityError(f"missing or invalid number for {field}")
    return to_decimal(value, field)


def _holding(
    p: Mapping[str, Any],
    instruments: Mapping[str, Mapping[str, Any]],
    account: Account,
    satellites: tuple[str, ...],
) -> Holding:
    inst = p.get("instrument") or {}
    ticker = str(inst.get("ticker") or p.get("ticker") or "")
    if not ticker:
        raise IngestIntegrityError("position without a ticker")
    meta = instruments.get(ticker, {})
    isin = meta.get("isin") or inst.get("isin")
    if not isin:
        raise IngestIntegrityError(f"no ISIN for instrument {ticker}")
    currency = meta.get("currencyCode") or meta.get("currency") or inst.get("currency")
    quantity = _dec(p.get("quantity"), f"{ticker} quantity")
    price = _dec(p.get("currentPrice"), f"{ticker} currentPrice")
    if currency == "GBX":
        price /= 100
        value = quantity * price
    elif currency == "GBP":
        value = quantity * price
    else:
        wallet = p.get("walletImpact") or {}
        if wallet.get("currency") != "GBP":
            raise IngestIntegrityError(f"no GBP value for {currency} instrument {ticker}")
        value = _dec(wallet.get("currentValue"), f"{ticker} walletImpact.currentValue")
    return Holding(
        account_id=account.id,
        platform=account.platform,
        isin=str(isin),
        ticker=ticker,
        name=str(meta.get("name") or inst.get("name") or ticker),
        quantity=quantity,
        price=price,
        value_gbp=value,
        excluded=is_fixed_satellite(ticker, meta.get("shortName"), satellites),
    )


def fetch_snapshot(
    client: T212Client,
    account: Account,
    *,
    satellites: tuple[str, ...],
    tolerance: Decimal,
    taken_at: datetime,
    day: date,
) -> Snapshot:
    """Read positions, cash and instruments and reconcile them with the account total.

    ``tolerance`` is the allowed fraction of the total by which positions plus cash may differ
    from it; beyond that the run stops.
    """
    instruments = {str(i["ticker"]): i for i in client.get_all(INSTRUMENTS)}
    holdings = merge_same_isin(
        _holding(p, instruments, account, satellites) for p in client.get_all(POSITIONS)
    )
    summary = client.get(SUMMARY)
    if summary.get("currency") != "GBP":
        raise IngestIntegrityError(f"account currency is {summary.get('currency')}, not GBP")
    total = _dec(summary.get("totalValue"), "totalValue")
    cash = sum(
        (_dec(v, f"cash.{k}") for k, v in (summary.get("cash") or {}).items()),
        ZERO,
    )
    gap = abs(sum((h.value_gbp for h in holdings), ZERO) + cash - total)
    if gap > tolerance * total:
        raise IngestIntegrityError(
            f"positions plus cash differ from the account total by {gap} (total {total})"
        )
    return Snapshot(account.id, account.platform, day, taken_at, holdings, cash, total)


RawItems = list[tuple[str, dict[str, Any]]]


def fetch_history(client: T212Client) -> dict[str, RawItems]:
    """Orders, dividends and transactions as ``(external_id, raw item)`` pairs."""
    out: dict[str, RawItems] = {}
    for kind, (path, id_field) in HISTORY.items():
        pairs: RawItems = []
        for item in client.get_all(path):
            if item.get(id_field) is None:
                raise IngestIntegrityError(f"{kind} item without {id_field}")
            pairs.append((str(item[id_field]), item))
        out[kind] = pairs
    return out
