from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from guard import readapi
from guard.config.models import GuardConfig
from guard.storage import open_ops, run_store

from .conftest import NOW, RUN_ID, add_run

AFTER = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


# ---- status ----------------------------------------------------------------------------------
def test_status_empty_database(empty_conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    s = readapi.status_view(empty_conn, cfg, AFTER)
    assert not s.has_runs and s.light is None and not s.paused
    assert all(r.slot is None and r.age is None for r in s.last_runs)
    assert s.weekly_overdue is False  # nothing has ever been expected of an empty system
    assert s.warning is None


def test_status_after_an_ok_weekly_run(conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    s = readapi.status_view(conn, cfg, AFTER)
    weekly = next(r for r in s.last_runs if r.kind == "weekly")
    assert weekly.slot == "2026-11-09" and timedelta(hours=20, minutes=59) < weekly.age < timedelta(
        hours=21
    )
    # one warn flag (unverified fee) makes it amber, by the shared light rule
    assert s.light == "amber" and s.has_runs and not s.weekly_overdue


def test_status_weekly_overdue_warns(conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    late = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)  # the 16th's run is a day overdue
    s = readapi.status_view(conn, cfg, late)
    assert s.weekly_overdue and s.warning is not None and "2026-11-16" in s.warning
    on_time = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)  # Monday: next run not due yet
    assert not readapi.status_view(conn, cfg, on_time).weekly_overdue


def test_status_light_red_when_latest_weekly_stopped(
    conn: sqlite3.Connection, cfg: GuardConfig
) -> None:
    add_run("stopped", "2026-11-16", reason="staleness: VWRP:XLON")
    s = readapi.status_view(conn, cfg, datetime(2026, 11, 18, 13, 0, tzinfo=UTC))
    assert s.light == "red" and s.latest_weekly is not None
    assert s.latest_weekly.reason == "staleness: VWRP:XLON"


def test_status_reports_pause(conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    c = open_ops()
    run_store.set_paused(c, True, NOW)
    c.close()
    s = readapi.status_view(conn, cfg, AFTER)
    assert s.paused and s.light == "amber"


# ---- advice ----------------------------------------------------------------------------------
def test_advice_view_latest(conn: sqlite3.Connection) -> None:
    a = readapi.advice_view(conn)
    assert a is not None and a.has_advice and a.run_id == RUN_ID
    assert a.advice_status == "advice" and a.last_close_date.isoformat() == "2026-11-10"
    assert len(a.advice_hash) == 64 and a.trades == ()
    c = a.contributions[0]
    assert (c.account_id, c.symbol, c.amount) == ("gia", "VWRP", Decimal("1000"))
    assert c.label == "monthly contribution (once a month; skip if already made)"
    assert {r.symbol for r in a.allocation} == {"BRK.B", "CSH2", "VWRP"}
    vwrp = next(r for r in a.allocation if r.symbol == "VWRP")
    assert vwrp.weight == 0.7 and vwrp.target_weight == 0.72 and vwrp.verified is False
    assert a.sleeve.growth_actual == 0.875 and a.sleeve.safe_advised == 0.1
    assert [g.name for g in a.guardrails][:2] == ["pause_switch", "trade_sanity"]
    assert any(f.severity == "warn" for f in a.flags)
    assert a.decision.kind == "hold" and a.decision.equity_after == 0.9
    assert a.ladder_state.equity == 0.9
    assert {s.key for s in a.signals} == {"trend", "drawdown", "volatility"}
    assert "VWRP" in a.unverified_symbols
    assert a.light == "amber"


def test_advice_view_unknown_run(conn: sqlite3.Connection) -> None:
    assert readapi.advice_view(conn, "weekly-1999-01-04") is None


def test_advice_view_empty_database(empty_conn: sqlite3.Connection) -> None:
    assert readapi.advice_view(empty_conn) is None


def test_advice_view_stopped_latest_has_no_advice(conn: sqlite3.Connection) -> None:
    add_run("stopped", "2026-11-16", reason="staleness: VWRP:XLON")
    a = readapi.advice_view(conn)
    assert a is not None and not a.has_advice and a.run_status == "stopped"
    assert a.stop_reason == "staleness: VWRP:XLON" and a.light == "red"
    # the earlier ok run is still reachable by id
    older = readapi.advice_view(conn, RUN_ID)
    assert older is not None and older.has_advice


def test_advice_view_latest_is_hidden_while_paused(conn: sqlite3.Connection) -> None:
    c = open_ops()
    run_store.set_paused(c, True, NOW)
    c.close()
    latest = readapi.advice_view(conn)
    assert latest is not None and not latest.has_advice and latest.paused
    assert readapi.advice_view(conn, RUN_ID).has_advice  # type: ignore[union-attr]


# ---- history ---------------------------------------------------------------------------------
def test_history_view_lists_runs_with_lights(conn: sqlite3.Connection) -> None:
    add_run("failed", "2026-11-16", reason="RuntimeError")
    h = readapi.history_view(conn)
    assert [(r.run_id, r.light) for r in h.runs if r.kind == "weekly"] == [
        ("weekly-2026-11-16", "red"),
        (RUN_ID, "amber"),
    ]
    assert len(h.ladder) == 1


def test_run_detail_replays_identical(conn: sqlite3.Connection) -> None:
    d = readapi.run_detail(conn, RUN_ID, current_code_version="test-code")
    assert d is not None and d.payload is not None and d.payload["kind"] == "weekly"
    assert d.replay is not None and d.replay.status == "identical"
    assert d.advice_hash is not None


def test_run_detail_non_weekly_has_no_replay(conn: sqlite3.Connection) -> None:
    add_run("stopped", "2026-11-16", reason="stale")
    d = readapi.run_detail(conn, "weekly-2026-11-16")
    assert d is not None and d.replay is None and d.payload is None
    assert readapi.run_detail(conn, "nope") is None


# ---- index -----------------------------------------------------------------------------------
def test_index_view_series_and_drawdown(conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    v = readapi.index_view(conn, cfg)
    assert v is not None and len(v.days) == len(v.closes) == len(v.average) == len(v.high)
    assert v.average[0] is None and v.average[-1] is not None  # needs 200 closes first
    assert v.high[-1] == max(v.closes[-cfg.signals.drawdown_lookback_weeks * 5 :])
    assert v.drawdown_cap == 0.20
    assert abs(v.drawdown - (v.closes[-1] / v.high[-1] - 1)) < 1e-12
    assert v.window == cfg.signals.trend_window
    assert [s.key for s in v.signals] == ["trend", "drawdown", "volatility"]
    assert v.signals[1].trigger_level == -cfg.signals.drawdown_threshold


def test_index_view_none_without_runs(empty_conn: sqlite3.Connection, cfg: GuardConfig) -> None:
    assert readapi.index_view(empty_conn, cfg) is None


# ---- portfolio and responses ----------------------------------------------------------------
def test_portfolio_view(conn: sqlite3.Connection) -> None:
    p = readapi.portfolio_view(conn)
    assert {s.account_id for s in p.series} == {"isa", "gia"}
    assert all(isinstance(pt.total, Decimal) for s in p.series for pt in s.points)
    assert p.allocation and {r.symbol for r in p.allocation} >= {"VWRP"}
    assert p.combined[-1].total == sum((s.points[-1].total for s in p.series), Decimal(0))


def test_portfolio_view_empty(empty_conn: sqlite3.Connection) -> None:
    p = readapi.portfolio_view(empty_conn)
    assert p.series == () and p.allocation == () and p.combined == ()


def test_responses_view_marks_superseded(conn: sqlite3.Connection) -> None:
    c = open_ops()
    run_store.record_response(c, RUN_ID, "followed", None, NOW)
    run_store.record_response(c, RUN_ID, "changed", "bought less", NOW)
    c.close()
    rows = readapi.responses_view(conn)
    assert [(r.decision, r.current) for r in rows] == [("changed", True), ("followed", False)]
    assert rows[0].slot == "2026-11-09"
    assert readapi.responses_view(conn, RUN_ID)[0].note == "bought less"
