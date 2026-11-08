"""Every run, its stored record, and whether it still recomputes to the same advice."""

from __future__ import annotations

import streamlit as st

from guard import readapi
from guard.ui import components as ui

REPLAY_WORDS = {
    "identical": "Recomputed from the stored record: identical.",
    "different": "Recomputed from the stored record: DIFFERENT. Look at the differences below.",
    "code_changed": "The program changed since this run, so the recomputed result differs.",
    "unavailable": "This run could not be recomputed.",
}

with ui.page("History and audit") as ctx:
    history = readapi.history_view(ctx.conn)
    if not history.runs:
        st.info(ui.NO_RUNS)
        st.stop()
    st.dataframe(
        [
            {
                "Run": r.run_id,
                "Job": ui.JOB_NAMES.get(r.kind, r.kind),
                "Status": r.status,
                "Light": r.light or "-",
                "Reason": r.reason or "",
                "Finished": r.finished_at or "",
            }
            for r in history.runs
        ],
        hide_index=True,
    )
    run_id = st.selectbox("Look at one run", [r.run_id for r in history.runs])
    detail = readapi.run_detail(ctx.conn, run_id) if run_id else None
    if detail is not None:
        st.subheader(detail.run_id)
        st.write(f"Status: {detail.status}. {detail.reason or ''}")
        if detail.replay is not None:
            text = REPLAY_WORDS.get(detail.replay.status, detail.replay.status)
            (st.success if detail.replay.status == "identical" else st.warning)(text)
            if detail.replay.message:
                st.caption(detail.replay.message)
            if detail.replay.differing:
                st.write("Differs in: " + ", ".join(detail.replay.differing))
        st.caption(
            f"Configuration {detail.config_version or '-'}, code {detail.code_version or '-'}, "
            f"inputs {(detail.inputs_hash or '-')[:12]}, advice {(detail.advice_hash or '-')[:12]}."
        )
        if detail.responses:
            st.write("Your responses (newest first):")
            st.dataframe(
                [
                    {
                        "Recorded": r.recorded_at,
                        "Decision": ui.DECISION_WORDS[r.decision],
                        "Note": r.note or "",
                        "Current": "yes" if r.current else "replaced",
                    }
                    for r in detail.responses
                ],
                hide_index=True,
            )
        if detail.payload is not None:
            with st.expander("Stored record (everything the advice was computed from)"):
                st.json(detail.payload, expanded=False)
    st.subheader("Ladder over time")
    st.dataframe(
        [
            {
                "Week": e.week,
                "Run": e.run_id,
                "Growth share": ui.pct(e.equity, 0),
                "Step": e.step,
            }
            for e in history.ladder
        ],
        hide_index=True,
    )
