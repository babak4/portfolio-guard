from __future__ import annotations

from datetime import date

import pytest

from guard.backtest.sweep import TrialResult, load_settings
from guard.backtest.validation import (
    Range,
    WeekdayOffsetResult,
    choose_parameters,
    plateau_score,
    plateau_table,
    propose_plateau_centre,
    ranges_by,
    walk_forward,
    walk_forward_windows,
    wednesday_verdict,
    weekday_offsets,
)
from guard.config import load_config
from tests.fixtures.backtest.synthetic import world


def trial(x, y, calmar, dd, lag):
    return TrialResult(
        {"trend_window": x, "drawdown_threshold": y},
        {"calmar": calmar, "max_drawdown": dd, "worst_lag": lag},
        f"{x}-{y}",
    )


def surface():
    """A broad plateau (x 150-250 by y .08-.15, except a ridge) plus one lucky isolated spike."""
    xs = [100, 150, 200, 250, 300]
    ys = [0.05, 0.08, 0.10, 0.15, 0.20]
    out = []
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            if 150 <= x <= 250 and 0.08 <= y <= 0.15:
                out.append(trial(x, y, 0.60, -0.20, 0.50))
            elif (i + j) % 2 == 0:  # the rest is a chequerboard of poor, unstable results
                out.append(trial(x, y, 0.10, -0.40, 3.0))
            else:
                out.append(trial(x, y, 0.10, -0.80, 6.0))
    # the single best point is a spike whose neighbours are all bad
    out = [
        t for t in out if (t.params["trend_window"], t.params["drawdown_threshold"]) != (100, 0.05)
    ]
    out.append(trial(100, 0.05, 2.0, -0.10, 0.1))
    return out


def test_plateau_table_has_one_cell_per_grid_point():
    tbl = plateau_table(surface(), "trend_window", "drawdown_threshold", "calmar")
    assert tbl.xs == [100, 150, 200, 250, 300]
    assert tbl.ys == [0.05, 0.08, 0.10, 0.15, 0.20]
    assert tbl.values[0][0] == 2.0 and tbl.values[2][2] == 0.60


def test_plateau_score_is_the_share_of_neighbours_within_tolerance():
    ts = surface()
    s = load_settings().plateau
    inside = plateau_score(
        ts,
        "trend_window",
        "drawdown_threshold",
        {"trend_window": 200, "drawdown_threshold": 0.10},
        s.tolerance,
        s.floors,
    )
    spike = plateau_score(
        ts,
        "trend_window",
        "drawdown_threshold",
        {"trend_window": 100, "drawdown_threshold": 0.05},
        s.tolerance,
        s.floors,
    )
    edge = plateau_score(
        ts,
        "trend_window",
        "drawdown_threshold",
        {"trend_window": 150, "drawdown_threshold": 0.08},
        s.tolerance,
        s.floors,
    )
    assert inside == 1.0
    assert spike == 0.0
    assert edge == pytest.approx(3 / 8)


def test_plateau_centre_differs_from_the_single_best_point():
    s = load_settings().plateau
    p = propose_plateau_centre(
        surface(), "trend_window", "drawdown_threshold", tolerance=s.tolerance, floors=s.floors
    )
    assert p.best_point == (100, 0.05)
    assert (p.x, p.y) == (200, 0.10)
    assert p.size == 9
    assert p.params == {"trend_window": 200, "drawdown_threshold": 0.10}
    assert p.score == 1.0


def test_plateau_respects_fixed_parameters():
    ts = []
    for other in ("a", "b"):
        for t in surface():
            ts.append(TrialResult({**t.params, "mode": other}, t.metrics, t.manifest_hash + other))
    s = load_settings().plateau
    p = propose_plateau_centre(
        ts,
        "trend_window",
        "drawdown_threshold",
        fixed={"mode": "a"},
        tolerance=s.tolerance,
        floors=s.floors,
    )
    assert (p.x, p.y) == (200, 0.10)


def test_choose_parameters_breaks_ties_toward_the_default():
    default = {"a": 1, "b": 1}
    scored = [({"a": 3, "b": 3}, 1.00), ({"a": 1, "b": 3}, 0.995), ({"a": 1, "b": 1}, 0.99)]
    chosen, score = choose_parameters(scored, default, tie_tolerance=0.02)
    assert chosen == {"a": 1, "b": 1} and score == 0.99
    chosen, _ = choose_parameters(scored, default, tie_tolerance=0.001)
    assert chosen == {"a": 3, "b": 3}
    chosen, score = choose_parameters([({"a": 2}, None), ({"a": 5}, 0.3)], {"a": 1}, 0.0)
    assert chosen == {"a": 5}


def test_walk_forward_windows_roll_without_overlap():
    w = walk_forward_windows(
        date(2000, 1, 3), date(2025, 1, 3), train_years=8, test_years=3, min_test_days=180
    )
    assert w[0] == (date(2008, 1, 3), date(2011, 1, 3))
    for (_, test_end), (next_train_end, _) in zip(w, w[1:], strict=False):
        assert next_train_end == test_end
    assert w[-1][1] <= date(2025, 1, 3)
    short = walk_forward_windows(
        date(2000, 1, 3), date(2011, 1, 20), train_years=8, test_years=3, min_test_days=180
    )
    assert short == [(date(2008, 1, 3), date(2011, 1, 3))]


