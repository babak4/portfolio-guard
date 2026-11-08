from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from guard.advice import Advice, Trade, build_advice

from .helpers import holding, make_config, make_inputs, registry


def D(x: int | str) -> Decimal:
    return Decimal(str(x))


def trades_of(a: Advice) -> set[tuple[str, str, str, Decimal]]:
    return {(t.account_id, t.symbol, t.action, t.amount) for t in a.trades}


def lines_of(a: Advice) -> dict[tuple[str, str], Decimal]:
    return {(c.account_id, c.symbol): c.amount for c in a.contribution_plan}


def flag_messages(a: Advice) -> list[str]:
    return [f.message for f in a.flags]


def test_drift_of_5_1_points_rebalances() -> None:
    inputs = make_inputs(
        [holding("isa", "VWRP", "651"), holding("isa", "CSH2", "349")], before=0.6, after=0.6
    )
    a = build_advice(inputs)
    assert a.status == "advice"
    assert trades_of(a) == {("isa", "VWRP", "SELL", D(51)), ("isa", "CSH2", "BUY", D(51))}


def test_drift_of_exactly_5_points_does_not() -> None:
    inputs = make_inputs(
        [holding("isa", "VWRP", "650"), holding("isa", "CSH2", "350")], before=0.6, after=0.6
    )
    assert build_advice(inputs).trades == ()


def test_ladder_move_triggers_rebalance_even_inside_band() -> None:
    inputs = make_inputs(
        [holding("isa", "VWRP", "900"), holding("isa", "CSH2", "100")], before=0.9, after=0.85
    )
    # target growth 850, sell 50
    assert trades_of(build_advice(inputs)) == {
        ("isa", "VWRP", "SELL", D(50)),
        ("isa", "CSH2", "BUY", D(50)),
    }


def test_fund_level_drift_triggers_with_targets() -> None:
    cfg = make_config(
        allocation={"growth_targets": {"VWRP": 0.5, "VUAG": 0.5}, "min_trade_gbp": 25}
    )
    # growth share exactly on 0.9 but VWRP is 0.6 of total rather than 0.45
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "600"),
            holding("isa", "VUAG", "300"),
            holding("isa", "CSH2", "100"),
        ],
        before=0.9,
        after=0.9,
        config=cfg,
    )
    assert trades_of(build_advice(inputs)) == {
        ("isa", "VWRP", "SELL", D(150)),
        ("isa", "VUAG", "BUY", D(150)),
    }


def test_contribution_splits_600_400_at_sixty_percent() -> None:
    inputs = make_inputs(
        [holding("isa", "VWRP", "600"), holding("isa", "CSH2", "400")],
        before=0.6,
        after=0.6,
        contributions={"gia": 1000},
    )
    a = build_advice(inputs)
    assert a.trades == ()
    assert lines_of(a) == {("gia", "VWRP"): D(600), ("gia", "CSH2"): D(400)}


def test_contribution_splits_900_100_at_ninety_percent() -> None:
    inputs = make_inputs(
        [holding("isa", "VWRP", "900"), holding("isa", "CSH2", "100")],
        contributions={"gia": 1000},
    )
    assert lines_of(build_advice(inputs)) == {("gia", "VWRP"): D(900), ("gia", "CSH2"): D(100)}


def test_below_target_equity_sends_whole_contribution_to_growth() -> None:
    # everything is in the GIA, which cannot sell, so the portfolio stays at 50%
    inputs = make_inputs(
        [holding("gia", "VWRP", "500"), holding("gia", "CSH2", "500")],
        contributions={"gia": 1000},
    )
    a = build_advice(inputs)
    assert a.trades == ()
    assert lines_of(a) == {("gia", "VWRP"): D(1000)}
    warn = [f for f in a.flags if f.severity == "warn"]
    assert any("new money will close it" in f.message for f in warn)


def test_above_target_equity_sends_whole_contribution_to_safe() -> None:
    inputs = make_inputs(
        [holding("gia", "VWRP", "950"), holding("gia", "CSH2", "50")],
        before=0.6,
        after=0.6,
        contributions={"gia": 200},
    )
    assert lines_of(build_advice(inputs)) == {("gia", "CSH2"): D(200)}


def test_de_risk_is_isa_sells_plus_gia_contribution_plan_and_no_gia_sell() -> None:
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "1000"),
            holding("gia", "VWRP", "4000"),
            holding("gia", "CSH2", "1000"),
        ],
        before=0.9,
        after=0.5,
        contributions={"gia": 1000},
    )
    a = build_advice(inputs)
    assert trades_of(a) == {("isa", "VWRP", "SELL", D(1000)), ("isa", "CSH2", "BUY", D(1000))}
    assert not any(t.account_id == "gia" for t in a.trades)
    # after trades growth is 4000 of 6000; adding 1000 needs no more growth
    assert lines_of(a) == {("gia", "CSH2"): D(1000)}
    msgs = " ".join(flag_messages(a))
    assert "16.7%" in msgs and "new money will close it" in msgs


def test_pause_returns_paused_with_no_trades() -> None:
    inputs = make_inputs(
        [holding("isa", "VWRP", "900"), holding("isa", "CSH2", "100")],
        before=0.9,
        after=0.5,
        contributions={"gia": 1000},
        paused=True,
    )
    a = build_advice(inputs)
    assert a.status == "paused" and a.trades == () and a.contribution_plan == ()
    assert a.reasons == ("advice is paused",)


