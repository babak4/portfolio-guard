from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from guard.market import (
    FetchResult,
    Listing,
    Observation,
    ProviderError,
    ProviderUnavailable,
    fetch_macro,
    fetch_prices,
)
from guard.market.providers.frankfurter import FrankfurterProvider
from guard.market.providers.fred import FredProvider, parse_fredgraph_csv
from guard.market.providers.msci_file import SERIES_ID, MsciFileProvider
from guard.market.providers.stooq import StooqProvider
from guard.market.providers.yahoo import YahooProvider

FIX = Path(__file__).parents[2] / "fixtures" / "market"
START, END = date(2026, 5, 20), date(2026, 5, 22)
VWRP = Listing.from_listing_id("VWRP:XLON", "GBP")


def _client(handler: httpx.MockTransport | None = None, *, body: bytes = b"") -> httpx.Client:
    return httpx.Client(
        transport=handler or httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )


def test_listing_derives_symbols() -> None:
    assert VWRP.yahoo_symbol == "VWRP.L" and VWRP.stooq_symbol == "vwrp.uk"
    with pytest.raises(ValueError):
        Listing.from_listing_id("VWRP", "GBP")


def test_stooq_parses_and_scales_pence() -> None:
    p = StooqProvider(_client(body=(FIX / "stooq_vwrp.csv").read_bytes()))
    assert [o.value for o in p.daily_closes(VWRP, START, END)] == [
        Decimal("100.50"),
        Decimal("101.25"),
        Decimal("102.00"),
    ]
    pence = Listing.from_listing_id("VWRP:XLON", "GBP", quoted_in_minor_units=True)
    assert p.daily_closes(pence, START, END)[0].value == Decimal("1.005")


def test_stooq_no_data_is_empty() -> None:
    assert StooqProvider(_client(body=b"No data")).daily_closes(VWRP, START, END) == []


def test_yahoo_uses_injected_downloader_and_sink() -> None:
    calls: list[str] = []
    seen: list[tuple[str, str, bytes]] = []
    payload = (FIX / "yahoo_vwrp.csv").read_bytes()

    def download(symbol: str, start: date, end: date) -> bytes:
        calls.append(symbol)
        return payload

    p = YahooProvider(download, sink=lambda s, n, b: seen.append((s, n, b)))
    assert p.daily_closes(VWRP, START, END)[-1].value == Decimal("102.01")
    assert calls == ["VWRP.L"] and seen[0][0] == "yahoo" and seen[0][2] == payload


def test_yahoo_without_yfinance_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "yfinance", None)
    with pytest.raises(ProviderUnavailable):
        YahooProvider().daily_closes(VWRP, START, END)


def test_frankfurter_parses_rates() -> None:
    p = FrankfurterProvider(_client(body=(FIX / "frankfurter_gbpusd.json").read_bytes()))
    assert p.supports_series("GBPUSD") and not p.supports_series("DEXUSUK")
    assert [o.value for o in p.series("GBPUSD", START, END)] == [
        Decimal("1.35"),
        Decimal("1.351"),
        Decimal("1.3525"),
    ]


def test_fred_forward_fills_holiday_nan_and_flags_it() -> None:
    obs = parse_fredgraph_csv((FIX / "fred_dexusuk.csv").read_bytes(), "DEXUSUK")
    assert [o.day.isoformat() for o in obs] == [
        "2026-05-21",
        "2026-05-22",
        "2026-05-25",
        "2026-05-26",
    ]
    assert obs[2] == Observation(date(2026, 5, 25), Decimal("1.3525"), filled=True)
    assert [o.filled for o in obs] == [False, False, True, False]


def test_fred_leading_gap_is_skipped_and_old_header_accepted() -> None:
    obs = parse_fredgraph_csv((FIX / "fred_leading_gap.csv").read_bytes(), "VIXCLS")
    assert obs == [Observation(date(2026, 1, 2), Decimal("16.5"))]


def test_fred_rejects_wrong_header_and_unsupported_series() -> None:
    with pytest.raises(ProviderError):
        parse_fredgraph_csv(b"a,b\n1,2\n", "DEXUSUK")
    with pytest.raises(ProviderError):
        FredProvider(_client()).series("NOPE", START, END)


def test_fred_requests_series_id() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=(FIX / "fred_dexusuk.csv").read_bytes())

    FredProvider(_client(httpx.MockTransport(handler))).series(
        "DEXUSUK", date(2026, 5, 21), date(2026, 5, 26)
    )
    assert seen[0].url.params["id"] == "DEXUSUK"


def test_msci_file_loads_csv_with_preamble(tmp_path: Path) -> None:
    f = tmp_path / "msci.csv"
    f.write_bytes((FIX / "msci_acwi.csv").read_bytes())
    obs = MsciFileProvider(f).series(SERIES_ID, date(2026, 1, 1), date(2026, 12, 31))
    assert obs == [
        Observation(date(2026, 5, 20), Decimal("1234.50")),
        Observation(date(2026, 5, 21), Decimal("1240.25")),
    ]


def test_msci_file_absent_gives_clear_error(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GUARD_MSCI_FILE", raising=False)
    with pytest.raises(ProviderUnavailable, match="MSCI file not found"):
        MsciFileProvider().series(SERIES_ID, START, END)


def test_msci_file_path_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "x.csv"
    f.write_bytes((FIX / "msci_acwi.csv").read_bytes())
    monkeypatch.setenv("GUARD_MSCI_FILE", str(f))
    assert len(MsciFileProvider().series(SERIES_ID, date(2026, 1, 1), date(2026, 12, 31))) == 2


@pytest.mark.parametrize("failure", ["429", "timeout"])
def test_failing_provider_falls_through_and_log_has_no_secrets(
    failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(429)

    first = StooqProvider(_client(httpx.MockTransport(handler)))
    second = YahooProvider(lambda s, a, b: (FIX / "yahoo_vwrp.csv").read_bytes())
    with caplog.at_level(logging.INFO, logger="guard.market"):
        result = fetch_prices(VWRP, [first, second], START, END)
    assert [s.source_id for s in result.sources] == ["yahoo"]
    assert "stooq" in result.errors
    text = caplog.text
    assert "stooq" in text and "vwrp.uk" not in text and "://" not in text


def test_fetch_stops_after_max_sources_and_records_empty() -> None:
    good = lambda s, a, b: (FIX / "yahoo_vwrp.csv").read_bytes()  # noqa: E731
    empty = StooqProvider(_client(body=b"No data"))
    result = fetch_prices(
        VWRP, [empty, YahooProvider(good), YahooProvider(good)], START, END, max_sources=1
    )
    assert isinstance(result, FetchResult)
    assert result.errors == {"stooq": "no data returned"}
    assert len(result.sources) == 1


def test_fetch_macro_skips_unsupported_series() -> None:
    fred = FredProvider(_client(body=(FIX / "fred_dexusuk.csv").read_bytes()))
    frank = FrankfurterProvider(_client(body=(FIX / "frankfurter_gbpusd.json").read_bytes()))
    r = fetch_macro("DEXUSUK", [frank, fred], date(2026, 5, 21), date(2026, 5, 26))
    assert [s.source_id for s in r.sources] == ["fred"] and r.errors == {}
