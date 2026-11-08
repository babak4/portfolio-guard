"""Reading a stored weekly payload into views. Shared by the advice, index and portfolio views."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from guard.config import GuardConfig, read_version
from guard.ladder import LadderState
from guard.readapi.models import (
    AllocationLine,
    ContributionItem,
    DecisionView,
    FlagView,
    GuardrailView,
    SignalView,
    SleeveView,
    TradeLine,
)

DRAWDOWN_CAP = 0.20  # the owner's accepted worst fall in a severe downturn (docs/decisions.md)


def parse(payload: str | None) -> dict[str, Any] | None:
    if not payload:
        return None
    loaded = json.loads(payload)
    return loaded if isinstance(loaded, dict) else None


def archived_config(payload: dict[str, Any]) -> GuardConfig | None:
    """The configuration the run was made with, when its archive can still be read."""
    try:
        return read_version(payload["config_version"])
    except OSError, ValueError, KeyError:
        return None


def last_close(payload: dict[str, Any]) -> date | None:
    days = payload.get("inputs", {}).get("closes", {}).get("days") or []
    return date.fromisoformat(days[-1]) if days else None


def allocation(payload: dict[str, Any]) -> tuple[AllocationLine, ...]:
    verified = {f["symbol"]: bool(f.get("verified")) for f in payload["inputs"].get("funds", [])}
    return tuple(
        AllocationLine(
            symbol=r["symbol"],
            role=r["role"],
            value=Decimal(r["value"]),
            weight=float(r["weight"]),
            target_weight=None if r["target_weight"] is None else float(r["target_weight"]),
            drift=None if r["drift"] is None else float(r["drift"]),
            verified=verified.get(r["symbol"]),
        )
        for r in payload["advice"]["allocation"]
    )


def sleeve(payload: dict[str, Any]) -> SleeveView:
    s = payload["advice"]["sleeve"]
    return SleeveView(**{k: float(s[k]) for k in SleeveView.__dataclass_fields__})


def trades(payload: dict[str, Any]) -> tuple[TradeLine, ...]:
    return tuple(
        TradeLine(t["account_id"], t["symbol"], t["action"], Decimal(t["amount"]))
        for t in payload["advice"]["trades"]
    )


def contributions(payload: dict[str, Any]) -> tuple[ContributionItem, ...]:
    return tuple(
        ContributionItem(c["account_id"], c["symbol"], Decimal(c["amount"]))
        for c in payload["advice"]["contribution_plan"]
    )


def flags(payload: dict[str, Any]) -> tuple[FlagView, ...]:
    return tuple(
        FlagView(f["kind"], f["severity"], f["message"], tuple(f.get("symbols", [])))
        for f in payload["advice"]["flags"]
    )


def guardrails(payload: dict[str, Any]) -> tuple[GuardrailView, ...]:
    return tuple(
        GuardrailView(g["name"], bool(g["passed"]), bool(g.get("adjusted")), g["detail"])
        for g in payload["advice"]["guardrails"]
    )


def decision(payload: dict[str, Any]) -> DecisionView:
    d = payload["decision"]
    return DecisionView(
        d["kind"], float(d["equity_before"]), float(d["equity_after"]), int(d["step"]),
        tuple(d.get("reasons", [])),
    )  # fmt: skip


def ladder_state(payload: dict[str, Any]) -> LadderState:
    return LadderState(**payload["ladder_after"])


def signals(payload: dict[str, Any], cfg: GuardConfig | None) -> tuple[SignalView, ...]:
    """The three signals with plain labels and, when the archived configuration is readable, the
    level at which each turns negative."""
    raw = payload["signals"]
    sig = cfg.signals if cfg else None
    window = sig.trend_window if sig else None
    trend_unit = "month" if sig and sig.trend_method == "sma_months" else "day"
    lookback = sig.drawdown_lookback_weeks if sig else None
    specs = {
        "trend": (
            f"Index versus its {window}-{trend_unit} average"
            if window
            else "Index versus its average",
            0.0,
            "turns negative when the index closes below its average",
        ),
        "drawdown": (
            f"Fall from the {lookback}-week high" if lookback else "Fall from the recent high",
            -sig.drawdown_threshold if sig else None,
            f"turns negative at a fall of {sig.drawdown_threshold:.0%} or more"
            if sig
            else "turns negative at a large fall",
        ),
        "volatility": (
            f"Recent ups and downs ({sig.volatility_window_days}-day volatility)"
            if sig
            else "Recent ups and downs",
            sig.volatility_threshold if sig else None,
            f"turns negative above {sig.volatility_threshold:.0%} a year"
            if sig
            else "turns negative when markets are jumpy",
        ),
    }
    out = []
    for key, (label, level, text) in specs.items():
        s = raw[key]
        out.append(
            SignalView(
                key,
                label,
                None if s["value"] is None else float(s["value"]),
                bool(s["negative"]),
                bool(s["data_gap"]),
                level,
                text,
            )  # fmt: skip
        )
    return tuple(out)
