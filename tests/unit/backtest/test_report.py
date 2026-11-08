from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from guard.backtest.report import render_phase0_report
from guard.backtest.run import run_study
from guard.backtest.sweep import TrialLog, load_settings
from guard.config import load_config
from guard.market.history import History
from tests.fixtures.backtest.synthetic import history, world

SECTIONS = [
    "Summary of what the numbers say",
    "Crisis frontier",
    "Rolling 3-year lag",
    "Benchmark comparison",
    "Full ladder versus realistic",
    "Safe-asset comparison",
    "Weekday and cadence",
    "Plateau tables",
    "Walk-forward",
    "Whipsaws and time defensive",
    "Data",
    "Trials",
    "Decisions for the owner",
]


def study(
    tmp_path: Path,
    proxy_until: date | None,
    *,
    n: int = 4200,
    start: date = date(2007, 1, 1),
    settings=None,
):
    cfg, version = load_config()
    eq, sf = world(n=n, start=start)
    gold = {d: v * 1.3 for d, v in sf.items()}
    log = TrialLog(tmp_path / "trials.jsonl", reset=True)
    inputs = run_study(
        history(eq, proxy_until, "vwrp"),
        {"money_market": history(sf, None, "csh2"), "gold": history(gold, None, "igln")},
        cfg,
        version,
        settings or load_settings(),
        quick=True,
        weekday=2,
        trial_log=log,
    )
    return inputs, log


@pytest.fixture(scope="module")
def draft(tmp_path_factory, data_dir_module):
    return study(tmp_path_factory.mktemp("r"), date(2012, 1, 1))


@pytest.fixture(scope="module")
def data_dir_module(tmp_path_factory):
    import os

    d = tmp_path_factory.mktemp("gd") / "data"
    old = os.environ.get("GUARD_DATA_DIR")
    os.environ["GUARD_DATA_DIR"] = str(d)
    yield d
    if old is None:
        os.environ.pop("GUARD_DATA_DIR")
    else:
        os.environ["GUARD_DATA_DIR"] = old


def test_report_has_every_section_in_order(draft):
    inputs, _ = draft
    md = render_phase0_report(inputs)
    positions = [md.index("\n## " + s) for s in SECTIONS]
    assert positions == sorted(positions)
    assert md.lstrip().startswith("# ")


def test_report_states_trial_count_exactly(draft):
    inputs, log = draft
    md = render_phase0_report(inputs)
    assert log.count > 20
    assert f"Trials recorded: {log.count}" in md
    assert inputs.trial_count == log.count


def test_summary_states_the_prd_targets_with_numbers(draft):
    md = render_phase0_report(draft[0])
    summary = md.split("Summary of what the numbers say")[1].split("\n## ")[0]
    cap = round(draft[0].settings.targets.crisis_max_drawdown * 100)
    assert f"-{cap}%" in summary
    assert "1%" in summary
    assert ("met" in summary) or ("not met" in summary) or ("Not met" in summary)
    assert "2008" in summary


def test_draft_banner_when_history_has_proxy_points(draft):
    md = render_phase0_report(draft[0])
    assert "Draft: proxy data" in md
    assert md.index("Draft: proxy data") < md.index("Summary of what the numbers say")


def test_no_banner_without_proxy_points(tmp_path, data_dir):
    inputs, _ = study(tmp_path, None)
    md = render_phase0_report(inputs)
    assert "Draft: proxy data" not in md
    assert "0.0%" in md  # the proxy share is still stated


def test_frontier_and_arithmetic_and_decisions_content(draft):
    md = render_phase0_report(draft[0])
    frontier = md.split("Crisis frontier")[1].split("\n## ")[0]
    assert "floor" in frontier.lower() and "50%" in frontier
    assert "buy-and-hold" in frontier.lower()
    decisions = md.split("Decisions for the owner")[1]
    assert "floor" in decisions.lower() and "cap" in decisions.lower()
    assert "GIA" in decisions
    assert "cooldown" in decisions.lower() and "confirmation" in decisions.lower()


def test_data_section_gives_proxy_share_and_provenance_by_source(draft):
    md = render_phase0_report(draft[0])
    data = md.split("## Data")[1].split("\n## ")[0]
    assert "proxy" in data and "stand_in" in data and "vwrp" in data
    assert "ETF-era" in data
    assert "%" in data


