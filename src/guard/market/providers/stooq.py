"""Stooq daily closes through the CSV download endpoint (unofficial; never the only source)."""

from __future__ import annotations

import csv
import io
from datetime import date

import httpx

from guard.market.base import Listing, Observation, ProviderError, RawSink
from guard.market.providers._http import default_client, get_bytes, to_decimal

SOURCE_ID = "stooq"
BASE_URL = "https://stooq.com/q/d/l/"


def parse_csv(content: bytes, listing: Listing) -> list[Observation]:
    text = content.decode("utf-8-sig").strip()
    if not text or text.lower().startswith("no data"):
        return []
    reader = csv.DictReader(io.StringIO(text))
    if (
        reader.fieldnames is None
        or "Date" not in reader.fieldnames
        or "Close" not in reader.fieldnames
    ):
        raise ProviderError(f"stooq: unexpected CSV header for {listing.listing_id}")
    rows: list[Observation] = []
    for rec in reader:
        try:
            d = date.fromisoformat(rec["Date"])
        except ValueError as e:
            raise ProviderError(f"stooq: bad date {rec['Date']!r}") from e
        close = to_decimal(rec["Close"], "close")
        if listing.quoted_in_minor_units:
            close = close / 100
        rows.append(Observation(d, close))
    return sorted(rows, key=lambda o: o.day)


class StooqProvider:
    provider_id = SOURCE_ID

    def __init__(self, client: httpx.Client | None = None, sink: RawSink | None = None) -> None:
        self._client = client
        self._sink = sink

    def supports(self, listing: Listing) -> bool:
        return listing.stooq_symbol is not None

    def daily_closes(self, listing: Listing, start: date, end: date) -> list[Observation]:
        if listing.stooq_symbol is None:
            raise ProviderError(f"stooq: no symbol for {listing.listing_id}")
        params = {
            "s": listing.stooq_symbol,
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        content = get_bytes(
            self._client or default_client(),
            BASE_URL,
            params,
            source_id=SOURCE_ID,
            name=f"{listing.stooq_symbol}_{params['d1']}_{params['d2']}.csv",
            sink=self._sink,
        )
        return [o for o in parse_csv(content, listing) if start <= o.day <= end]
