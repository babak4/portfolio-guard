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
    run_study,
)
from guard.market.base import Observation, ProviderUnavailable
from guard.market.providers.fred import SERIES
from tests.fixtures.backtest.synthetic import observations, world
from tests.unit.backtest.helpers import weekdays


def test_fred_series_include_the_uk_short_rate():
    assert "IR3TIB01GBM156N" in SERIES


def test_resample_holds_the_last_observed_level_until_the_next_observation():
    pts = {date(2020, 1, 1): 100.0, date(2020, 2, 1): 110.0}
    days = [
        date(2019, 12, 30),
        date(2020, 1, 1),
        date(2020, 1, 16),
        date(2020, 1, 31),
        date(2020, 2, 1),
        date(2020, 2, 10),
    ]
    out = resample_to_days(pts, days)
    assert date(2019, 12, 30) not in out and date(2020, 2, 10) not in out
    assert out[date(2020, 1, 16)] == 100.0 and out[date(2020, 1, 31)] == 100.0
    assert out[date(2020, 2, 1)] == 110.0


def test_resample_never_depends_on_observations_after_the_day():
    days = [date(2020, 1, 1) + timedelta(days=k) for k in range(60)]
    a = {date(2020, 1, 1): 100.0, date(2020, 2, 1): 110.0, date(2020, 3, 1): 120.0}
    b = {**a, date(2020, 2, 1): 500.0, date(2020, 3, 1): 7.0}
    ra, rb = resample_to_days(a, days), resample_to_days(b, days)
    for d in days:
        if d < date(2020, 2, 1) and d in ra:
            assert ra[d] == rb[d]


def test_carried_forward_share_counts_days_without_an_observation():
    from guard.backtest.run import carried_forward_share

    pts = {date(2020, 1, 1): 1.0, date(2020, 1, 3): 2.0}
    days = [date(2020, 1, d) for d in (1, 2, 3)]
    assert carried_forward_share(pts, days) == pytest.approx(1 / 3)


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


# ---------------------------------------------------------------- review fixes


def small_study(tmp_path, n=3300, **kw):
    from guard.backtest.sweep import TrialLog, load_settings
    from guard.config import load_config
    from tests.fixtures.backtest.synthetic import history

    cfg, version = load_config()
    eq, sf = world(n=n)
    log = TrialLog(tmp_path / "t.jsonl", reset=True)
    inputs = run_study(
        history(eq, None, "vwrp"),
        {"money_market": history(sf, None, "csh2")},
        cfg,
        version,
        load_settings(),
        quick=True,
        weekday=2,
        trial_log=log,
        **kw,
    )
    return inputs, log


def test_trial_count_equals_the_number_of_run_trial_calls(tmp_path, data_dir, monkeypatch):
    from guard.backtest import sweep as sweep_mod
    from guard.backtest import validation as val_mod

    calls = []
    real = sweep_mod.run_trial

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    for mod in (run_mod, sweep_mod, val_mod):
        monkeypatch.setattr(mod, "run_trial", counting)
    inputs, log = small_study(tmp_path)
    assert len(calls) == log.count == log.session_count == inputs.runs_this_invocation
    assert inputs.cumulative_runs == log.count


def test_runs_accumulate_across_invocations_until_fresh(data_dir, tmp_path):
    out = tmp_path / "r.md"
    manifest = data_dir / "backtests" / "manifest.json"
    log = data_dir / "backtests" / "trials.jsonl"
    assert main(["--quick", "--out", str(out)], loaders=synthetic_loaders()) == 0
    n = len(log.read_text().splitlines())
    first_distinct = len(
        {json.dumps(json.loads(x)["params"], sort_keys=True) for x in log.read_text().splitlines()}
    )
    assert json.loads(manifest.read_text())["cumulative_runs"] == n
    assert main(["--quick", "--out", str(out)], loaders=synthetic_loaders()) == 0
    assert len(log.read_text().splitlines()) == 2 * n
    m2 = json.loads(manifest.read_text())
    assert m2["cumulative_runs"] == 2 * n and m2["runs_this_invocation"] == n
    md = out.read_text()
    assert f"Runs this invocation: {n}" in md
    assert f"Cumulative runs including earlier invocations: {2 * n}" in md
    assert f"Distinct parameter combinations in the whole log: {first_distinct}" in md
    assert main(["--quick", "--fresh", "--out", str(out)], loaders=synthetic_loaders()) == 0
    assert len(log.read_text().splitlines()) == n
    assert json.loads(manifest.read_text())["cumulative_runs"] == n


def test_only_insufficient_history_becomes_a_missing_walk_forward(tmp_path, data_dir, monkeypatch):
    inputs, _ = small_study(tmp_path, n=1500)
    assert inputs.walk_forward is None and "too short" in inputs.walk_forward_note

    def boom(*a, **k):
        raise ValueError("boom: a real bug")

    monkeypatch.setattr(run_mod, "walk_forward", boom)
    with pytest.raises(ValueError, match="boom"):
        small_study(tmp_path, n=3300)


def test_benchmarks_run_on_the_strategys_own_parameters(tmp_path, data_dir):
    inputs, _ = small_study(tmp_path)
    strat = inputs.strategies["config defaults"].parameters
    refs = [
        *inputs.benchmarks.values(),
        inputs.single_tracker,
        inputs.buy_and_hold,
        inputs.floor_only,
        inputs.rebalanced_reference,
    ]
    assert inputs.rebalanced_reference is not None
    for b in refs:
        for k in ("spread", "drift_band", "weekday", "initial_value"):
            assert b.parameters[k] == strat[k], (b.parameters.get("benchmark"), k)
    ref = inputs.rebalanced_reference
    assert ref.parameters["schedule"] == "decision_days"
    assert ref.parameters["equity_weight"] == pytest.approx(0.9)
