"""Record what you did with a week's advice."""

from __future__ import annotations

import streamlit as st

from guard import actions, readapi
from guard.ui import components as ui

with ui.page("Respond") as ctx:
    runs = [
        r for r in readapi.history_view(ctx.conn).runs if r.kind == "weekly" and r.status == "ok"
    ]
    if not runs:
        st.info("There is no advice to respond to yet.")
        st.stop()
    run_id = st.selectbox(
        "Which week",
        [r.run_id for r in runs],
        format_func=lambda i: f"Week of {i.split('-', 1)[1]}",
    )
    assert run_id is not None
    advice = readapi.advice_view(ctx.conn, run_id)
    if advice is not None and advice.has_advice:
        n = len(advice.trades)
        st.write(f"That week's advice: {n} trade{'s' if n != 1 else ''}. Reference "
                 f"{(advice.advice_hash or '')[:12]}.")  # fmt: skip
    with st.form("respond"):
        decision = st.radio(
            "What did you do?", list(ui.DECISION_WORDS), format_func=ui.DECISION_WORDS.__getitem__
        )
        note = st.text_area("Note (optional)", key="note")
        if st.form_submit_button("Record"):
            result = actions.respond(actions.default_conn_factory, run_id, decision, note)  # type: ignore[arg-type]
            ui.flash(result.ok, result.message)
            st.rerun()
    st.subheader("Recorded so far")
    rows = readapi.responses_view(ctx.conn, run_id)
    if not rows:
        st.write("Nothing recorded for this week.")
    else:
        st.dataframe(
            [
                {
                    "Recorded": r.recorded_at,
                    "Decision": ui.DECISION_WORDS[r.decision],
                    "Note": r.note or "",
                    "Current": "yes" if r.current else "replaced",
                }
                for r in rows
            ],
            hide_index=True,
        )
