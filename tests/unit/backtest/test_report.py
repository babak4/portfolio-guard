from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from guard.backtest.report import render_phase0_report
from guard.backtest.run import run_study
from guard.backtest.sweep import TrialLog, load_settings
from guard.config import load_config
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


def study(tmp_path: Path, proxy_until: date | None):
    cfg, version = load_config()
    eq, sf = world(n=4200, start=date(2007, 1, 1))
    gold = {d: v * 1.3 for d, v in sf.items()}
    log = TrialLog(tmp_path / "trials.jsonl", reset=True)
    inputs = run_study(
        history(eq, proxy_until, "vwrp"),
        {"money_market": history(sf, None, "csh2"), "gold": history(gold, None, "igln")},
        cfg,
        version,
        load_settings(),
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
    assert "-15%" in summary or "15%" in summary
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
