from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from tests.unit.advice.helpers import holding, make_config, make_inputs

from guard.guardrails import advise

SNAPSHOT = json.loads((Path(__file__).parent / "de_risk_step.json").read_text())


def test_de_risk_step_matches_hand_computed_advice() -> None:
    inputs = make_inputs(
        [
            holding(h["account"], h["symbol"], h["value"], excluded=h.get("excluded", False))
            for h in SNAPSHOT["holdings"]
        ],
        before=SNAPSHOT["equity_before"],
        after=SNAPSHOT["equity_after"],
        cash=SNAPSHOT["cash"],
        contributions=SNAPSHOT["contributions"],
        config=make_config(
            allocation={"growth_targets": SNAPSHOT["growth_targets"], "min_trade_gbp": 25}
        ),
    )
    advice = advise(inputs)
    want = SNAPSHOT["expected"]
    assert advice.status == want["status"]
    assert [(t.account_id, t.symbol, t.action, t.amount) for t in advice.trades] == [
        (a, s, act, Decimal(v)) for a, s, act, v in want["trades"]
    ]
    assert [(c.account_id, c.symbol, c.amount) for c in advice.contribution_plan] == [
        (a, s, Decimal(v)) for a, s, v in want["contribution_plan"]
    ]
    assert advice.tactical_value == Decimal(want["tactical_value"])
    assert advice.portfolio_value == Decimal(want["portfolio_value"])
    us = next(f for f in advice.flags if f.kind == "us_exposure")
    assert us.severity == "info" and want["us_share_text"] in us.message
    assert not any(t.account_id == "gia" for t in advice.trades)