# ---------------------------------------------------------------- review fixes


def section(md: str, title: str) -> str:
    return md.split("\n## " + title)[1].split("\n## ")[0]


def tweaked(draft_inputs, **changes):
    return replace(draft_inputs, **changes)


@pytest.fixture(scope="module")
def mid_2008(tmp_path_factory, data_dir_module):
    return study(tmp_path_factory.mktemp("m"), None, n=4200, start=date(2008, 7, 1))[0]


@pytest.fixture(scope="module")
def no_2008(tmp_path_factory, data_dir_module):
    return study(tmp_path_factory.mktemp("n"), None, n=3800, start=date(2010, 1, 4))[0]


@pytest.mark.parametrize("fixture", ["mid_2008", "no_2008"])
def test_uncovered_crisis_window_is_not_tested_and_never_blanket_met(fixture, request):
    md = render_phase0_report(request.getfixturevalue(fixture))
    assert "Both targets are met." not in md
    for title in ("Summary of what the numbers say", "Crisis frontier", "Decisions for the owner"):
        assert "NOT TESTED: 2008-2009" in section(md, title), title
    summary = section(md, "Summary of what the numbers say")
    assert "not tested" in summary.lower()
    assert "met in the windows tested" in summary or "not met" in summary


def test_fully_covered_history_has_no_not_tested_note(draft):
    assert "NOT TESTED" not in render_phase0_report(draft[0])


def test_proxy_share_per_window_is_time_weighted_and_split_by_leg(draft):
    md = render_phase0_report(draft[0])
    frontier = section(md, "Crisis frontier")
    assert "Equity leg proxy" in frontier and "Safe leg proxy" in frontier
    row_2008 = next(r for r in frontier.splitlines() if r.startswith("| config defaults | 2008"))
    row_2020 = next(r for r in frontier.splitlines() if r.startswith("| config defaults | 2020"))
    assert "100%" in row_2008 and "0%" in row_2008  # equity proxy until 2012, safe leg real
    assert "100%" not in row_2020


def test_window_proxy_share_weights_by_time_span_not_point_count():
    from guard.backtest.report import window_proxy_share
    from guard.market.history import HistoryPoint

    pts = (
        HistoryPoint(date(2020, 1, 1), 1.0, "proxy", "p"),  # one sparse proxy point: 90 days
        HistoryPoint(date(2020, 3, 31), 1.0, "etf", "e"),
        *(HistoryPoint(date(2020, 4, 1 + k), 1.0, "etf", "e") for k in range(10)),
    )
    share = window_proxy_share(History(pts), date(2020, 1, 1), date(2020, 4, 10))
    assert share == pytest.approx(90 / 100)  # point count would say 1/12


def test_a_met_window_is_flagged_when_it_rests_on_proxy_data(draft):
    inputs = draft[0]
    loose = inputs.settings.model_copy(
        update={"targets": inputs.settings.targets.model_copy(update={"crisis_max_drawdown": 0.99})}
    )
    md = render_phase0_report(tweaked(inputs, settings=loose))
    row_2008 = next(
        r
        for r in section(md, "Crisis frontier").splitlines()
        if r.startswith("| config defaults | 2008")
    )
    row_2020 = next(
        r
        for r in section(md, "Crisis frontier").splitlines()
        if r.startswith("| config defaults | 2020")
    )
    assert "met on PROXY data only" in row_2008
    assert "PROXY" not in row_2020


def _with_provenance(h: History, first: int, prov: str, source: str | None = None) -> History:
    from guard.market.history import HistoryPoint

    pts = list(h.points)
    for i in range(first):
        p = pts[i]
        pts[i] = HistoryPoint(p.day, p.level, prov, source or p.source_id)  # type: ignore[arg-type]
    return History(tuple(pts), h.manifest)


def test_banner_shows_for_any_non_instrument_point_on_either_leg(tmp_path, data_dir):
    inputs, _ = study(tmp_path, None)
    assert "Draft: proxy data" not in render_phase0_report(inputs)
    h = dict(inputs.histories)
    h["money_market"] = _with_provenance(h["money_market"], 1, "proxy")  # rounds to 0.0%
    assert "Draft: proxy data" in render_phase0_report(tweaked(inputs, histories=h))
    h = dict(inputs.histories)
    h["global_equity"] = _with_provenance(h["global_equity"], 400, "index")
    assert "Draft: proxy data" in render_phase0_report(tweaked(inputs, histories=h))


