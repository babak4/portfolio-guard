"""The index behind the signals, and how close the fall is to the drawdown cap."""

from __future__ import annotations

import streamlit as st

from guard import readapi
from guard.ui import components as ui

with ui.page("Index and signals") as ctx:
    view = readapi.index_view(ctx.conn, ctx.cfg)
    if view is None:
        st.info(ui.NO_RUNS)
        st.stop()
    st.caption(f"Global index closes up to {view.last_close_date}, from the run of {view.slot}.")
    avg_label = f"{view.window}-day average"
    high_label = f"{view.lookback_days // 5}-week high"
    st.line_chart(
        {
            "Date": list(view.days),
            "Index close": list(view.closes),
            avg_label: list(view.average),
            high_label: list(view.high),
        },
        x="Date",
        y=["Index close", avg_label, high_label],
    )
    if view.average_note:
        st.caption(view.average_note)

    st.subheader("Fall from the high compared with your limit")
    if view.drawdown is None:
        st.write("Not enough history for the high yet.")
    else:
        fall = -view.drawdown
        left, right = st.columns(2)
        left.metric("Fall from the 52-week high", ui.pct(view.drawdown))
        right.metric("Your worst-case line for the portfolio", f"-{view.drawdown_cap:.0%}")
        st.progress(
            max(0.0, min(1.0, fall / view.drawdown_cap)),
            text=f"{fall / view.drawdown_cap:.0%} of the way to the {view.drawdown_cap:.0%} line",
        )
        st.caption(
            "The index falling this far hurts your portfolio less than the full amount, because "
            "part of it sits in safe funds."
        )

    st.subheader("The three signals")
    for col, sig in zip(st.columns(3), view.signals, strict=True):
        with col:
            ui.show_signal(sig)
