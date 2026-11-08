from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

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
    cfg = make_config(ladder={"equity_floor": 0.8})
    a = advise(make_inputs(BASE, before=0.6, after=0.6, config=cfg))
    assert a.status == "blocked" and a.trades == ()
    assert "equity floor" in " ".join(a.reasons)
    assert names(a)["equity_floor"] is False


def test_equity_floor_tolerance() -> None:
    cfg = make_config(ladder={"equity_floor": 0.6})
    a = advise(make_inputs(BASE, before=0.6, after=0.6, config=cfg))
    assert a.status == "advice"
