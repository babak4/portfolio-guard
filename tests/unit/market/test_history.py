from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from guard.market.base import Observation, ProviderUnavailable
from guard.market.chains import assemble, load_chains
from guard.market.history import (
    HistoryError,
    HistoryGapError,
    HistorySpliceError,
    SeriesInput,
    build_history,
    weekly,
)


def obs(*pairs: tuple[str, float]) -> list[Observation]:
    return [Observation(date.fromisoformat(d), Decimal(str(v))) for d, v in pairs]


def test_splice_creates_no_return_at_the_join_and_tags_proxy_before_launch() -> None:
    etf = SeriesInput("etf", "etf", obs(("2020-01-03", 50), ("2020-01-06", 51)))
    proxy = SeriesInput(
        "proxy",
        "proxy",
        obs(("2020-01-01", 100), ("2020-01-02", 102), ("2020-01-03", 104.04), ("2020-01-06", 106)),
    )
    h = build_history([etf, proxy])
    by_day = {p.day.isoformat(): p for p in h.points}
    assert by_day["2020-01-03"].level == 50 and by_day["2020-01-03"].provenance == "etf"
    assert by_day["2020-01-01"].provenance == "proxy"
    assert by_day["2020-01-02"].provenance == "proxy"
    assert by_day["2020-01-06"].provenance == "etf" and by_day["2020-01-06"].level == 51
    # the return into the join is the proxy's own 2%, not a rebasing jump
    assert by_day["2020-01-03"].level / by_day["2020-01-02"].level - 1 == pytest.approx(0.02)
    assert by_day["2020-01-02"].level == pytest.approx(102 * 50 / 104.04)


def test_usd_index_over_usd_per_gbp_gives_hand_computed_gbp_levels() -> None:
    usd = SeriesInput(
        "i", "proxy", obs(("2020-01-01", 150), ("2020-01-02", 153), ("2020-01-03", 156)), "USD"
    )
    fx = obs(("2020-01-01", 1.5), ("2020-01-02", 1.5), ("2020-01-03", 1.2))
    h = build_history([usd], fx)
    assert [round(p.level, 9) for p in h.points] == [100.0, 102.0, 130.0]


def test_usd_series_without_fx_is_rejected() -> None:
    with pytest.raises(HistoryError):
        build_history([SeriesInput("i", "proxy", obs(("2020-01-01", 1)), "USD")])


def test_missing_fx_near_a_day_raises() -> None:
    usd = SeriesInput("i", "proxy", obs(("2020-02-01", 100)), "USD")
    with pytest.raises(HistoryGapError):
        build_history([usd], obs(("2020-01-01", 1.5)), max_gap_days=10)


def test_rate_series_accrues_on_the_previous_rate() -> None:
    rate = SeriesInput(
        "r",
        "proxy",
        obs(("2020-01-01", 4), ("2020-03-14", 5), ("2020-05-26", 5)),
        kind="rate_pct",
    )
    h = build_history([rate], max_gap_days=100)
    assert [round(p.level, 6) for p in h.points] == [100.0, 100.8, 101.808]


def test_annual_fee_is_deducted_as_a_continuous_drag() -> None:
    s = SeriesInput("i", "proxy", obs(("2021-01-01", 100), ("2022-01-01", 100)), annual_fee=0.01)
    h = build_history([s], max_gap_days=400)
    assert h.points[0].level == 100
    assert h.points[1].level == pytest.approx(99.004983, abs=1e-6)


def test_gap_longer_than_threshold_raises_instead_of_interpolating() -> None:
    s = SeriesInput("i", "etf", obs(("2020-01-01", 1), ("2020-01-21", 2)))
    with pytest.raises(HistoryGapError):
        build_history([s])


def test_non_overlapping_standin_is_rejected() -> None:
    etf = SeriesInput("etf", "etf", obs(("2020-03-02", 50), ("2020-03-03", 51)))
    old = SeriesInput("old", "proxy", obs(("2020-01-01", 1), ("2020-01-02", 2)))
    with pytest.raises(HistorySpliceError):
        build_history([etf, old])