WF_GRID = {"trend_window": [100, 200], "drawdown_threshold": [0.08, 0.15]}


def test_walk_forward_test_windows_never_overlap_training(tmp_path, data_dir):
    cfg = load_config()[0]
    eq, sf = world(n=3300)
    s = load_settings().walk_forward
    res = walk_forward(eq, sf, WF_GRID, cfg, settings=s, data_version="d")
    assert len(res.folds) >= 2
    prev_end = None
    for f in res.folds:
        assert f.train_start < f.train_end < f.test_start <= f.test_end
        if prev_end is not None:
            assert f.test_start > prev_end  # test blocks are disjoint
        prev_end = f.test_end
    assert res.dates[0] == res.folds[0].train_end or res.dates[0] < res.folds[0].test_start
    assert res.index[0] == 1.0 and res.benchmark_index[0] == 1.0
    assert len(res.dates) == len(res.index) == len(res.benchmark_index)
    assert {"cagr", "max_drawdown", "calmar"} <= set(res.metrics)
    assert {"cagr", "max_drawdown"} <= set(res.benchmark_metrics)
    assert all(d1 < d2 for d1, d2 in zip(res.dates, res.dates[1:], strict=False))


def test_first_fold_choice_cannot_depend_on_data_after_its_training_window(data_dir):
    cfg = load_config()[0]
    eq, sf = world(n=3300)
    s = load_settings().walk_forward
    a = walk_forward(eq, sf, WF_GRID, cfg, settings=s, data_version="d")
    cut = a.folds[0].train_end
    eq2 = {d: (v if d <= cut else v * 0.5) for d, v in eq.items()}  # a different future
    b = walk_forward(eq2, sf, WF_GRID, cfg, settings=s, data_version="d")
    assert a.folds[0].params == b.folds[0].params
    assert a.folds[0].train_score == b.folds[0].train_score


def test_walk_forward_logs_its_training_trials(tmp_path, data_dir):
    from guard.backtest.sweep import TrialLog

    cfg = load_config()[0]
    eq, sf = world(n=3300)
    log = TrialLog(tmp_path / "t.jsonl")
    res = walk_forward(
        eq, sf, WF_GRID, cfg, settings=load_settings().walk_forward, data_version="d", trial_log=log
    )
    assert log.count == (4 + 1) * len(res.folds)  # each fold: 4 training trials and its test run


def test_weekday_ranges_and_wednesday_verdict_on_constructed_results():
    def runs(day, values):
        return [(day, k, {"calmar": v, "max_drawdown": -0.2}) for k, v in enumerate(values)]

    base = (
        runs(0, [0.50, 0.52])
        + runs(1, [0.49, 0.53])
        + runs(3, [0.51, 0.54])
        + runs(4, [0.50, 0.55])
    )
    wed = runs(2, [0.48, 0.58, 0.53])  # spread 0.10
    res = WeekdayOffsetResult.from_runs(base + wed)
    assert res.per_weekday[2]["calmar"] == Range(0.48, 0.53, 0.58)
    assert res.overall["calmar"].min == 0.48 and res.overall["calmar"].max == 0.58
    v = wednesday_verdict(res, "calmar")
    assert v.wednesday_stays and v.beaten_by == []
    assert "Wednesday stays" in v.text

    thursday = runs(3, [0.90, 0.92])
    v2 = wednesday_verdict(WeekdayOffsetResult.from_runs(base[:4] + thursday + wed), "calmar")
    assert not v2.wednesday_stays and v2.beaten_by == [3]
    assert "Thursday" in v2.text

    slightly = runs(
        3, [0.60, 0.62]
    )  # median 0.61 beats 0.53 by 0.08, inside Wednesday's 0.10 spread
    v3 = wednesday_verdict(WeekdayOffsetResult.from_runs(slightly + wed), "calmar")
    assert v3.wednesday_stays


def test_ranges_by_groups_trials_by_a_parameter():
    ts = [
        TrialResult({"cadence": "weekly"}, {"calmar": 0.4}, "1"),
        TrialResult({"cadence": "weekly"}, {"calmar": 0.6}, "2"),
        TrialResult({"cadence": "monthly"}, {"calmar": 0.3}, "3"),
        TrialResult({"cadence": "monthly"}, {"calmar": None}, "4"),
    ]
    r = ranges_by(ts, "cadence", "calmar")
    assert r["weekly"] == Range(0.4, 0.5, 0.6)
    assert r["monthly"] == Range(0.3, 0.3, 0.3)


def test_weekday_offsets_runs_every_weekday_and_offset_and_logs_them(tmp_path, data_dir):
    from guard.backtest.sweep import TrialLog

    cfg = load_config()[0]
    eq, sf = world(n=1500)
    log = TrialLog(tmp_path / "t.jsonl")
    res = weekday_offsets(eq, sf, cfg, offsets=2, data_version="d", trial_log=log)
    assert sorted({w for w, _, _ in res.runs}) == [0, 1, 2, 3, 4]
    assert len(res.runs) == 10
    assert log.count == 10
    assert set(res.per_weekday) == {0, 1, 2, 3, 4}
