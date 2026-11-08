"""FRED series through the public ``fredgraph.csv`` endpoint (no key needed).

Holiday gaps arrive as empty or ``.`` values. They are forward-filled from the previous value and
flagged ``filled`` rather than dropped. Gaps before the first real value cannot be filled and are
skipped. FRED publishes with a lag, so these series suit history, not the live run.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

import httpx

from guard.market.base import Observation, ProviderError, RawSink
from guard.market.providers._http import default_client, get_bytes, to_decimal

SOURCE_ID = "fred"
BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SERIES = frozenset({"DEXUSUK", "VIXCLS"})


def parse_fredgraph_csv(content: bytes, series_id: str) -> list[Observation]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    names = reader.fieldnames or []
    date_col = next((c for c in names if c.upper() in ("DATE", "OBSERVATION_DATE")), None)
    if date_col is None or series_id not in names:
        raise ProviderError(f"fred: unexpected CSV header for {series_id}")
    rows: list[Observation] = []
    last: Decimal | None = None
    for rec in reader:
        try:
            day = date.fromisoformat(rec[date_col])
        except ValueError as e:
            raise ProviderError(f"fred: bad date {rec[date_col]!r}") from e
        raw = (rec[series_id] or "").strip()
        if raw in ("", "."):
            if last is not None:
                rows.append(Observation(day, last, filled=True))
            continue
        last = to_decimal(raw, "value")
        rows.append(Observation(day, last))
    return sorted(rows, key=lambda o: o.day)


class FredProvider:
    provider_id = SOURCE_ID

    def __init__(self, client: httpx.Client | None = None, sink: RawSink | None = None) -> None:
        self._client = client
        self._sink = sink

    def supports_series(self, series_id: str) -> bool:
        return series_id in SERIES

    def series(self, series_id: str, start: date, end: date) -> list[Observation]:
        if not self.supports_series(series_id):
            raise ProviderError(f"fred: unsupported series {series_id!r}")
        content = get_bytes(
            self._client or default_client(),
            BASE_URL,
            {"id": series_id, "cosd": start.isoformat(), "coed": end.isoformat()},
            source_id=SOURCE_ID,
            name=f"{series_id}_{start:%Y%m%d}_{end:%Y%m%d}.csv",
            sink=self._sink,
        )
        return [o for o in parse_fredgraph_csv(content, series_id) if start <= o.day <= end]
