from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from guard.backtest.engine import run_fixed_weight
from guard.backtest.metrics import rolling_lag
from guard.backtest.sweep import (
    TrialLog,
    TrialResult,
    apply_params,
    expand_grid,
    load_settings,
    rolling_lag_fast,
    run_sweep,
)
from guard.config import load_config
from tests.fixtures.backtest.synthetic import world


@pytest.fixture
def cfg(data_dir: Path):
    return load_config()[0]


def test_expand_grid_is_the_cartesian_product_in_declared_order():
    grid = {"a": [1, 2], "b": ["x", "y", "z"]}
    out = expand_grid(grid)
    assert len(out) == 6
    assert out[0] == {"a": 1, "b": "x"}
    assert out[1] == {"a": 1, "b": "y"}
    assert out[-1] == {"a": 2, "b": "z"}


def test_default_grid_matches_the_declared_axes_and_quick_is_a_small_subset():
    s = load_settings()
    assert s.grid["trend_window"] == [150, 200, 250]
    assert s.grid["weekday"] == [0, 1, 2, 3, 4]
    assert len(expand_grid(s.grid)) == 1620
    quick = expand_grid(s.quick_grid)
    assert 0 < len(quick) <= 40
    for axis, values in s.quick_grid.items():
        assert set(values) <= set(s.grid[axis])


def test_apply_params_overrides_only_what_is_named(cfg):
    sig, lad, bp = apply_params(
        cfg, {"trend_window": 150, "confirmation_runs": 3, "cadence": "monthly", "weekday": 4}
    )
    assert sig.trend_window == 150 and sig.drawdown_threshold == cfg.signals.drawdown_threshold
    assert lad.confirmation_runs == 3
    assert bp.cadence == "monthly" and bp.weekday == 4 and bp.mode == "full"


def test_every_trial_is_logged_and_the_count_equals_the_log_lines(cfg, tmp_path):
    eq, sf = world(n=1500)
    safes = {"cash": sf, "alt": {d: v * 1.01 for d, v in sf.items()}}
    grid = {"trend_window": [150, 200], "drawdown_threshold": [0.08, 0.1], "weekday": [1, 2]}
    log_path = tmp_path / "trials.jsonl"
    trials = run_sweep(eq, safes, grid, cfg, trial_log=log_path, data_version="dv")
    assert len(trials) == 8 * 2
    lines = log_path.read_text().splitlines()
    assert len(lines) == len(trials)
    first = json.loads(lines[0])
    assert {"trial", "stage", "params", "metrics", "manifest_hash"} <= set(first)
    assert {"safe_asset", "trend_window"} <= set(first["params"])
    assert {t.manifest_hash for t in trials} == {json.loads(x)["manifest_hash"] for x in lines}
    assert len({t.manifest_hash for t in trials}) == len(trials)
    assert {
        "cagr",
        "max_drawdown",
        "calmar",
        "sortino",
        "turnover",
        "whipsaws_per_year",
        "time_defensive",
        "worst_lag",
    } <= set(trials[0].metrics)


def test_a_second_sweep_appends_to_the_same_log(cfg, tmp_path):
    eq, sf = world(n=900)
    log_path = tmp_path / "trials.jsonl"
    run_sweep(eq, {"cash": sf}, {"weekday": [2]}, cfg, trial_log=log_path, data_version="dv")
    log = TrialLog(log_path)
    assert log.count == 1
    run_sweep(eq, {"cash": sf}, {"weekday": [2, 3]}, cfg, trial_log=log, data_version="dv")
    assert log.count == 3
    assert len(log_path.read_text().splitlines()) == 3


def test_sweep_is_reproducible(cfg, tmp_path):
    eq, sf = world(n=900)
    a = run_sweep(
        eq, {"cash": sf}, {"weekday": [2, 3]}, cfg, trial_log=tmp_path / "a", data_version="dv"
    )
    b = run_sweep(
        eq, {"cash": sf}, {"weekday": [2, 3]}, cfg, trial_log=tmp_path / "b", data_version="dv"
    )
    assert [t.manifest_hash for t in a] == [t.manifest_hash for t in b]
    assert [t.metrics for t in a] == [t.metrics for t in b]


def test_unknown_axis_or_no_safe_series_is_rejected(cfg, tmp_path):
    eq, sf = world(n=900)
    with pytest.raises(ValueError, match="unknown parameter"):
        run_sweep(eq, {"cash": sf}, {"bogus": [1]}, cfg, trial_log=tmp_path / "t", data_version="d")
    with pytest.raises(ValueError, match="safe"):
        run_sweep(eq, {}, {"weekday": [2]}, cfg, trial_log=tmp_path / "t", data_version="d")


def test_fast_rolling_lag_equals_the_reference_implementation(cfg):
    eq, sf = world(n=1800)
    a = run_fixed_weight(eq, sf, 0.9, schedule="never", data_version="d")
    b = run_fixed_weight(eq, sf, 0.6, schedule="quarterly", data_version="d")
    ref = rolling_lag(a.dates, b.index, a.index, 3)
    fast = rolling_lag_fast(a.dates, b.index, a.index, 3)
    assert [d for d, _ in fast] == [d for d, _ in ref]
    assert [v for _, v in fast] == pytest.approx([v for _, v in ref])
    assert len(ref) > 0


def test_trial_result_roundtrips_to_a_record():
    t = TrialResult({"a": 1}, {"cagr": 0.05, "calmar": None}, "abc")
    rec = t.to_record(3, "sweep")
    assert rec == {
        "trial": 3,
        "stage": "sweep",
        "params": {"a": 1},
        "metrics": {"cagr": 0.05, "calmar": None},
        "manifest_hash": "abc",
    }


def test_env_is_not_needed_for_settings():
    os.environ.pop("GUARD_DATA_DIR", None)
    assert load_settings().targets.annual_lag_pct == 1.0
    assert date  # keep the import used
