from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from guard.advice import Advice, Trade, build_advice
from guard.guardrails import advise, apply_guardrails
from tests.unit.advice.helpers import holding, make_config, make_inputs


def D(x: int | str) -> Decimal:
    return Decimal(str(x))


def names(a: Advice) -> dict[str, bool]:
    return {g.name: g.passed for g in a.guardrails}


BASE = [holding("isa", "VWRP", "600"), holding("isa", "CSH2", "400")]


def bad(trades: list[Trade], hs=None, **kw):  # type: ignore[no-untyped-def]
    inputs = make_inputs(hs or BASE, before=0.6, after=0.6, **kw)
    draft = replace(build_advice(inputs), trades=tuple(trades))
    return apply_guardrails(draft, inputs)


def test_all_checks_are_recorded_when_advice_passes() -> None:
    a = advise(make_inputs(BASE, before=0.6, after=0.6))
    assert a.status == "advice"
    assert names(a) == {
        "pause_switch": True,
        "trade_sanity": True,
        "weekly_cap": True,
        "equity_floor": True,
    }


def test_pause_switch() -> None:
    inputs = make_inputs(BASE, before=0.9, after=0.5, paused=True, contributions={"gia": 1000})
    draft = build_advice(replace(inputs, paused=False))
    a = apply_guardrails(draft, inputs)
    assert a.status == "paused" and a.trades == () and a.contribution_plan == ()
    assert "advice is paused" in a.reasons
    assert names(a)["pause_switch"] is False


def test_gia_sell_is_blocked() -> None:
    hs = [*BASE, holding("gia", "VWRP", "500")]
    a = bad([Trade("gia", "VWRP", "SELL", D(100)), Trade("gia", "CSH2", "BUY", D(100))], hs)
    assert a.status == "blocked" and a.trades == () and a.contribution_plan == ()
    assert any("GIA" in r or "gia" in r for r in a.reasons)
    assert names(a)["trade_sanity"] is False


def test_sell_more_than_held_is_blocked() -> None:
    a = bad([Trade("isa", "VWRP", "SELL", D(601)), Trade("isa", "CSH2", "BUY", D(601))])
    assert a.status == "blocked" and "more than" in " ".join(a.reasons)


def test_buy_beyond_proceeds_and_cash_is_blocked() -> None:
    a = bad([Trade("isa", "VWRP", "SELL", D(10)), Trade("isa", "CSH2", "BUY", D(60))])
    assert a.status == "blocked"
    ok = bad(
        [Trade("isa", "VWRP", "SELL", D(10)), Trade("isa", "CSH2", "BUY", D(60))],
        cash={"isa": 50},
    )
    assert ok.status == "advice"


def test_excluded_holding_trade_is_blocked() -> None:
    hs = [*BASE, holding("isa", "BRK.B", "500", excluded=True)]
    a = bad([Trade("isa", "BRK.B", "SELL", D(100)), Trade("isa", "CSH2", "BUY", D(100))], hs)
    assert a.status == "blocked" and "excluded" in " ".join(a.reasons)


def test_non_positive_amount_is_blocked() -> None:
    a = bad([Trade("isa", "VWRP", "SELL", D(0))])
    assert a.status == "blocked"


def test_weekly_cap_scales_all_trades_proportionally() -> None:
    hs = [holding("isa", "VWRP", "5000"), holding("gia", "VWRP", "5000")]
    inputs = make_inputs(hs, before=1.0, after=0.5)
    draft = build_advice(inputs)
    assert {t.amount for t in draft.trades} == {D(5000)}
    a = apply_guardrails(draft, inputs)
    assert a.status == "advice"
    assert {(t.symbol, t.action, t.amount) for t in a.trades} == {
        ("VWRP", "SELL", D(1500)),
        ("CSH2", "BUY", D(1500)),
    }
    assert any(
        f.severity == "warn" and f.message == "capped at 15% this week; continues next week"
        for f in a.flags
    )
    assert "weekly_cap" in names(a)


def test_contribution_plan_is_recomputed_after_cap() -> None:
    hs = [holding("isa", "VWRP", "5000"), holding("gia", "VWRP", "5000")]
    inputs = make_inputs(hs, before=1.0, after=0.5, contributions={"gia": 1000})
    a = advise(inputs)
    # after capped trades growth is 8500 of 10000; target 0.5 means all new money is safe
    assert {(c.symbol, c.amount) for c in a.contribution_plan} == {("CSH2", D(1000))}


def test_equity_floor_violation_blocks() -> None:
    # trades that cut growth to 75% of a 90% book stay blocked under an 80% floor
    cfg = make_config(ladder={"equity_floor": 0.8})
    hs = [holding("isa", "VWRP", "900"), holding("isa", "CSH2", "100")]
    a = advise(make_inputs(hs, before=0.9, after=0.5, config=cfg))
    assert a.status == "blocked" and a.trades == ()
    assert "equity floor" in " ".join(a.reasons)
    assert names(a)["equity_floor"] is False