def test_no_per_fund_targets_flags_and_buys_pro_rata() -> None:
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "3000"),
            holding("isa", "VUAG", "1000"),
            holding("isa", "CSH2", "1000"),
        ],
        before=0.8,
        after=0.9,
    )
    a = build_advice(inputs)
    assert "no per-fund targets set" in flag_messages(a)
    assert [f.severity for f in a.flags if f.message == "no per-fund targets set"] == ["info"]
    assert trades_of(a) == {
        ("isa", "CSH2", "SELL", D(500)),
        ("isa", "VWRP", "BUY", D(375)),
        ("isa", "VUAG", "BUY", D(125)),
    }


def test_contribution_goes_to_most_under_target_fund_first() -> None:
    cfg = make_config(
        allocation={"growth_targets": {"VWRP": 0.6, "VUAG": 0.4}, "min_trade_gbp": 25}
    )
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "540"),
            holding("isa", "VUAG", "360"),
            holding("isa", "CSH2", "100"),
        ],
        contributions={"gia": 1000},
        config=cfg,
    )
    a = build_advice(inputs)
    assert a.trades == ()
    assert lines_of(a) == {
        ("gia", "VWRP"): D(540),
        ("gia", "VUAG"): D(360),
        ("gia", "CSH2"): D(100),
    }
    assert [c.symbol for c in a.contribution_plan][0] == "VWRP"


def test_contribution_with_no_growth_holding_and_no_targets_is_flagged() -> None:
    inputs = make_inputs(
        [holding("isa", "CSH2", "1000")], before=0.6, after=0.6, contributions={"gia": 1000}
    )
    a = build_advice(inputs)
    # the whole contribution is growth (gap 1200 > 1000) and there is nowhere to put it
    assert lines_of(a) == {}
    assert "cannot place growth contribution: no growth fund held and no targets set" in (
        flag_messages(a)
    )


def test_contribution_lines_sum_to_contribution_with_awkward_split() -> None:
    cfg = make_config(
        allocation={"growth_targets": {"VWRP": 0.5, "VUAG": 0.3, "VWCE": 0.2}, "min_trade_gbp": 25}
    )
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "333"),
            holding("isa", "VUAG", "333"),
            holding("isa", "VWCE", "334"),
        ],
        before=1.0,
        after=1.0,
        contributions={"gia": 777},
        config=cfg,
    )
    a = build_advice(inputs)
    assert sum((c.amount for c in a.contribution_plan), D(0)) == D(777)
    assert all(
        c.amount == c.amount.to_integral_value() and c.amount > 0 for c in a.contribution_plan
    )


def test_excluded_holdings_are_never_traded_and_leave_tactical_totals() -> None:
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "900"),
            holding("isa", "CSH2", "100"),
            holding("gia", "BRK.B", "1000", excluded=True),
        ],
        before=0.9,
        after=0.75,
    )
    a = build_advice(inputs)
    assert a.tactical_value == D(1000) and a.portfolio_value == D(2000)
    assert all(t.symbol != "BRK.B" for t in a.trades)
    assert trades_of(a) == {("isa", "VWRP", "SELL", D(150)), ("isa", "CSH2", "BUY", D(150))}
    assert a.sleeve.growth_actual == 0.9 and a.sleeve.growth_advised == 0.75


def test_trades_below_minimum_are_dropped() -> None:
    inputs = make_inputs(
        [holding("isa", "VWRP", "900"), holding("isa", "CSH2", "100")], before=0.9, after=0.89
    )
    assert build_advice(inputs).trades == ()


def test_unknown_fund_is_flagged_and_blocks_advice() -> None:
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "800"),
            holding("isa", "CSH2", "100"),
            holding("isa", "ZZZZ", "100"),
        ],
        before=0.9,
        after=0.6,
    )
    a = build_advice(inputs)
    assert a.status == "blocked" and a.trades == () and a.contribution_plan == ()
    assert any(f.kind == "unknown_fund" for f in a.flags)


def test_flags_from_constraints_are_included() -> None:
    inputs = make_inputs(
        [holding("isa", "VUAG", "600"), holding("isa", "CSH2", "400")], before=0.6, after=0.6
    )
    a = build_advice(inputs)
    assert any(f.kind == "us_exposure" and f.severity == "warn" for f in a.flags)


def test_available_symbols_reach_the_fee_check() -> None:
    # VWRP at 0.25% has the same-index VWCE as a cheaper, available alternative
    funds = registry()
    pricey = replace(funds.get("VWRP"), ongoing_charge=0.0025)  # type: ignore[type-var]
    inputs = make_inputs(
        [holding("isa", "VWRP", "900"), holding("isa", "CSH2", "100")],
        available=frozenset({"VWCE"}),
        funds=type(funds)({**funds.by_symbol, "VWRP": pricey}),
    )
    msgs = " ".join(flag_messages(build_advice(inputs)))
    assert "VWCE" in msgs


def test_allocation_rows() -> None:
    inputs = make_inputs(
        [holding("isa", "VWRP", "600"), holding("isa", "CSH2", "400")], before=0.6, after=0.6
    )
    rows = {r.symbol: r for r in build_advice(inputs).allocation}
    assert rows["VWRP"].value == D(600) and rows["VWRP"].weight == 0.6
    assert rows["VWRP"].role == "growth"
    assert rows["CSH2"].target_weight == 0.4 and rows["CSH2"].drift == 0.0


def test_trade_dataclass_is_frozen() -> None:
    t = Trade("isa", "VWRP", "SELL", D(1))
    try:
        t.amount = D(2)  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Trade must be frozen")
