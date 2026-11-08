from __future__ import annotations

from datetime import date

import pytest

from guard.backtest.engine import BacktestParams, run_backtest
from guard.config.models import Signals
from tests.unit.backtest.helpers import (
    fixed_ladder,
    flat_then,
    trend_only_signals,
    two_step_ladder,
    weekdays,
)

MON = date(2024, 1, 1)


def run(equity, safe, dates, *, ladder=None, signals=None, data_version="d1", **p):
    params = BacktestParams(**{"drift_band": 0.5, "spread": 0.0, **p})
    return run_backtest(
        dict(zip(dates, equity, strict=True)),
        dict(zip(dates, safe, strict=True)),
        signals=signals or trend_only_signals(),
        ladder=ladder or two_step_ladder(),
        params=params,
        data_version=data_version,
        config_version="c1",
    )


def four_week_drop():
    """Equity falls 10% on Tuesday of week 4 (day 16); the safe asset is flat."""
    dates = weekdays(MON, 20)
    return dates, flat_then(20, {16: 90.0}), [100.0] * 20


def test_constant_equity_strategy_is_buy_and_hold():
    dates = weekdays(MON, 30)
    eq = [100 + (i * 7) % 11 + i * 0.5 for i in range(30)]
    sf = [50 + (i * 3) % 5 for i in range(30)]
    res = run(eq, sf, dates, ladder=fixed_ladder(1.0))
    assert res.total_traded == 0
    for v, e in zip(res.values, eq, strict=True):
        assert v == pytest.approx(100000 * e / eq[0], rel=1e-12)
    assert res.index == pytest.approx([e / eq[0] for e in eq], rel=1e-12)


def test_constant_mixed_weight_matches_hand_buy_and_hold_when_band_is_wide():
    dates = weekdays(MON, 30)
    eq = [100 + (i * 7) % 11 + i * 0.5 for i in range(30)]
    sf = [50 + (i * 3) % 5 for i in range(30)]
    res = run(eq, sf, dates, ladder=fixed_ladder(0.9), drift_band=0.99)
    for v, e, s in zip(res.values, eq, sf, strict=True):
        assert v == pytest.approx(90000 * e / eq[0] + 10000 * s / sf[0], rel=1e-12)


def test_step_change_in_week_four_hand_computed():
    dates, eq, sf = four_week_drop()
    res = run(eq, sf, dates)
    # day 16: equity 80000 -> 72000, total 92000. Day 17 (Wednesday): trend negative, step 0.8 -> 0.6.
    # Equity 0.6 * 92000 = 55200; traded 2 * (72000 - 55200) = 33600.
    assert res.values[16] == pytest.approx(92000)
    assert res.advised_equity[16] == pytest.approx(0.8)
    assert res.advised_equity[17] == pytest.approx(0.6)
    assert res.total_traded == pytest.approx(33600)
    assert res.values[-1] == pytest.approx(92000)
    assert [(d, x.kind) for d, x in res.decisions if x.kind == "derisk"] == [(dates[17], "derisk")]
    (isa,) = res.sleeves
    assert (isa.equity, isa.safe) == (pytest.approx(55200), pytest.approx(36800))


def test_spread_on_a_twenty_point_shift():
    dates, eq, sf = four_week_drop()
    res = run(eq, sf, dates, spread=0.001)
    # traded value 33600 (sell 16800 equity, buy 16800 safe) costs 0.1% = 33.60
    assert res.total_traded == pytest.approx(33600)
    assert res.values[-1] == pytest.approx(92000 - 33.6)
    assert res.index[-1] == pytest.approx((92000 - 33.6) / 100000)


