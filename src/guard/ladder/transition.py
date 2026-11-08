"""One weekly ladder transition. Pure: the same state, signals and week give the same result."""

from __future__ import annotations

from dataclasses import replace

from guard.config.models import Ladder
from guard.ladder.state import Decision, LadderState, Signals

_EPS = 1e-9


def _decision(
    kind: str, before: LadderState, after: LadderState, *reasons: str
) -> tuple[LadderState, Decision]:
    return after, Decision(kind, before.equity, after.equity, after.step, reasons)  # type: ignore[arg-type]


def _reconcile(
    state: LadderState, signals: Signals, week: int, cfg: Ladder
) -> tuple[LadderState, Decision] | None:
    """Walk equity toward the weight of the current step when it is not there yet.

    Used when the signals agree with the step already in force but an earlier move was capped,
    interrupted or cancelled. Equity above the step weight comes down; equity below it (a
    cancelled re-entry) goes back up only while the trend is not negative. Each week moves at most
    the weekly cap and ignores the cooldown, the minimum shift and confirmation, because the
    decision to move was already taken.
    """
    dest = max(cfg.steps[state.step].equity, cfg.equity_floor)
    gap = state.equity - dest
    if gap > _EPS:
        equity = state.equity - min(gap, cfg.max_weekly_change)
        after = replace(state, equity=equity, streak=0, last_change_week=week)
        return _decision("continue", state, after, "remainder of an earlier reduction")
    if gap < -_EPS and not signals.trend.negative:
        equity = state.equity + min(-gap, cfg.max_weekly_change)
        after = replace(state, equity=equity, streak=0, last_change_week=week)
        return _decision("reentry", state, after, "back to the step weight")
    return None


def advance(
    state: LadderState, signals: Signals, week: int, cfg: Ladder
) -> tuple[LadderState, Decision]:
    """Apply one weekly run. ``week`` is any increasing run counter."""
    if any(s.data_gap for s in signals.all()):
        return _decision("data_gap", state, state, "a signal has no data; nothing advised")

    max_step = len(cfg.steps) - 1
    target = min(sum(s.negative for s in signals.all()), max_step)
    fast = signals.weekly_return is not None and signals.weekly_return <= -cfg.fast_path_weekly_drop
    if fast:
        target = max(target, min(state.step + 1, max_step))
    notes: list[str] = []

    reentry_dest = state.reentry_dest
    if reentry_dest is not None:
        if signals.trend.negative or target > state.step:
            state = replace(state, reentry_dest=None, reentry_tranche=0.0)
            notes.append("re-entry cancelled by a negative signal")
        else:
            move = min(state.reentry_tranche, cfg.max_weekly_change, reentry_dest - state.equity)
            equity = state.equity + move
            done = equity >= reentry_dest - _EPS
            after = replace(
                state,
                equity=reentry_dest if done else equity,
                reentry_dest=None if done else reentry_dest,
                reentry_tranche=0.0 if done else state.reentry_tranche,
                last_change_week=week,
                streak=0,
            )
            return _decision("reentry", state, after, "staged return to equities")

    pending = state.pending_equity
    if pending is not None:
        if target == state.step:
            move = min(cfg.max_weekly_change, state.equity - pending)
            equity = state.equity - move
            done = equity <= pending + _EPS
            after = replace(
                state,
                equity=pending if done else equity,
                pending_equity=None if done else pending,
                last_change_week=week,
            )
            return _decision("continue", state, after, "remainder of a capped reduction")

    if target == state.step and state.pending_equity is None:
        reconciled = _reconcile(state, signals, week, cfg)
        if reconciled is not None:
            return reconciled

    if target > state.step:
        streak = state.streak + 1
        if streak < cfg.confirmation_runs and not fast:
            return _decision(
                "hold", state, replace(state, streak=streak), *notes, "awaiting confirmation"
            )
        dest = max(cfg.steps[target].equity, cfg.equity_floor)
        # The minimum applies to the step change itself, so a cancelled re-entry that left equity
        # close to the destination does not strand the ladder one step short of its weight.
        step_shift = max(cfg.steps[state.step].equity, cfg.equity_floor, state.equity) - dest
        shift = max(state.equity - dest, 0.0)
        if step_shift < cfg.min_shift - _EPS:
            return _decision(
                "hold", state, replace(state, streak=streak), *notes, "shift below the minimum"
            )
        if (
            state.last_change_week is not None
            and week - state.last_change_week < cfg.cooldown_weeks
        ):
            return _decision(
                "hold", state, replace(state, streak=streak), *notes, "cooldown after a change"
            )
        move = min(shift, cfg.max_weekly_change)
        remaining = shift - move
        after = replace(
            state,
            equity=state.equity - move,
            step=target,
            streak=0,
            last_change_week=week,
            pending_equity=dest if remaining > _EPS else None,
        )
        if state.pending_equity is not None:
            notes.append("pending reduction replaced by a changed signal")
        return _decision("derisk", state, after, *notes, "fast path" if fast else "confirmed")

    if target < state.step:
        if signals.trend.negative:
            return _decision(
                "hold", state, replace(state, streak=0), *notes, "trend still negative"
            )
        dest = max(cfg.steps[target].equity, cfg.equity_floor)
        if dest <= state.equity + _EPS:
            return _decision(
                "hold", state, replace(state, step=target, streak=0, pending_equity=None), *notes
            )
        tranche = min((dest - state.equity) / cfg.reentry_tranches, cfg.max_weekly_change)
        move = min(tranche, dest - state.equity)
        equity = state.equity + move
        done = equity >= dest - _EPS
        after = replace(
            state,
            equity=dest if done else equity,
            step=target,
            streak=0,
            last_change_week=week,
            reentry_dest=None if done else dest,
            reentry_tranche=0.0 if done else tranche,
            pending_equity=None,
        )
        if state.pending_equity is not None:
            notes.append("pending reduction replaced by a changed signal")
        return _decision("reentry", state, after, *notes, "main signal positive")

    return _decision("hold", state, replace(state, streak=0), *notes, "no change")