def test_book_already_below_floor_with_no_trades_is_not_blocked() -> None:
    cfg = make_config(ladder={"equity_floor": 0.8})
    a = advise(make_inputs(BASE, before=0.6, after=0.6, config=cfg))
    assert a.status == "advice"
    assert names(a)["equity_floor"] is True


def test_corrective_buys_below_floor_pass() -> None:
    cfg = make_config(ladder={"equity_floor": 0.8})
    a = advise(make_inputs(BASE, before=0.6, after=0.9, config=cfg))
    assert a.status == "advice" and a.trades
    assert {t.symbol for t in a.trades if t.action == "BUY"} == {"VWRP"}
    assert names(a)["equity_floor"] is True
    assert a.sleeve.growth_after >= a.sleeve.growth_actual
    assert a.sleeve.growth_after < 0.8


def test_trades_that_lower_a_below_floor_share_stay_blocked() -> None:
    cfg = make_config(ladder={"equity_floor": 0.8})
    a = advise(make_inputs(BASE, before=0.6, after=0.5, config=cfg))
    assert a.status == "blocked" and a.trades == ()
    assert names(a)["equity_floor"] is False


def test_equity_floor_tolerance() -> None:
    cfg = make_config(ladder={"equity_floor": 0.6})
    a = advise(make_inputs(BASE, before=0.6, after=0.6, config=cfg))
    assert a.status == "advice"


def test_weekly_cap_is_marked_adjusted_with_factor_in_detail() -> None:
    hs = [holding("isa", "VWRP", "5000"), holding("gia", "VWRP", "5000")]
    a = advise(make_inputs(hs, before=1.0, after=0.5))
    cap = next(g for g in a.guardrails if g.name == "weekly_cap")
    assert cap.passed is True and cap.adjusted is True
    assert "0.3000" in cap.detail
    others = [g for g in a.guardrails if g.name != "weekly_cap"]
    assert all(not g.adjusted for g in others)


def test_uncapped_advice_is_not_adjusted() -> None:
    a = advise(make_inputs(BASE, before=0.6, after=0.6))
    assert all(not g.adjusted for g in a.guardrails)


def test_rebalance_gap_flag_is_recomputed_after_cap() -> None:
    hs = [
        holding("isa", "VWRP", "1000"),
        holding("gia", "VWRP", "4000"),
        holding("gia", "CSH2", "1000"),
    ]
    inputs = make_inputs(hs, before=0.9, after=0.5)
    draft = build_advice(inputs)
    assert any("16.7%" in f.message for f in draft.flags if f.kind == "rebalance_gap")
    a = apply_guardrails(draft, inputs)
    gaps = [f for f in a.flags if f.kind == "rebalance_gap"]
    # cap is 15% of 6000 = 900; growth 4100 of 6000 against 3000 advised: 1100 still to move
    assert len(gaps) == 1 and "18.3%" in gaps[0].message
    assert a.sleeve.growth_after == pytest.approx(4100 / 6000)
    assert a.sleeve.growth_advised == 0.5


def test_cap_that_leaves_a_gap_adds_a_gap_flag_where_the_draft_had_none() -> None:
    hs = [holding("isa", "VWRP", "5000"), holding("gia", "VWRP", "5000")]
    inputs = make_inputs(hs, before=1.0, after=0.5)
    assert not [f for f in build_advice(inputs).flags if f.kind == "rebalance_gap"]
    a = apply_guardrails(build_advice(inputs), inputs)
    gaps = [f for f in a.flags if f.kind == "rebalance_gap"]
    assert len(gaps) == 1 and "35.0%" in gaps[0].message


def test_growth_after_without_cap_is_the_post_trade_share() -> None:
    hs = [holding("isa", "VWRP", "900"), holding("isa", "CSH2", "100")]
    a = advise(make_inputs(hs, before=0.9, after=0.85))
    assert a.sleeve.growth_after == pytest.approx(0.85)


def test_buy_of_a_symbol_that_is_not_a_growth_fund_is_blocked() -> None:
    for symbol in ("XSTR", "ZZZZ"):
        a = bad(
            [Trade("isa", "VWRP", "SELL", D(100)), Trade("isa", symbol, "BUY", D(100))],
        )
        assert a.status == "blocked", symbol
        assert symbol in " ".join(a.reasons)
    ok = bad([Trade("isa", "VWRP", "SELL", D(100)), Trade("isa", "CSH2", "BUY", D(100))])
    assert ok.status == "advice"
