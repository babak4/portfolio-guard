"""Yahoo Finance daily closes through the optional ``yfinance`` package (extra ``market``).

yfinance is imported lazily. The raw payload kept is the history as CSV (``Date,Close``) because
yfinance exposes DataFrames, not HTTP bodies. Unofficial: never the only source.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import date, timedelta

from guard.market.base import Listing, Observation, ProviderError, ProviderUnavailable, RawSink
from guard.market.providers._http import to_decimal

SOURCE_ID = "yahoo"

Downloader = Callable[[str, date, date], bytes]


def default_downloader(symbol: str, start: date, end: date) -> bytes:
    try:
        import yfinance
    except ImportError as e:
        raise ProviderUnavailable(
            "yfinance is not installed; install the optional extra: uv sync --extra market"
        ) from e
    try:
        df = yfinance.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # end is exclusive in yfinance
            auto_adjust=False,
            actions=False,
        )
    except Exception as e:  # noqa: BLE001 - yfinance raises assorted errors
        raise ProviderError(f"yahoo: download failed: {type(e).__name__}") from None
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Date", "Close"])
    for ts, close in zip(df.index, df["Close"], strict=True):
        writer.writerow([ts.strftime("%Y-%m-%d"), repr(float(close))])
    return out.getvalue().encode("utf-8")


def parse_csv(content: bytes, listing: Listing) -> list[Observation]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if (
        reader.fieldnames is None
        or "Date" not in reader.fieldnames
        or "Close" not in reader.fieldnames
    ):
        raise ProviderError(f"yahoo: unexpected CSV header for {listing.listing_id}")
    rows: list[Observation] = []
    for rec in reader:
        if not rec["Close"]:
            continue
        try:
            d = date.fromisoformat(rec["Date"][:10])
        except ValueError as e:
            raise ProviderError(f"yahoo: bad date {rec['Date']!r}") from e
        close = to_decimal(rec["Close"], "close")
        if listing.quoted_in_minor_units:
            close = close / 100
        rows.append(Observation(d, close))
    return sorted(rows, key=lambda o: o.day)


class YahooProvider:
    provider_id = SOURCE_ID

    def __init__(self, downloader: Downloader | None = None, sink: RawSink | None = None) -> None:
        self._download = downloader or default_downloader
        self._sink = sink

    def supports(self, listing: Listing) -> bool:
        return listing.yahoo_symbol is not None

    def daily_closes(self, listing: Listing, start: date, end: date) -> list[Observation]:
        if listing.yahoo_symbol is None:
            raise ProviderError(f"yahoo: no symbol for {listing.listing_id}")
        content = self._download(listing.yahoo_symbol, start, end)
        if self._sink is not None:
            name = f"{listing.yahoo_symbol}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
            self._sink(SOURCE_ID, name, content)
        return [o for o in parse_csv(content, listing) if start <= o.day <= end]
