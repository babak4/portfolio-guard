from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from guard.run import weekly
from guard.run.audit import inputs_hash
from guard.run.weekly import run_weekly
from guard.storage import run_store, writer_lock

from .fakes import make_deps, market

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)  # Wednesday noon, London = UTC in November
NEXT = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
LAST = date(2026, 11, 10)


def _count(conn: sqlite3.Connection, table: str, where: str = "1=1") -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0])


@pytest.fixture
def deps(data_dir: Path):  # type: ignore[no-untyped-def]
    prices, macro = market(LAST)
    d = make_deps(prices, macro)
    yield d
    d.close()


def test_full_run_writes_run_ladder_advice_and_an_audit_payload(deps) -> None:  # type: ignore[no-untyped-def]
    result = run_weekly(deps, NOW)
    assert result.status == "ok" and result.advice is not None and result.decision is not None
    conn = deps.ops
    assert _count(conn, "run", "kind='weekly'") == 1
    assert _count(conn, "ladder_state") == 1
    assert _count(conn, "advice") == 1
    run = run_store.get_run_for_slot(conn, "weekly", "2026-11-11")
    assert run is not None and run.status == "ok" and run.code_version == "test-code"
    payload = json.loads(run.payload or "")
    assert payload["slot"] == "2026-11-11"
    assert payload["inputs"]["closes"]["days"][-1] == "2026-11-10"
    assert len(payload["inputs"]["closes"]["days"]) <= 300
    assert payload["inputs"]["holdings"][0]["symbol"] == "VWRP"
    assert payload["inputs"]["ladder_before"]["equity"] == 0.9
    assert payload["signals"]["trend"]["negative"] is False
    assert payload["decision"]["kind"] == "hold"
    assert payload["advice"]["status"] == "advice"
    assert run.inputs_hash == inputs_hash(payload["inputs"])
    assert json.loads(run_store.get_advice(conn, run.run_id) or "") == payload["advice"]
    # the snapshot and raw history were stored as the snapshot job would
    assert _count(conn, "snapshot") == 2 and _count(conn, "raw_history") > 0


def test_same_slot_twice_is_one_run_and_one_advice(deps) -> None:  # type: ignore[no-untyped-def]
    first = run_weekly(deps, NOW)
    calls = deps.price_providers[0].calls
    second = run_weekly(deps, NOW)
    assert second.status == "ok" and second.run == first.run
    assert deps.price_providers[0].calls == calls  # nothing was fetched again
    assert _count(deps.ops, "run", "kind='weekly'") == 1
    assert _count(deps.ops, "advice") == 1
    assert _count(deps.ops, "ladder_state") == 1


def test_stale_data_stops_with_reason_leaves_ladder_alone_and_retry_succeeds(  # type: ignore[no-untyped-def]
    data_dir: Path,
) -> None:
    stale_prices, stale_macro = market(date(2026, 11, 3))
    deps = make_deps(stale_prices, stale_macro)
    result = run_weekly(deps, NOW)
    assert result.status == "stopped" and result.advice is None
    assert "staleness" in (result.reason or "") and "VWRP:XLON" in (result.reason or "")
    row = run_store.get_run_for_slot(deps.ops, "weekly", "2026-11-11")
    assert row is not None and row.status == "stopped" and "staleness" in (row.stop_reason or "")
    assert run_store.latest_ladder_state(deps.ops) is None
    assert _count(deps.ops, "advice") == 0 and _count(deps.ops, "snapshot") == 0

    fresh_prices, fresh_macro = market(LAST)
    deps.price_providers, deps.macro_providers = fresh_prices, fresh_macro
    retry = run_weekly(deps, NOW)
    assert retry.status == "ok"
    assert _count(deps.ops, "run", "kind='weekly'") == 1
    assert run_store.latest_ladder_state(deps.ops) is not None
    deps.close()


def test_etf_return_disagreement_stops_the_run(data_dir: Path) -> None:
    prices, macro = market(LAST, secondary_shock={date(2026, 11, 5): 0.95})
    deps = make_deps(prices, macro)
    result = run_weekly(deps, NOW)
    assert result.status == "stopped"
    assert "live_return" in (result.reason or "")
    assert "VWRP:XLON@yahoo" in (result.reason or "") and "VT:XNYS@yahoo" in (result.reason or "")
    assert run_store.latest_ladder_state(deps.ops) is None
    deps.close()


def test_an_observation_dated_today_never_reaches_the_stored_closes(data_dir: Path) -> None:
    prices, macro = market(LAST, through=date(2026, 11, 11))
    deps = make_deps(prices, macro)
    assert run_weekly(deps, NOW).status == "ok"
    run = run_store.get_run_for_slot(deps.ops, "weekly", "2026-11-11")
    assert json.loads(run.payload or "")["inputs"]["closes"]["days"][-1] == "2026-11-10"  # type: ignore[union-attr]
    deps.close()


