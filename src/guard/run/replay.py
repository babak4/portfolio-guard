"""Recompute a stored weekly run from its own record and compare.

Only the stored payload is used: the archived configuration of that run, its fund reference, its
holdings and its closes. The recomputed signals, ladder move and advice are compared, as
canonical JSON, with what was stored.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal

from guard.config import config_version, read_version
from guard.run.audit import (
    canonical_json,
    code_version,
    inputs_hash,
    weekly_inputs_from_payload,
    weekly_payload,
)
from guard.run.core import compute_weekly
from guard.storage import run_store

ReplayStatus = Literal["identical", "different", "code_changed"]
_COMPARED = ("signals", "decision", "ladder_after", "advice")


class ReplayError(ValueError):
    """The run cannot be replayed (unknown, or it has no stored advice)."""


@dataclass(frozen=True)
class ReplayResult:
    """``diff`` maps each differing part to its stored and recomputed JSON text."""

    status: ReplayStatus
    diff: dict[str, dict[str, str]] = field(default_factory=dict)
    message: str | None = None


def replay(
    conn: sqlite3.Connection, run_id: str, *, current_code_version: str | None = None
) -> ReplayResult:
    run = run_store.get_run(conn, run_id)
    if run is None:
        raise ReplayError(f"unknown run {run_id}")
    if run.status != "ok" or run.payload is None:
        raise ReplayError(f"run {run_id} is {run.status}; it has no advice to replay")
    stored: dict[str, Any] = json.loads(run.payload)
    config = read_version(stored["config_version"])
    actual = config_version(config)
    if actual != stored["config_version"]:
        message = (
            f"the archived configuration {stored['config_version']} does not match its version "
            f"hash (it hashes to {actual})"
        )
        return ReplayResult(
            "different",
            {"config_version": {"stored": stored["config_version"], "recomputed": actual}},
            message,
        )
    try:
        inputs = weekly_inputs_from_payload(stored, config)
        recomputed = weekly_payload(
            inputs,
            compute_weekly(inputs),
            slot=stored["slot"],
            config_version=stored["config_version"],
            code=stored["code_version"],
            sources=tuple(stored["inputs"]["sources"]),
        )
    except Exception as e:  # noqa: BLE001 - old records meeting new code must report, not crash
        message = f"the stored run cannot be rebuilt by the current code: {type(e).__name__}: {e}"
        return ReplayResult(
            "code_changed", {"rebuild": {"stored": "", "recomputed": message}}, message
        )

    diff: dict[str, dict[str, str]] = {}
    for part in _COMPARED:
        was, now = canonical_json(stored[part]), canonical_json(recomputed[part])
        if was != now:
            diff[part] = {"stored": was, "recomputed": now}
    if run.inputs_hash != inputs_hash(stored["inputs"]):
        diff["inputs_hash"] = {
            "stored": str(run.inputs_hash),
            "recomputed": inputs_hash(stored["inputs"]),
        }
    table_advice = run_store.get_advice(conn, run_id)
    if table_advice is None or canonical_json(json.loads(table_advice)) != canonical_json(
        stored["advice"]
    ):
        diff["advice_table"] = {
            "stored": str(table_advice),
            "recomputed": canonical_json(stored["advice"]),
        }

    if not diff:
        return ReplayResult("identical")
    current = current_code_version or code_version()
    status: ReplayStatus = "code_changed" if stored["code_version"] != current else "different"
    return ReplayResult(status, diff)
