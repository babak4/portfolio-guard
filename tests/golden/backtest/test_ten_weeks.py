"""Frozen hand-computed result; the derivation is stored in the fixture and the fixture is never
edited to make the engine pass."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from tests.unit.backtest.helpers import flat_then, trend_only_signals, two_step_ladder, weekdays

from guard.backtest.engine import BacktestParams, run_backtest

FIXTURE = json.loads((Path(__file__).parent / "ten_weeks.json").read_text())


def test_ten_week_example_matches_hand_computation():
    n = FIXTURE["days"]
    dates = weekdays(date.fromisoformat(FIXTURE["start"]), n)
    changes = {int(k): v for k, v in FIXTURE["equity_changes"].items()}
    eq = flat_then(n, changes)
    res = run_backtest(
        dict(zip(dates, eq, strict=True)),
        dict.fromkeys(dates, 100.0),
        signals=trend_only_signals(),
        ladder=two_step_ladder(),
        params=BacktestParams(initial_value=FIXTURE["initial_value"], spread=0.0, drift_band=0.5),
        data_version="golden",
    )
    exp = FIXTURE["expected"]
    assert res.values[-1] == pytest.approx(exp["final_value"], rel=1e-12)
    assert res.sleeves[0].equity == pytest.approx(exp["final_equity"], rel=1e-12)
    assert res.sleeves[0].safe == pytest.approx(exp["final_safe"], rel=1e-12)
    assert res.total_traded == pytest.approx(exp["total_traded"], rel=1e-12)
    assert res.index[-1] == pytest.approx(exp["final_index"], rel=1e-12)
    for k, w in exp["advised"].items():
        assert res.advised_equity[int(k)] == pytest.approx(w)
    for k, v in exp["values"].items():
        assert res.values[int(k)] == pytest.approx(v, rel=1e-12)
    assert len(res.decisions) == exp["decisions"]
    by_kind = {x.kind: d for d, x in res.decisions if x.kind in ("derisk", "reentry")}
    assert by_kind["derisk"] == dates[exp["derisk_day"]]
    assert by_kind["reentry"] == dates[exp["reentry_day"]]
