"""FX from Frankfurter (ECB reference rates). Series ids are ``<BASE><QUOTE>``, e.g. ``GBPUSD``
is units of USD per 1 GBP. Requests are chunked because long ranges may come back down-sampled.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import httpx

from guard.market.base import Observation, ProviderError, RawSink
from guard.market.providers._http import default_client, get_bytes, to_decimal

SOURCE_ID = "frankfurter"
BASE_URL = "https://api.frankfurter.dev/v1"
CHUNK_DAYS = 90
_PAIR = re.compile(r"^([A-Z]{3})([A-Z]{3})$")


def parse_json(content: bytes, quote: str) -> list[Observation]:
    try:
        rates = json.loads(content)["rates"]
    except (ValueError, KeyError, TypeError) as e:
        raise ProviderError("frankfurter: unexpected payload") from e
    rows = [
        Observation(date.fromisoformat(day), to_decimal(repr(by_ccy[quote]), "rate"))
        for day, by_ccy in rates.items()
        if quote in by_ccy
    ]
    return sorted(rows, key=lambda o: o.day)


class FrankfurterProvider:
    provider_id = SOURCE_ID

    def __init__(self, client: httpx.Client | None = None, sink: RawSink | None = None) -> None:
        self._client = client
        self._sink = sink

    def supports_series(self, series_id: str) -> bool:
        m = _PAIR.match(series_id)
        return m is not None and m.group(1) != m.group(2)

    def series(self, series_id: str, start: date, end: date) -> list[Observation]:
        m = _PAIR.match(series_id)
        if m is None:
            raise ProviderError(f"frankfurter: unsupported series {series_id!r}")
        base, quote = m.groups()
        client = self._client or default_client()
        rows: list[Observation] = []
        lo = start
        while lo <= end:
            hi = min(lo + timedelta(days=CHUNK_DAYS - 1), end)
            content = get_bytes(
                client,
                f"{BASE_URL}/{lo.isoformat()}..{hi.isoformat()}",
                {"base": base, "symbols": quote},
                source_id=SOURCE_ID,
                name=f"{series_id}_{lo:%Y%m%d}_{hi:%Y%m%d}.json",
                sink=self._sink,
            )
            rows.extend(o for o in parse_json(content, quote) if start <= o.day <= end)
            lo = hi + timedelta(days=1)
        return rows
