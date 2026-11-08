from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from hypothesis import event, given, settings
from hypothesis import strategies as st

from guard.advice import AdviceInputs
from guard.guardrails import advise
from tests.unit.advice.helpers import holding, make_config, make_inputs

SYMBOLS = ["VWRP", "VUAG", "VWCE", "CSH2", "XSTR", "BRK.B", "ZZZZ"]
TARGET_SETS = [
    {},
    {"VWRP": 0.6, "VUAG": 0.4},
    {"VWRP": 1.0},
    {"VWRP": 0.5, "VWCE": 0.3, "VUAG": 0.2},
]
ACCOUNTS = ["isa", "gia"]


@st.composite
def scenarios(draw: st.DrawFn) -> AdviceInputs:
    pairs = draw(
        st.lists(
            st.tuples(st.sampled_from(ACCOUNTS), st.sampled_from(SYMBOLS)),
            max_size=10,
            unique=True,
        )
    )
    holdings = [
        holding(
            account,
            symbol,
            draw(st.integers(0, 60_000)),
            excluded=symbol == "BRK.B",
        )
        for account, symbol in pairs
    ]
    cash = {a: draw(st.integers(0, 5_000)) for a in ACCOUNTS}
    contributions = {a: draw(st.integers(0, 3_000)) for a in ACCOUNTS}
    before = draw(st.sampled_from([0.5, 0.6, 0.75, 0.9, 1.0]))
    after = draw(st.sampled_from([0.5, 0.6, 0.75, 0.9, 1.0]))
    cfg = make_config(
        allocation={"growth_targets": draw(st.sampled_from(TARGET_SETS)), "min_trade_gbp": 25}
    )
    return make_inputs(
        holdings,
        before=before,
        after=after,
        cash=cash,
        contributions=contributions,
        config=cfg,
        paused=draw(st.integers(0, 9)) == 0,
    )


@settings(max_examples=300, deadline=None)
@given(scenarios())
def test_advice_invariants(inputs: AdviceInputs) -> None:
    a = advise(inputs)
    event(f"status={a.status}")
    event(f"trades={bool(a.trades)}")
    funds = inputs.funds
    held: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    excluded: set[str] = set()
    for h in inputs.holdings:
        s = funds.symbol_for(h.ticker)
        held[(h.account_id, s)] += h.value_gbp
        if h.excluded:
            excluded.add(s)

    if a.status != "advice":
        assert a.trades == () and a.contribution_plan == ()
        return

    sold: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    sells: dict[str, Decimal] = defaultdict(Decimal)
    buys: dict[str, Decimal] = defaultdict(Decimal)
    for t in a.trades:
        assert t.amount > 0 and t.amount == t.amount.to_integral_value()
        assert t.symbol not in excluded
        assert funds.get(t.symbol) is not None
        if t.action == "SELL":
            assert t.account_id == "isa"  # never a GIA
            sold[(t.account_id, t.symbol)] += t.amount
            sells[t.account_id] += t.amount
        else:
            buys[t.account_id] += t.amount
    for key, amount in sold.items():
        assert amount <= held[key]
    for account, amount in buys.items():
        assert amount <= sells[account] + inputs.cash[account]

    cap = Decimal(str(inputs.config.ladder.max_weekly_change)) * a.portfolio_value
    assert sum(sells.values(), Decimal(0)) <= cap
    assert sum(buys.values(), Decimal(0)) <= cap

    placed: dict[str, Decimal] = defaultdict(Decimal)
    for c in a.contribution_plan:
        assert c.amount > 0 and c.amount == c.amount.to_integral_value()
        placed[c.account_id] += c.amount
    unplaceable = any("cannot place growth" in f.message for f in a.flags)
    for account, amount in inputs.contributions.items():
        if unplaceable:
            assert placed[account] <= amount
        else:
            assert placed[account] == amount.to_integral_value()

    # resulting growth share respects the floor
    growth = Decimal(0)
    total = Decimal(0)
    for (account, s), value in held.items():
        fund = funds.get(s)
        if fund is None or s in excluded:
            continue
        delta = sum(
            (t.amount if t.action == "BUY" else -t.amount)
            for t in a.trades
            if t.account_id == account and t.symbol == s
        )
        value += delta
        total += value
        if fund.role == "growth":
            growth += value
    for t in a.trades:
        if (t.account_id, t.symbol) not in held:
            total += t.amount
            if funds.get(t.symbol).role == "growth":  # type: ignore[union-attr]
                growth += t.amount
    if total > 0:
        assert growth / total >= Decimal(str(inputs.config.ladder.equity_floor)) - Decimal("1e-9")