def test_realistic_mode_leaves_gia_alone_and_steers_contributions():
    dates = weekdays(date(2024, 1, 8), 20)  # Thursday 2024-02-01 is day 18
    assert dates[18] == date(2024, 2, 1) and dates[17] == date(2024, 1, 31)
    eq, sf = flat_then(20, {16: 90.0}), [100.0] * 20
    res = run(eq, sf, dates, mode="realistic", gia_share=0.4, monthly_gia_contribution=1000)
    isa, gia = res.sleeves
    assert isa.tradable and not gia.tradable
    # ISA 60000 at 0.8: 48000/12000; falls to 43200/12000 = 55200; de-risked to 0.6: 33120/22080.
    assert isa.equity == pytest.approx(33120) and isa.safe == pytest.approx(22080)
    # GIA 40000: 32000/8000 -> 28800/8000 untouched, then the 1000 contribution at 0.6: +600/+400.
    assert gia.equity == pytest.approx(29400) and gia.safe == pytest.approx(8400)
    assert res.total_traded == pytest.approx(2 * (43200 - 33120))


def test_contributions_do_not_look_like_returns():
    dates = weekdays(date(2024, 1, 22), 15)  # crosses into February on day 8
    flat = [100.0] * 15
    res = run(flat, flat, dates, mode="realistic", monthly_gia_contribution=1000)
    assert res.index == pytest.approx([1.0] * 15)
    assert res.values[-1] == pytest.approx(101000)
    assert res.values[7] == pytest.approx(100000)
    assert res.values[8] == pytest.approx(101000)


def test_no_lookahead_a_spike_on_the_decision_day_cannot_change_it():
    dates = weekdays(MON, 30)
    calm = [100.0] * 30
    spiked = flat_then(30, {17: 40.0, 18: 100.0})  # crash on Wednesday of week 4 only
    a = run(calm, [100.0] * 30, dates)
    b = run(spiked, [100.0] * 30, dates)
    da = {d: x for d, x in a.decisions}
    db = {d: x for d, x in b.decisions}
    assert db[dates[17]] == da[dates[17]]
    assert b.advised_equity[17] == a.advised_equity[17] == pytest.approx(0.8)


def test_decision_days_follow_the_weekday_and_use_prior_close():
    dates = weekdays(MON, 15)
    res = run([100.0] * 15, [100.0] * 15, dates, weekday=4)
    assert [d.weekday() for d, _ in res.decisions] == [4, 4, 4]


def test_monthly_cadence_decides_on_first_decision_day_of_each_month():
    dates = weekdays(date(2024, 1, 1), 70)
    res = run([100.0] * 70, [100.0] * 70, dates, cadence="monthly")
    days = [d for d, _ in res.decisions]
    assert days == [date(2024, 1, 3), date(2024, 2, 7), date(2024, 3, 6), date(2024, 4, 3)]


def test_manifest_is_deterministic_and_data_version_changes_it():
    dates, eq, sf = four_week_drop()
    a, b = run(eq, sf, dates), run(eq, sf, dates)
    assert a.to_canonical_json() == b.to_canonical_json()
    assert a.manifest == b.manifest
    c = run(eq, sf, dates, data_version="d2")
    assert c.to_canonical_json() != a.to_canonical_json()
    assert c.manifest["data_version"] == "d2"
    assert set(a.manifest) == {"code_version", "config_version", "parameters", "data_version"}
    d = run(eq, sf, dates, spread=0.002)
    assert d.to_canonical_json() != a.to_canonical_json()


def test_only_dates_in_both_series_are_used():
    dates = weekdays(MON, 10)
    eq = dict(zip(dates, [100.0] * 10, strict=True))
    sf = dict(zip(dates[2:], [100.0] * 8, strict=True))
    res = run_backtest(
        eq,
        sf,
        signals=trend_only_signals(),
        ladder=fixed_ladder(1.0),
        params=BacktestParams(drift_band=0.5),
        data_version="x",
    )
    assert list(res.dates) == dates[2:]


def test_sma_months_method_runs():
    sig = Signals(
        trend_method="sma_months",
        trend_window=2,
        drawdown_threshold=0.99,
        drawdown_lookback_weeks=1,
        volatility_window_days=2,
        volatility_threshold=1000.0,
    )
    dates = weekdays(date(2024, 1, 1), 70)
    res = run([100.0] * 70, [100.0] * 70, dates, signals=sig)
    assert res.values[-1] == pytest.approx(100000)
