"""This week's advice, laid out for reading."""

from __future__ import annotations

import streamlit as st

from guard import readapi
from guard.ui import components as ui

with ui.page("This week's advice") as ctx:
    runs = [r for r in readapi.history_view(ctx.conn).runs if r.kind == "weekly"]
    if not runs:
        st.info(ui.NO_RUNS)
        st.stop()
    labels = {r.run_id: f"Week of {r.slot} ({r.status})" for r in runs}
    run_id = st.selectbox("Week", list(labels), format_func=labels.__getitem__)
    view = readapi.advice_view(ctx.conn, None if run_id == runs[0].run_id else run_id)
    if view is None:
        st.info(ui.NO_RUNS)
        st.stop()
    ui.light_banner(view.light)
    if not view.has_advice:
        if view.paused and view.run_status == "ok":
            st.warning("Advice is paused, so this week's advice is not shown.")
        else:
            st.error(f"This run {view.run_status}: {view.stop_reason or 'no reason recorded'}.")
        st.stop()
    assert view.decision and view.sleeve and view.ladder_state
    st.caption(
        f"Prices as of the close on {view.last_close_date}. "
        f"Advice reference {(view.advice_hash or '')[:12]} (ties a response to this advice)."
    )
    for reason in view.reasons:
        st.info(reason)

    st.subheader("What to do")
    d = view.decision
    st.write(
        f"Growth share {ui.pct(d.equity_before, 0)} to {ui.pct(d.equity_after, 0)}; "
        f"safe fund share {ui.pct(1 - d.equity_before, 0)} to {ui.pct(1 - d.equity_after, 0)}. "
        f"Ladder decision: {d.kind}. " + " ".join(d.reasons)
    )
    if view.trades:
        st.dataframe(
            [
                {"Action": t.action, "Fund": t.symbol, "Account": t.account_id.upper(),
                 "Amount": ui.money(t.amount)}
                for t in view.trades
            ],
            hide_index=True,
        )  # fmt: skip
    else:
        st.success("No trades this week.")
    for c in view.contributions:
        st.write(
            f"{c.label}: put {ui.money(c.amount)} into {c.symbol} in the {c.account_id.upper()}."
        )

    st.subheader("Where the money is")
    st.dataframe(ui.allocation_rows(view.allocation), hide_index=True)
    if view.unverified_symbols:
        st.caption(
            "Unverified fund data (fees, availability) for: "
            + ", ".join(view.unverified_symbols)
            + ". Check the fund factsheet before relying on it."
        )
    a, b, c_ = st.columns(3)
    a.metric("Growth share now", ui.pct(view.sleeve.growth_actual))
    b.metric("Growth share advised", ui.pct(view.sleeve.growth_advised))
    c_.metric("Growth share after the trades", ui.pct(view.sleeve.growth_after))
    st.caption(
        f"Safe fund share now {ui.pct(view.sleeve.safe_actual)}, advised "
        f"{ui.pct(view.sleeve.safe_advised)}."
    )

    st.subheader("Notes")
    if not view.flags:
        st.write("Nothing to flag.")
    for f in view.flags:
        (st.warning if f.severity == "warn" else st.info)(f.message)

    st.subheader("Safety checks")
    st.dataframe(
        [
            {
                "Check": g.name.replace("_", " "),
                "Result": ("adjusted the advice" if g.adjusted else "passed")
                if g.passed
                else "FAILED",
                "Detail": g.detail,
            }
            for g in view.guardrails
        ],
        hide_index=True,
    )

    st.subheader("The three signals")
    cols = st.columns(3)
    for col, sig in zip(cols, view.signals, strict=True):
        with col:
            ui.show_signal(sig)
    st.caption(
        f"Ladder now: growth {ui.pct(view.ladder_state.equity, 0)}, step {view.ladder_state.step}."
    )
