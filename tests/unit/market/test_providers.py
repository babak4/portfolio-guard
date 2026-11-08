from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from guard.config.models import DataRules
from guard.market import (
    DEFAULT_MACRO_ALIASES,
    FetchResult,
    Listing,
    Observation,
    ProviderError,
    ProviderUnavailable,
    cross_check,
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
    f.write_bytes((FIX / "acwi_preamble_fixture.csv").read_bytes())
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
    f.write_bytes((FIX / "acwi_preamble_fixture.csv").read_bytes())
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


RULES = DataRules(
    live_return_tolerance=0.02,
    jump_threshold=0.15,
    crosscheck_tolerance=0.005,
    crosscheck_min_overlap=0.9,
    stale_trading_days=0,
    max_gap_days=10,
)


def test_fx_series_reaches_two_providers_through_aliases_and_cross_checks() -> None:
    fred = FredProvider(_client(body=(FIX / "fred_dexusuk.csv").read_bytes()))
    frank = FrankfurterProvider(
        _client(body=(FIX / "frankfurter_gbpusd_overlap.json").read_bytes())
    )
    result = fetch_macro(
        "USD_PER_GBP",
        [fred, frank],
        date(2026, 5, 21),
        date(2026, 5, 26),
        aliases=DEFAULT_MACRO_ALIASES,
    )
    assert result.series_id == "USD_PER_GBP"
    assert [s.source_id for s in result.sources] == ["fred", "frankfurter"]
    checked = cross_check(result, RULES)
    assert checked.series_id == "USD_PER_GBP"
    assert checked.sources == ("fred", "frankfurter")


def test_alias_requests_use_each_providers_own_series_id() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.params["id"])
        return httpx.Response(200, content=(FIX / "fred_dexusuk.csv").read_bytes())

    fred = FredProvider(_client(httpx.MockTransport(handler)))
    fetch_macro("RATE", [fred], START, END, aliases={"RATE": {"fred": "DEXUSUK", "other": "ZZZ"}})
    assert seen == ["DEXUSUK"]
    assert fetch_macro("USD_PER_GBP", [fred], START, END).sources == []  # no aliases given


def test_yahoo_parse_handles_pence_blank_nan_and_bad_date() -> None:
    from guard.market.providers.yahoo import parse_csv

    pence = Listing.from_listing_id("VWRP:XLON", "GBP", quoted_in_minor_units=True)
    body = b"Date,Close\n2026-05-20,10050\n2026-05-21,\n2026-05-22,nan\n2026-05-23,NaN\n"
    assert parse_csv(body, pence) == [Observation(date(2026, 5, 20), Decimal("100.5"))]
    with pytest.raises(ProviderError):
        parse_csv(b"Date,Close\nnot-a-date,1\n", VWRP)
    with pytest.raises(ProviderError):
        parse_csv(b"Foo,Bar\n", VWRP)


def _fake_yfinance(monkeypatch: pytest.MonkeyPatch, frame: object, calls: list[dict]) -> None:
    import sys
    import types

    class Ticker:
        def __init__(self, symbol: str) -> None:
            calls.append({"symbol": symbol})

        def history(self, **kwargs: object) -> object:
            calls.append(kwargs)
            if isinstance(frame, Exception):
                raise frame
            return frame

    module = types.ModuleType("yfinance")
    module.Ticker = Ticker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", module)


def test_default_downloader_converts_history_to_csv_with_inclusive_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pandas as pd

    from guard.market.providers.yahoo import default_downloader

    frame = pd.DataFrame(
        {"Close": [100.5, float("nan"), 102.0]},
        index=pd.to_datetime(["2026-05-20", "2026-05-21", "2026-05-22"]),
    )
    calls: list[dict] = []
    _fake_yfinance(monkeypatch, frame, calls)
    body = default_downloader("VWRP.L", START, END)
    assert calls[0] == {"symbol": "VWRP.L"}
    assert calls[1]["start"] == "2026-05-20" and calls[1]["end"] == "2026-05-23"
    assert body.startswith(b"Date,Close\n2026-05-20,100.5\n")
    # The NaN row is dropped when parsed instead of poisoning the whole source.
    got = YahooProvider(default_downloader).daily_closes(VWRP, START, END)
    assert [o.value for o in got] == [Decimal("100.5"), Decimal("102.0")]


def test_default_downloader_maps_exceptions_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from guard.market.providers.yahoo import default_downloader

    _fake_yfinance(monkeypatch, RuntimeError("boom"), [])
    with pytest.raises(ProviderError, match="download failed"):
        default_downloader("VWRP.L", START, END)


def test_stooq_row_with_missing_close_falls_through_to_next_provider() -> None:
    body = b"Date,Open,High,Low,Close,Volume\n2026-05-20,1,1,1\n"
    first = StooqProvider(_client(body=body))
    second = YahooProvider(lambda s, a, b: (FIX / "yahoo_vwrp.csv").read_bytes())
    result = fetch_prices(VWRP, [first, second], START, END)
    assert [s.source_id for s in result.sources] == ["yahoo"]
    assert "stooq" in result.errors


def test_frankfurter_malformed_date_and_payload_fall_through() -> None:
    fred = FredProvider(_client(body=(FIX / "fred_dexusuk.csv").read_bytes()))
    for body in (
        b'{"rates":{"not-a-date":{"USD":1.3}}}',
        b'{"rates":[]}',
        b'{"rates":{"2026-05-20":5}}',
    ):
        frank = FrankfurterProvider(_client(body=body))
        r = fetch_macro(
            "USD_PER_GBP",
            [frank, fred],
            date(2026, 5, 21),
            date(2026, 5, 26),
            aliases={"USD_PER_GBP": {"frankfurter": "GBPUSD", "fred": "DEXUSUK"}},
        )
        assert [s.source_id for s in r.sources] == ["fred"]
        assert "frankfurter" in r.errors


def test_non_utf8_body_falls_through() -> None:
    first = StooqProvider(_client(body=b"Date,Close\n\xff\xfe,1\n"))
    second = YahooProvider(lambda s, a, b: (FIX / "yahoo_vwrp.csv").read_bytes())
    result = fetch_prices(VWRP, [first, second], START, END)
    assert [s.source_id for s in result.sources] == ["yahoo"]
    assert "stooq" in result.errors


def test_frankfurter_chunks_long_ranges_without_gaps_or_duplicates() -> None:
    from datetime import timedelta

    start, end = date(2026, 1, 1), date(2026, 7, 19)  # 200 days
    requests: list[tuple[date, date]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        lo, hi = (date.fromisoformat(x) for x in req.url.path.rsplit("/", 1)[1].split(".."))
        requests.append((lo, hi))
        days = [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]
        rates = {d.isoformat(): {"USD": 1.3} for d in days}
        return httpx.Response(200, json={"rates": rates})

    p = FrankfurterProvider(_client(httpx.MockTransport(handler)))
    obs = p.series("GBPUSD", start, end)
    assert len(requests) == 3
    assert requests[0][0] == start and requests[-1][1] == end
    for (_, prev_hi), (nxt_lo, _) in zip(requests, requests[1:], strict=False):
        assert nxt_lo == prev_hi + timedelta(days=1)
    assert all((hi - lo).days + 1 <= 90 for lo, hi in requests)
    days = [o.day for o in obs]
    assert len(days) == len(set(days)) == 200
    assert days == sorted(days)