def test_trading212_integrity_error_stops_the_run(data_dir: Path) -> None:
    prices, macro = market(LAST)
    deps = make_deps(prices, macro, summary_isa_total=99999.0)
    result = run_weekly(deps, NOW)
    assert result.status == "stopped" and "differ from the account total" in (result.reason or "")
    assert run_store.latest_ladder_state(deps.ops) is None
    assert _count(deps.ops, "snapshot") == 0
    deps.close()


def test_paused_records_a_paused_run_without_advice_and_resume_proceeds(deps) -> None:  # type: ignore[no-untyped-def]
    run_store.set_paused(deps.ops, True, NOW)
    result = run_weekly(deps, NOW)
    assert result.status == "paused" and result.advice is None
    row = run_store.get_run_for_slot(deps.ops, "weekly", "2026-11-11")
    assert row is not None and row.status == "paused"
    assert _count(deps.ops, "advice") == 0 and run_store.latest_ladder_state(deps.ops) is None
    assert deps.price_providers[0].calls == 0

    run_store.set_paused(deps.ops, False, NOW)
    resumed = run_weekly(deps, NOW)
    assert resumed.status == "ok" and _count(deps.ops, "run", "kind='weekly'") == 1


def test_slot_is_the_london_date_of_the_run(deps) -> None:  # type: ignore[no-untyped-def]
    run_store.set_paused(deps.ops, True, NOW)
    late_summer = datetime(2026, 7, 15, 23, 30, tzinfo=UTC)  # 00:30 on the 16th in London
    assert run_weekly(deps, late_summer).slot == "2026-07-16"


def test_ladder_state_is_read_from_the_previous_run_and_the_week_advances(  # type: ignore[no-untyped-def]
    data_dir: Path,
) -> None:
    prices, macro = market(date(2026, 11, 17))
    deps = make_deps(prices, macro)
    first_prices, first_macro = market(LAST)
    deps.price_providers, deps.macro_providers = first_prices, first_macro
    assert run_weekly(deps, NOW).status == "ok"
    week_one = run_store.latest_ladder_state(deps.ops)
    assert week_one is not None
    deps.price_providers, deps.macro_providers = prices, macro
    assert run_weekly(deps, NEXT).status == "ok"
    week_two = run_store.latest_ladder_state(deps.ops)
    assert week_two is not None and week_two[0] == week_one[0] + 1
    second = run_store.get_run_for_slot(deps.ops, "weekly", "2026-11-18")
    payload = json.loads(second.payload or "")  # type: ignore[union-attr]
    assert payload["inputs"]["week"] == week_two[0]
    assert payload["inputs"]["ladder_before"] == payload_after(deps, "2026-11-11")
    deps.close()


def payload_after(deps, slot: str) -> object:  # type: ignore[no-untyped-def]
    row = run_store.get_run_for_slot(deps.ops, "weekly", slot)
    return json.loads(row.payload or "")["ladder_after"]  # type: ignore[union-attr]


def test_an_unexpected_exception_is_recorded_failed_and_reraised(  # type: ignore[no-untyped-def]
    deps, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_inputs: object) -> object:
        raise RuntimeError("bug")

    monkeypatch.setattr(weekly, "compute_weekly", boom)
    with pytest.raises(RuntimeError, match="bug"):
        run_weekly(deps, NOW)
    row = run_store.get_run_for_slot(deps.ops, "weekly", "2026-11-11")
    assert row is not None and row.status == "failed" and row.stop_reason == "RuntimeError"
    assert run_store.latest_ladder_state(deps.ops) is None
    assert _count(deps.ops, "advice") == 0


def test_returns_locked_when_the_run_lock_is_held(  # type: ignore[no-untyped-def]
    deps, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(weekly, "LOCK_WAIT_SECONDS", 0.1)
    with writer_lock("run") as held:
        assert held
        result = run_weekly(deps, NOW)
    assert result.status == "locked" and result.run is None
    assert _count(deps.ops, "run", "kind='weekly'") == 0


def test_contributions_follow_the_configured_accounts(deps) -> None:  # type: ignore[no-untyped-def]
    run_weekly(deps, NOW, isa_lump_sum=Decimal(2000))
    row = run_store.get_run_for_slot(deps.ops, "weekly", "2026-11-11")
    inputs = json.loads(row.payload or "")["inputs"]  # type: ignore[union-attr]
    assert inputs["contributions"] == {"isa": "2000", "gia": "1000"}
    assert inputs["cash"]["isa"] and inputs["cash"]["gia"]
