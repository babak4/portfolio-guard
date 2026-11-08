"""Account totals over time, and the allocation against its target."""

from __future__ import annotations

import streamlit as st

from guard import readapi
from guard.ui import components as ui

with ui.page("Portfolio") as ctx:
    view = readapi.portfolio_view(ctx.conn)
    if not view.series:
        st.info("No account snapshots yet. The daily snapshot or the first weekly run stores them.")
        st.stop()
    st.subheader("Totals over time")
    days = sorted({pt.day for s in view.series for pt in s.points})
    chart: dict[str, list] = {"Date": days}  # type: ignore[type-arg]
    for s in view.series:
        by_day = {pt.day: float(pt.total) for pt in s.points}
        chart[s.account_id.upper()] = [by_day.get(d) for d in days]
    st.line_chart(chart, x="Date", y=[k for k in chart if k != "Date"])
    st.dataframe(
        [
            {
                "Account": s.account_id.upper(),
                "Latest day": str(s.points[-1].day),
                "Total": ui.money(s.points[-1].total),
                "Cash": ui.money(s.points[-1].cash),
            }
            for s in view.series
        ],
        hide_index=True,
    )
    if view.combined:
        st.metric("Everything together", ui.money(view.combined[-1].total))
    st.caption("A gap in the lines is a day with no snapshot; it is never filled in.")

    st.subheader("Allocation against target")
    if view.allocation:
        st.caption(f"From the weekly run {view.allocation_run_id}.")
        st.dataframe(ui.allocation_rows(view.allocation), hide_index=True)
    else:
        st.write("No weekly run yet.")
