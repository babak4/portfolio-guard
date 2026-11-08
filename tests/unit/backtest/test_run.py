from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from guard.backtest import run as run_mod
from guard.backtest.run import (
    Loaders,
    main,
    resample_to_days,
)
from guard.market.base import Observation, ProviderUnavailable
from guard.market.providers.fred import SERIES
from tests.fixtures.backtest.synthetic import observations, world
from tests.unit.backtest.helpers import weekdays


def test_fred_series_include_the_uk_short_rate():
    assert "IR3TIB01GBM156N" in SERIES


def test_resample_interpolates_monthly_levels_onto_trading_days():
    pts = {date(2020, 1, 1): 100.0, date(2020, 2, 1): 110.0}
    days = [
        date(2019, 12, 30),
        date(2020, 1, 1),
        date(2020, 1, 16),
        date(2020, 2, 1),
        date(2020, 2, 10),
    ]
    out = resample_to_days(pts, days)
    assert date(2019, 12, 30) not in out and date(2020, 2, 10) not in out
    assert out[date(2020, 1, 1)] == 100.0 and out[date(2020, 2, 1)] == pytest.approx(110.0)
    assert 104.0 < out[date(2020, 1, 16)] < 106.0


def no_msci_loaders() -> Loaders:
    def series(source: str):
        raise ProviderUnavailable(f"{source} unavailable")

    return Loaders(series=series, usd_per_gbp=lambda: [])


def test_cli_stops_with_the_msci_message_when_no_equity_series(data_dir, tmp_path, capsys):
    code = main(["--quick", "--out", str(tmp_path / "r.md")], loaders=no_msci_loaders())
    err = capsys.readouterr().err
    assert code == 2
    assert "msci_acwi_nr.csv" in err and str(data_dir / "inputs") in err
    assert not (tmp_path / "r.md").exists()


def synthetic_loaders() -> Loaders:
    days_all = weekdays(date(2007, 1, 1), 4200)
    eq, sf = world(n=4200)
    fx = {d: 1.25 for d in days_all}
    eq_gbp_etf = {d: v for d, v in eq.items() if d >= date(2017, 1, 2)}
    msci_usd = {d: v * 1.25 for d, v in eq.items()}
    csh2 = {d: v for d, v in sf.items() if d >= date(2013, 1, 1)}
    rates = [
        Observation(d, Decimal("2.0")) for d in sorted({date(d.year, d.month, 1) for d in days_all})
    ]

    table = {
        "vwrp_gbp": observations(eq_gbp_etf),
        "msci_acwi_nr_usd": observations(msci_usd),
        "csh2_gbp": observations(csh2),
        "sonia_rate_pct": rates,
    }

    def series(source: str):
        if source in table:
            return table[source]
        raise ProviderUnavailable(f"{source} unavailable")

    return Loaders(series=series, usd_per_gbp=lambda: observations(fx))


def test_cli_end_to_end_on_synthetic_loaders_writes_report_log_and_manifest(data_dir, tmp_path):
    out = tmp_path / "docs" / "phase0-report.md"
    code = main(["--quick", "--out", str(out)], loaders=synthetic_loaders())
    assert code == 0
    md = out.read_text()
    assert "Draft: proxy data" in md
    assert "Decisions for the owner" in md
    log = data_dir / "backtests" / "trials.jsonl"
    n = len(log.read_text().splitlines())
    assert n > 20 and f"Trials recorded: {n}" in md
    manifest = json.loads((data_dir / "backtests" / "manifest.json").read_text())
    assert manifest["trial_count"] == n
    assert manifest["data_version"] and manifest["config_version"]
    assert {"parameters", "code_version"} <= set(manifest)


def test_weekday_option_is_validated(data_dir, tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["--weekday", "9"], loaders=no_msci_loaders())
    assert timedelta and run_mod
