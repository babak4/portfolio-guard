"""Portfolio Guard dashboard: status page (the entry point). Other pages live in ``pages/``."""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from guard import actions, readapi
from guard.ui import components as ui

with ui.page("Status", needs_database=False) as ctx:
    if ctx.conn is None:
        st.info(ui.NO_RUNS)
        st.stop()
    view = readapi.status_view(ctx.conn, ctx.cfg, datetime.now(UTC))
    if view.alert_problem:
        st.error(
            f"Alerts are not reaching you ({view.alert_problem}). Nothing will be pushed to your "
            "phone until this is fixed; see the Alerts section of the README."
        )
    if view.warning:
        st.error(view.warning)
    elif view.week_paused:
        st.warning(
            f"Advice is paused: the weekly run for the week of {view.expected_weekly_slot} "
            "was recorded as paused and gave no advice."
        )
    if not view.has_runs:
        st.info(ui.NO_RUNS)
    ui.light_banner(
        view.light,
        "" if view.latest_weekly is None else f"From the weekly run of {view.latest_weekly.slot}.",
    )

    st.subheader("Pause switch")
    if view.paused:
        st.warning(
            "Advice is paused. The weekly run records that it was paused and gives no advice."
        )
        if st.button("Resume advice", key="resume"):
            result = actions.resume(actions.default_conn_factory)
            ui.flash(result.ok, result.message)
            st.rerun()
    else:
        st.write(
            "Pausing stops all advice until you resume. Use it if you are unsure about the data."
        )
        confirmed = st.checkbox("Yes, pause advice", key="confirm_pause")
        if st.button("Pause advice", key="pause", disabled=not confirmed):
            result = actions.pause(actions.default_conn_factory)
            ui.flash(result.ok, result.message)
            st.rerun()

    st.subheader("Last successful runs")
    st.dataframe(
        [
            {
                "Job": ui.JOB_NAMES[r.kind],
                "Period": r.slot or "never succeeded",
                "Finished": ui.age_text(r.age),
            }
            for r in view.last_runs
        ],
        hide_index=True,
    )
    if view.alert_deliveries:
        with st.expander("Recent alert deliveries"):
            st.dataframe(
                [
                    {k: d.get(k) for k in ("created_at", "kind", "channel", "ok", "error")}
                    for d in view.alert_deliveries
                ],
                hide_index=True,
            )
    st.caption("Advice only. This tool never places trades and is not financial advice.")