def test_data_section_names_every_substitution(tmp_path, data_dir):
    inputs, _ = study(tmp_path, None)
    h = dict(inputs.histories)
    h["money_market"] = _with_provenance(h["money_market"], 600, "proxy", "sonia_rate_pct")
    md = render_phase0_report(tweaked(inputs, histories=h))
    data = section(md, "Data")
    assert (
        "sonia_rate_pct = FRED UK 3-month interbank rate IR3TIB01GBM156N, which carries a credit "
        "spread over SONIA, notably in 2008" in data
    )
    h["global_equity"] = _with_provenance(h["global_equity"], 50, "proxy", "mystery_series")
    data = section(render_phase0_report(tweaked(inputs, histories=h)), "Data")
    assert "mystery_series" in data and "substitut" in data.lower()


def test_trials_section_gives_three_run_counts(draft):
    inputs, log = draft
    t = section(render_phase0_report(inputs), "Trials")
    assert f"Runs this invocation: {log.session_count}" in t
    assert f"Distinct parameter combinations in the whole log: {log.distinct_combinations()}" in t
    assert f"Cumulative runs including earlier invocations: {log.count}" in t


def test_quick_run_banner_and_no_wednesday_verdict(draft):
    md = render_phase0_report(draft[0])
    banner = "QUICK RUN: reduced grid, Wednesday verdict not assessed"
    assert banner in md and md.index(banner) < md.index("Summary of what the numbers say")
    wd = section(md, "Weekday and cadence")
    assert "stays" not in wd and "clearly beaten" not in wd
    assert "not assessed" in wd.lower()
    assert "Max drawdown" in wd and "Worst lag" in wd  # medians per weekday for both


def test_plateau_section_labels_selected_parameters_in_sample(draft):
    md = render_phase0_report(draft[0])
    p = section(md, "Plateau tables")
    assert "IN-SAMPLE (selected on this history)" in p
    assert "Joint plateau score" in p
    assert "validation" not in p.lower()
    for line in md.splitlines():
        if line.startswith("| plateau centre"):
            assert "IN-SAMPLE (selected on this history)" in line


def test_plateau_says_when_no_region_meets_the_targets(tmp_path, data_dir):
    s = load_settings()
    tight = s.model_copy(
        update={"targets": s.targets.model_copy(update={"crisis_max_drawdown": 0.001})}
    )
    inputs, _ = study(tmp_path, None, n=3600, settings=tight)
    assert "no region meets the targets" in section(render_phase0_report(inputs), "Plateau tables")


def test_walk_forward_section_reports_coverage_per_fold_table_and_the_limitation(draft):
    w = section(render_phase0_report(draft[0]), "Walk-forward")
    assert "about four crises" not in w
    assert "Crisis windows inside the out-of-sample span" in w
    assert "Defaults test return" in w and "Training Calmar" in w and "Test return" in w
    assert "replayed from the start of the history" in w
    assert "only" in w.lower() and "validation" in w.lower()


def test_safe_asset_rows_show_aligned_range_and_a_shorter_range_note(tmp_path, data_dir):
    cfg, version = load_config()
    eq, sf = world(n=3300)
    late = {d: v * 1.3 for d, v in sf.items() if d >= date(2012, 1, 2)}
    inputs = run_study(
        history(eq, None, "vwrp"),
        {"money_market": history(sf, None, "csh2"), "gold": history(late, None, "igln")},
        cfg,
        version,
        load_settings(),
        quick=True,
        weekday=2,
        trial_log=TrialLog(tmp_path / "t.jsonl", reset=True),
    )
    starts = {r.dates[0] for r in inputs.safe_strategies.values()}
    assert len(starts) == 1 and starts.pop() >= date(2012, 1, 2)
    safe = section(render_phase0_report(inputs), "Safe-asset comparison")
    assert "Aligned start" in safe and "Aligned end" in safe
    assert "shorter than the equity history" in safe
    data = section(render_phase0_report(inputs), "Data")
    assert "of safe-asset days are carried forward" in data


def test_lag_section_states_its_reference_and_the_rebalanced_comparison(draft):
    lag = section(render_phase0_report(draft[0]), "Rolling 3-year lag")
    assert "never rebalanced" in lag
    assert "rebalanced" in lag and "90/10" in lag