def test_standin_that_starts_later_is_unused_and_recorded() -> None:
    etf = SeriesInput("etf", "etf", obs(("2020-01-01", 50), ("2020-01-02", 51)))
    late = SeriesInput("late", "proxy", obs(("2020-01-02", 1), ("2020-01-03", 2)))
    h = build_history([etf, late])
    assert [p.source_id for p in h.points] == ["etf", "etf"]
    assert h.manifest["sources"][1]["role"].startswith("unused")


def test_weekly_takes_last_point_on_or_before_the_weekday_and_skips_empty_weeks() -> None:
    days = [date(2020, 1, 6) + timedelta(days=i) for i in range(5)]  # Mon-Fri
    days += [date(2020, 1, 13), date(2020, 1, 14), date(2020, 1, 16), date(2020, 1, 17)]  # no Wed
    s = SeriesInput("i", "etf", [Observation(d, Decimal(d.toordinal())) for d in days])
    wed = weekly(build_history([s]), 2)
    assert [p.day for p in wed] == [date(2020, 1, 8), date(2020, 1, 14)]


def test_rebuild_is_identical_and_a_changed_input_changes_the_data_version() -> None:
    def make(v: float) -> object:
        return build_history([SeriesInput("i", "etf", obs(("2020-01-01", 1), ("2020-01-02", v)))])

    a, b, c = make(2), make(2), make(3)
    assert a.canonical_json() == b.canonical_json()  # type: ignore[attr-defined]
    assert a.data_version == b.data_version  # type: ignore[attr-defined]
    assert a.data_version != c.data_version  # type: ignore[attr-defined]


def test_chain_falls_back_to_the_composite_when_the_msci_file_is_absent() -> None:
    day = date(2000, 1, 3)
    sp = [Observation(day + timedelta(days=7 * i), Decimal(1000 + i)) for i in range(60)]
    fx = [Observation(day + timedelta(days=7 * i), Decimal("1.6")) for i in range(60)]
    vwrp = [Observation(sp[40].day + timedelta(days=7 * i), Decimal(50 + i)) for i in range(20)]

    def loader(source: str) -> list[Observation]:
        if source == "sp500_tr_usd":
            return sp
        if source == "vwrp_gbp":
            return vwrp
        raise ProviderUnavailable(f"{source} missing")

    chain, skipped = assemble("global_equity", loader)
    assert sorted(skipped) == ["msci_acwi_nr_usd", "vt_usd"]
    h = build_history(chain, fx)
    assert {p.provenance for p in h.points[:40]} == {"proxy"}
    assert {p.source_id for p in h.points[:40]} == {"sp500_tr_usd"}
    assert {p.provenance for p in h.points[40:]} == {"etf"}


def test_default_chains_load_and_name_the_safe_asset_fallbacks() -> None:
    chains = load_chains()
    assert [e.source for e in chains["money_market"]] == [
        "csh2_gbp",
        "sonia_rate_pct",
        "bank_rate_pct",
    ]
    assert chains["global_equity"][0].provenance == "etf"
    assert all(e.provenance == "proxy" for e in chains["global_equity"][1:])


def test_golden_gfc_window_matches_independent_arithmetic() -> None:
    g = json.loads((Path(__file__).parents[2] / "golden/history/gfc_window.json").read_text())
    days = [date.fromisoformat(d) for d in g["days"]]
    usd = SeriesInput(
        "msci",
        "proxy",
        [Observation(d, Decimal(str(v))) for d, v in zip(days, g["msci_usd"], strict=True)],
        "USD",
        annual_fee=g["fee"],
    )
    fx = [Observation(d, Decimal(str(v))) for d, v in zip(days, g["usd_per_gbp"], strict=True)]
    h = build_history([usd], fx)
    assert [p.level for p in h.points] == pytest.approx(g["expected_gbp"], rel=1e-9)
    assert all(p.provenance == "proxy" for p in h.points)
