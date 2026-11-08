"""The advice engine blocks, rather than guesses, when the book or the targets are incomplete."""

from __future__ import annotations

from decimal import Decimal

from guard.advice import Advice
from guard.guardrails import advise

from .helpers import holding, make_config, make_inputs

FUNDS_FILE = "$GUARD_DATA_DIR/config/funds.yaml"


def targets_config(targets: dict[str, float]):  # type: ignore[no-untyped-def]
    return make_config(allocation={"growth_targets": targets, "min_trade_gbp": 25})


def blocked(a: Advice) -> str:
    assert a.status == "blocked", (a.status, a.reasons)
    assert a.trades == () and a.contribution_plan == ()
    return " | ".join(a.reasons)


def test_unknown_held_fund_blocks_with_fix_instructions() -> None:
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "800"),
            holding("isa", "CSH2", "100"),
            holding("isa", "ZZZZ", "100"),
        ],
        before=0.9,
        after=0.6,
        contributions={"gia": 500},
    )
    a = advise(inputs)
    text = blocked(a)
    assert "ZZZZ" in text and f"add ZZZZ to the funds file: {FUNDS_FILE}" in text
    assert any(f.kind == "unknown_fund" for f in a.flags)
    assert any(f.kind == "us_exposure" for f in a.flags)


def test_every_unknown_symbol_is_named() -> None:
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "800"),
            holding("isa", "AAAA", "100"),
            holding("gia", "BBBB", "100"),
        ]
    )
    text = blocked(advise(inputs))
    assert "AAAA" in text and "BBBB" in text


def test_excluded_unknown_holding_is_exempt() -> None:
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "800"),
            holding("isa", "CSH2", "200"),
            holding("gia", "ZZZZ", "100", excluded=True),
        ],
        before=0.8,
        after=0.8,
    )
    assert advise(inputs).status == "advice"


def test_held_growth_fund_without_target_blocks() -> None:
    cfg = targets_config({"VWRP": 1.0})
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "500"),
            holding("isa", "VUAG", "300"),
            holding("isa", "CSH2", "200"),
        ],
        config=cfg,
    )
    text = blocked(advise(inputs))
    assert "VUAG is held but has no target: add it to growth_targets, with 0 to sell it" in text


def test_zero_target_for_held_fund_is_allowed_and_sells_it() -> None:
    cfg = targets_config({"VWRP": 1.0, "VUAG": 0.0})
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "500"),
            holding("isa", "VUAG", "300"),
            holding("isa", "CSH2", "200"),
        ],
        before=0.8,
        after=0.8,
        config=cfg,
    )
    a = advise(inputs)
    assert a.status == "advice"
    assert any(t.symbol == "VUAG" and t.action == "SELL" for t in a.trades)


def test_target_that_is_not_a_registry_growth_fund_blocks() -> None:
    for bad in ("NOPE", "CSH2"):
        cfg = targets_config({"VWRP": 0.5, bad: 0.5})
        inputs = make_inputs(
            [holding("isa", "VWRP", "800"), holding("isa", "CSH2", "200")],
            before=0.8,
            after=0.8,
            config=cfg,
        )
        assert bad in blocked(advise(inputs))


def test_targets_not_summing_to_one_block() -> None:
    cfg = targets_config({"VWRP": 0.5, "VUAG": 0.4})
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "500"),
            holding("isa", "VUAG", "300"),
            holding("isa", "CSH2", "200"),
        ],
        config=cfg,
    )
    assert "sum to 0.9, not 1" in blocked(advise(inputs))


def test_targets_sum_within_tolerance_is_accepted() -> None:
    cfg = targets_config({"VWRP": 0.6, "VUAG": 0.4000001})
    inputs = make_inputs(
        [
            holding("isa", "VWRP", "480"),
            holding("isa", "VUAG", "320"),
            holding("isa", "CSH2", "200"),
        ],
        before=0.8,
        after=0.8,
        config=cfg,
    )
    assert advise(inputs).status == "advice"


def test_contribution_is_not_spent_by_the_rebalance_buy_budget() -> None:
    # the GIA's safe fund cannot be sold, so there is nothing to fund buys but spendable cash
    hs = [holding("isa", "VWRP", "100"), holding("gia", "CSH2", "1000")]
    a = advise(make_inputs(hs, before=0.5, after=0.5, contributions={"isa": 1000}))
    assert a.status == "advice"
    assert [t for t in a.trades if t.action == "BUY"] == []
    assert sum((c.amount for c in a.contribution_plan), Decimal(0)) == Decimal(1000)

    a = advise(
        make_inputs(hs, before=0.5, after=0.5, cash={"isa": 50}, contributions={"isa": 1000})
    )
    buys = sum((t.amount for t in a.trades if t.action == "BUY"), Decimal(0))
    assert buys == Decimal(50)
    assert sum((c.amount for c in a.contribution_plan), Decimal(0)) == Decimal(1000)
