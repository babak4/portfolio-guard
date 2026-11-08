"""Canonical JSON for audit records and the hash that identifies a run's inputs.

Canonical means the same data always gives the same text: keys sorted, no whitespace, money as
strings, dates as ISO text. A stored run can then be recomputed and compared byte for byte.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from guard.advice import Fund, FundRegistry
from guard.config import GuardConfig
from guard.ingest.model import Holding
from guard.ladder import LadderState
from guard.run.core import CLOSES_TAIL, WeeklyComputation, WeeklyInputs

# Packages whose code decides what a run advises; a change in any of them is a code change.
_DECISION_PACKAGES = ("signals", "ladder", "advice", "guardrails", "run")


def to_jsonable(value: Any) -> Any:
    """Plain JSON types from dataclasses, enums, Decimals, dates, mappings and sets."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("canonical JSON cannot hold NaN or infinity")
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted((to_jsonable(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    raise TypeError(f"cannot write {type(value).__name__} as canonical JSON")


def canonical_json(value: Any) -> str:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def inputs_hash(inputs: Any) -> str:
    """SHA-256 (hex) of the canonical JSON of a run's inputs."""
    return hashlib.sha256(canonical_json(inputs).encode("utf-8")).hexdigest()


@cache
def code_version() -> str:
    """A fingerprint of the source files that decide advice (16 hex characters)."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for package in _DECISION_PACKAGES:
        for path in sorted((root / package).rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def weekly_payload(
    inputs: WeeklyInputs,
    result: WeeklyComputation,
    *,
    slot: str,
    config_version: str,
    code: str,
    sources: tuple[str, ...],
) -> dict[str, Any]:
    """Everything needed to recompute a weekly run's advice offline, as plain JSON types."""
    funds = inputs.funds
    stored_inputs = {
        "config_version": config_version,
        "slot": slot,
        "week": inputs.week,
        "paused": inputs.paused,
        "ladder_before": inputs.state_before,
        "funds": [dataclasses.asdict(f) for f in funds],
        "holdings": [
            {
                "account": h.account_id,
                "platform": h.platform,
                "ticker": h.ticker,
                "symbol": funds.symbol_for(h.ticker),
                "isin": h.isin,
                "name": h.name,
                "quantity": h.quantity,
                "price": h.price,
                "value": h.value_gbp,
                "excluded": h.excluded,
            }
            for h in inputs.holdings
        ],
        "cash": inputs.cash,
        "contributions": inputs.contributions,
        "available_symbols": inputs.available_symbols,
        "closes": {
            "days": inputs.days[-CLOSES_TAIL:],
            "levels": inputs.levels[-CLOSES_TAIL:],
        },
        "sources": sources,
    }
    payload: dict[str, Any] = to_jsonable(
        {
            "kind": "weekly",
            "slot": slot,
            "config_version": config_version,
            "code_version": code,
            "inputs": stored_inputs,
            "signals": result.signals,
            "decision": result.decision,
            "ladder_after": result.state_after,
            "advice": result.advice,
        }
    )
    return payload


def weekly_inputs_from_payload(payload: Mapping[str, Any], config: GuardConfig) -> WeeklyInputs:
    """Rebuild the inputs of a stored weekly run (the inverse of ``weekly_payload``)."""
    raw = payload["inputs"]
    funds = FundRegistry({d["symbol"]: Fund(**d) for d in raw["funds"]})
    holdings = tuple(
        Holding(
            account_id=h["account"],
            platform=h["platform"],
            isin=h["isin"],
            ticker=h["ticker"],
            name=h["name"],
            quantity=Decimal(h["quantity"]),
            price=Decimal(h["price"]),
            value_gbp=Decimal(h["value"]),
            excluded=h["excluded"],
        )
        for h in raw["holdings"]
    )
    symbols = raw["available_symbols"]
    return WeeklyInputs(
        config=config,
        days=tuple(date.fromisoformat(d) for d in raw["closes"]["days"]),
        levels=tuple(raw["closes"]["levels"]),
        state_before=LadderState(**raw["ladder_before"]),
        week=raw["week"],
        holdings=holdings,
        cash={k: Decimal(v) for k, v in raw["cash"].items()},
        contributions={k: Decimal(v) for k, v in raw["contributions"].items()},
        funds=funds,
        paused=raw["paused"],
        available_symbols=None if symbols is None else frozenset(symbols),
    )
