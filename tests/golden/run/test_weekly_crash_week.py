"""A -12% week on a flat year, worked out by hand.

Closes: 295 days at 100, then 100 (the reference close), 95, 92, 90, 89, 88.

* Week return: 88 / 100 - 1 = -12%, past the 10% fast-path line.
* Trend: the 200-day mean is (195 * 100 + 95 + 92 + 90 + 89 + 88) / 200 = 99.77 and 88 is below it:
  negative.
* Drawdown: the high is 100 and 88 is 12% below it, past the 10% threshold: negative.
* Volatility: 15 zero returns and ln(95/100), ln(92/95), ln(90/92), ln(89/90), ln(88/89); their
  sample standard deviation is 0.01373 a day, 21.8% a year, under the 25% limit: not negative.
* Two negative signals mean step 2 (60% equity). The fast path skips the two-run confirmation.
  Equity falls from 90% by the 15-point weekly cap to 75% and 60% remains pending.
* A 10,000 ISA fully in VWRP holds 100% growth, not the ladder's 90%. Getting to 75% would be a
  25-point move, so the 15-point weekly cap limits the trade: sell 1,500 and buy 1,500 of CSH2.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.unit.advice.helpers import holding, make_config

from guard.advice import packaged_registry
from guard.ladder import initial_state
from guard.run.core import WeeklyInputs, compute_weekly

TAIL = [100.0, 95.0, 92.0, 90.0, 89.0, 88.0]


def _inputs() -> WeeklyInputs:
    config = make_config()
    levels = tuple([100.0] * 295 + TAIL)
    end = date(2026, 11, 10)
    days = tuple(end - timedelta(days=len(levels) - 1 - i) for i in range(len(levels)))
    return WeeklyInputs(
        config=config,
        days=days,
        levels=levels,
        state_before=initial_state(config.ladder),
        week=1,
        holdings=(holding("isa", "VWRP", 10000),),
        cash={"isa": Decimal(0)},
        contributions={"isa": Decimal(0), "gia": Decimal(0)},
        funds=packaged_registry(),
    )


def test_signals_match_the_hand_calculation() -> None:
    out = compute_weekly(_inputs())
    assert out.signals.weekly_return is not None
    assert abs(out.signals.weekly_return - (-0.12)) < 1e-12
    assert out.signals.trend.negative and out.signals.drawdown.negative
    assert not out.signals.volatility.negative
    assert out.signals.volatility.value is not None
    assert abs(out.signals.volatility.value - 0.2179) < 0.0005
    assert out.signals.drawdown.value is not None
    assert abs(out.signals.drawdown.value - (-0.12)) < 1e-12


def test_ladder_moves_to_step_two_by_the_weekly_cap() -> None:
    out = compute_weekly(_inputs())
    assert out.decision.kind == "derisk"
    assert (out.decision.equity_before, out.decision.step) == (0.9, 2)
    assert abs(out.decision.equity_after - 0.75) < 1e-9
    assert out.state_after.step == 2
    assert out.state_after.pending_equity == 0.6


def test_advice_sells_the_weekly_cap_of_the_isa_into_the_safe_fund() -> None:
    advice = compute_weekly(_inputs()).advice
    assert advice.status == "advice"
    assert [(t.account_id, t.symbol, t.action, t.amount) for t in advice.trades] == [
        ("isa", "VWRP", "SELL", Decimal(1500)),
        ("isa", "CSH2", "BUY", Decimal(1500)),
    ]
