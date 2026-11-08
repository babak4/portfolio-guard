"""Index and signals: the closes behind the three signals and the distance to the drawdown cap."""

from __future__ import annotations

import sqlite3
from datetime import date

from guard.config import GuardConfig
from guard.readapi import _payload as p
from guard.readapi.models import IndexView
from guard.run.core import TRADING_DAYS_PER_WEEK
from guard.storage import read_store, run_store


def _rolling(values: list[float], window: int, fn: str) -> tuple[float | None, ...]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = values[i + 1 - window : i + 1]
        out.append(sum(chunk) / window if fn == "mean" else max(chunk))
    return tuple(out)


def index_view(
    conn: sqlite3.Connection, cfg: GuardConfig, run_id: str | None = None
) -> IndexView | None:
    """From the stored closes of ``run_id``, or of the latest ok weekly run."""
    run = (
        run_store.last_successful_run(conn, "weekly")
        if run_id is None
        else read_store.get_run(conn, run_id)
    )
    payload = None if run is None else p.parse(run.payload)
    if run is None or payload is None or run.status != "ok":
        return None
    closes = [float(x) for x in payload["inputs"]["closes"]["levels"]]
    days = tuple(date.fromisoformat(d) for d in payload["inputs"]["closes"]["days"])
    # The run used the configuration it archived; fall back to today's only if that is gone.
    used = p.archived_config(payload) or cfg
    s = used.signals
    lookback = s.drawdown_lookback_weeks * TRADING_DAYS_PER_WEEK
    if s.trend_method == "sma_days":
        average, note = _rolling(closes, s.trend_window, "mean"), None
    else:
        average = (None,) * len(closes)
        note = f"The {s.trend_window}-month average uses month-end closes and is not drawn."
    high = _rolling(closes, lookback, "max")
    top = high[-1]
    return IndexView(
        run_id=run.run_id,
        slot=run.slot,
        days=days,
        closes=tuple(closes),
        average=average,
        high=high,
        window=s.trend_window,
        average_note=note,
        lookback_days=lookback,
        drawdown=None if top is None else closes[-1] / top - 1.0,
        drawdown_threshold=s.drawdown_threshold,
        drawdown_cap=p.DRAWDOWN_CAP,
        signals=p.signals(payload, used),
        last_close_date=days[-1] if days else None,
    )
