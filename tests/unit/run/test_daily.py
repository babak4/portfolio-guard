from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from guard.ladder import LadderState
from guard.run.daily import run_daily
from guard.storage import run_store

from .fakes import make_deps, market, step_for_return, steps_for_week

EVENING = datetime(2026, 11, 10, 22, 30, tzinfo=UTC)  # Tuesday evening
DAY = date(2026, 11, 10)
PREV = date(2026, 11, 9)
WEEK_START = date(2026, 11, 3)


def _check(shock: dict[date, float], data_dir: Path):  # type: ignore[no-untyped-def]
    prices, macro = market(DAY, shock=shock, secondary_shock=shock)
    deps = make_deps(prices, macro)
    run_store.save_ladder_state(deps.ops, 100, "seed", LadderState(0.9, 0), EVENING)
    result = run_daily(deps, EVENING)
    assert run_store.latest_ladder_state(deps.ops) == (100, LadderState(0.9, 0))
    return deps, result


def test_alerts_at_a_5_1_percent_day_and_says_the_fast_path_does_not_apply(data_dir: Path) -> None:
    deps, result = _check({DAY: step_for_return(DAY, PREV, -0.051)}, data_dir)
    assert result.status == "ok"
    assert result.day_return == pytest.approx(-0.051)
    assert [e.kind for e in result.events] == ["day_drop"]
    assert result.events[0].fast_path is False
    assert "does not apply" in result.events[0].message
    deps.close()


def test_silent_at_4_9_percent(data_dir: Path) -> None:
    deps, result = _check({DAY: step_for_return(DAY, PREV, -0.049)}, data_dir)
    assert result.status == "ok" and result.events == ()
    row = run_store.get_run_for_slot(deps.ops, "daily", "2026-11-10")
    assert row is not None and row.status == "ok"
    deps.close()


def test_flags_an_8_percent_week_without_a_day_alert(data_dir: Path) -> None:
    deps, result = _check(
        steps_for_week(date(2026, 11, 5), date(2026, 11, 9), WEEK_START, DAY, -0.081), data_dir
    )
    assert result.week_return == pytest.approx(-0.081)
    assert [e.kind for e in result.events] == ["week_drop"]
    assert result.events[0].fast_path is False
    deps.close()


def test_a_fall_past_the_fast_path_threshold_says_it_applies(data_dir: Path) -> None:
    deps, result = _check(
        steps_for_week(date(2026, 11, 5), date(2026, 11, 9), WEEK_START, DAY, -0.11), data_dir
    )
    kinds = {e.kind for e in result.events}
    assert "week_drop" in kinds
    assert all(e.fast_path and "fast path applies" in e.message for e in result.events)
    deps.close()


def test_flags_a_volatility_spike(data_dir: Path) -> None:
    shock: dict[date, float] = {}
    down = True
    for d in [date(2026, 10, 14) + timedelta(days=i) for i in range(28)]:
        shock[d] = 0.97 if down else 1 / 0.97
        down = not down
    deps, result = _check(shock, data_dir)
    assert [e.kind for e in result.events] == ["volatility"]
    deps.close()


def test_events_carry_no_money_or_account_names(data_dir: Path) -> None:
    deps, result = _check(
        steps_for_week(date(2026, 11, 5), date(2026, 11, 9), WEEK_START, DAY, -0.11), data_dir
    )
    for e in result.events:
        assert "£" not in e.message and "isa" not in e.message.lower()
        assert "gia" not in e.message.lower()
    deps.close()


def test_a_bank_holiday_is_skipped_without_error(data_dir: Path) -> None:
    prices, macro = market(DAY)
    deps = make_deps(prices, macro)
    christmas = datetime(2026, 12, 25, 22, 30, tzinfo=UTC)
    result = run_daily(deps, christmas)
    assert result.status == "skipped" and result.events == ()
    assert prices[0].calls == 0
    assert run_store.get_run_for_slot(deps.ops, "daily", "2026-12-25") is None
    deps.close()


def test_a_stale_second_etf_stops_the_daily_check(data_dir: Path) -> None:
    prices, macro = market(DAY, secondary_last=date(2026, 11, 6))
    deps = make_deps(prices, macro)
    result = run_daily(deps, EVENING)
    assert result.status == "stopped" and "staleness" in (result.reason or "")
    row = run_store.get_run_for_slot(deps.ops, "daily", "2026-11-10")
    assert row is not None and row.status == "stopped"
    deps.close()


def test_same_slot_twice_is_one_run_with_the_same_events(data_dir: Path) -> None:
    deps, first = _check({DAY: step_for_return(DAY, PREV, -0.051)}, data_dir)
    again = run_daily(deps, EVENING)
    assert again.run == first.run and again.events == first.events
    count = deps.ops.execute("SELECT COUNT(*) FROM run WHERE kind='daily'").fetchone()[0]
    assert count == 1
    deps.close()


def test_a_missing_close_for_the_day_stops_with_a_retryable_message(data_dir: Path) -> None:
    prices, macro = market(PREV)  # the day's own close has not been published yet
    deps = make_deps(prices, macro)
    result = run_daily(deps, EVENING)
    assert result.status == "stopped"
    assert "today's close is not available yet" in (result.reason or "")
    row = run_store.get_run_for_slot(deps.ops, "daily", "2026-11-10")
    assert row is not None and row.status == "stopped"
    # once the close appears the same slot is computed and the stopped row is replaced
    deps.price_providers, deps.macro_providers = market(DAY)
    assert run_daily(deps, EVENING).status == "ok"
    deps.close()


def test_a_crash_day_the_second_etf_does_not_confirm_still_alerts_and_says_so(
    data_dir: Path,
) -> None:
    drop = step_for_return(DAY, PREV, -0.06)
    prices, macro = market(DAY, shock={DAY: drop})  # the US ETF has not fallen at all
    deps = make_deps(prices, macro)
    result = run_daily(deps, EVENING)
    assert result.status == "ok" and result.unconfirmed is True
    assert [e.kind for e in result.events] == ["day_drop"]
    assert "not confirmed by the second source" in result.events[0].message
    deps.close()


def test_a_confirmed_alert_does_not_carry_the_unconfirmed_wording(data_dir: Path) -> None:
    deps, result = _check({DAY: step_for_return(DAY, PREV, -0.051)}, data_dir)
    assert result.unconfirmed is False
    assert "not confirmed" not in result.events[0].message
    deps.close()


def test_while_paused_the_check_reports_the_market_but_gives_no_fast_path_guidance(
    data_dir: Path,
) -> None:
    shock = steps_for_week(date(2026, 11, 5), date(2026, 11, 9), WEEK_START, DAY, -0.11)
    prices, macro = market(DAY, shock=shock, secondary_shock=shock)
    deps = make_deps(prices, macro)
    run_store.set_paused(deps.ops, True, EVENING)
    result = run_daily(deps, EVENING)
    assert result.status == "ok" and result.paused is True
    assert result.week_return == pytest.approx(-0.11)
    assert result.events
    for e in result.events:
        assert e.fast_path is False
        assert "advice is paused" in e.message
        assert "fast path applies" not in e.message and "does not apply" not in e.message
    deps.close()


def test_a_second_run_returns_the_stored_unconfirmed_flag(data_dir: Path) -> None:
    prices, macro = market(DAY, shock={DAY: step_for_return(DAY, PREV, -0.06)})
    deps = make_deps(prices, macro)
    first = run_daily(deps, EVENING)
    again = run_daily(deps, EVENING)
    assert again.unconfirmed is True and again.events == first.events
    deps.close()
