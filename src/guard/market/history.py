"""Long GBP history: splice a priority chain of series into one tagged, reproducible series.

A chain is ordered from the most real series (the instrument itself) to the oldest stand-in.
Each older series is rebased to the stitched series at its first day, so the join creates no
return, and every point carries the provenance of the series it came from. USD series are
converted with a USD-per-GBP series, rate series are accrued into a level, and fund fees are
deducted from stand-ins so proxy periods are not flattered. Pure functions: no I/O.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from guard.market.base import Observation

Provenance = Literal["index", "etf", "proxy"]
Currency = Literal["GBP", "USD"]
Kind = Literal["level", "rate_pct"]


class HistoryError(ValueError):
    """The history cannot be built without guessing."""


class HistoryGapError(HistoryError):
    """A gap between consecutive observations exceeds the allowed length."""


class HistorySpliceError(HistoryError):
    """A series cannot be joined to the stitched history."""


@dataclass(frozen=True)
class SeriesInput:
    """One source in a chain. ``annual_fee`` is deducted from the level as a continuous drag."""

    source_id: str
    provenance: Provenance
    observations: Sequence[Observation]
    currency: Currency = "GBP"
    kind: Kind = "level"
    annual_fee: float = 0.0


@dataclass(frozen=True)
class HistoryPoint:
    day: date
    level: float
    provenance: Provenance
    source_id: str


@dataclass(frozen=True)
class History:
    points: tuple[HistoryPoint, ...]
    manifest: dict[str, Any] = field(default_factory=dict)

    def canonical_json(self) -> str:
        body = {
            "manifest": self.manifest,
            "points": [
                [p.day.isoformat(), repr(p.level), p.provenance, p.source_id] for p in self.points
            ],
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    @property
    def data_version(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()[:16]


_Points = list[tuple[date, float]]


def _clean(observations: Sequence[Observation]) -> _Points:
    by_day = {o.day: float(o.value) for o in observations}
    return sorted(by_day.items())


def _content_hash(observations: Sequence[Observation]) -> str:
    text = "\n".join(f"{o.day.isoformat()}:{o.value}" for o in sorted(observations, key=_day))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _day(o: Observation) -> date:
    return o.day


def _rate_to_level(points: _Points) -> _Points:
    """Accrue an annualised percentage rate into a level starting at 100."""
    level = 100.0
    out: _Points = []
    prev_day: date | None = None
    prev_rate = 0.0
    for day, rate in points:
        if prev_day is not None:
            level *= 1.0 + prev_rate / 100.0 * (day - prev_day).days / 365.0
        out.append((day, level))
        prev_day, prev_rate = day, rate
    return out


def _apply_fee(points: _Points, annual_fee: float) -> _Points:
    if annual_fee == 0 or not points:
        return points
    start = points[0][0]
    return [(d, v * math.exp(-annual_fee * (d - start).days / 365.0)) for d, v in points]


def _to_gbp(points: _Points, usd_per_gbp: _Points, max_gap_days: int, source_id: str) -> _Points:
    fx_days = [d for d, _ in usd_per_gbp]
    out: _Points = []
    for day, value in points:
        i = bisect.bisect_right(fx_days, day) - 1
        if i < 0 or (day - fx_days[i]).days > max_gap_days:
            raise HistoryGapError(
                f"{source_id}: no GBP/USD rate within {max_gap_days} days of {day}"
            )
        out.append((day, value / usd_per_gbp[i][1]))
    return out


def _value_on_or_before(points: _Points, day: date) -> float | None:
    days = [d for d, _ in points]
    i = bisect.bisect_right(days, day) - 1
    return points[i][1] if i >= 0 else None


def _prepare(s: SeriesInput, usd_per_gbp: _Points | None, max_gap_days: int) -> _Points:
    points = _clean(s.observations)
    if not points:
        raise HistoryError(f"{s.source_id}: no observations")
    if s.kind == "rate_pct":
        points = _rate_to_level(points)
    points = _apply_fee(points, s.annual_fee)
    if s.currency == "USD":
        if usd_per_gbp is None:
            raise HistoryError(f"{s.source_id}: a USD series needs a USD-per-GBP series")
        points = _to_gbp(points, usd_per_gbp, max_gap_days, s.source_id)
    return points


def _check_gaps(points: Sequence[HistoryPoint], max_gap_days: int) -> None:
    for a, b in zip(points, points[1:], strict=False):
        if (b.day - a.day).days > max_gap_days:
            raise HistoryGapError(
                f"gap of {(b.day - a.day).days} days between {a.day} and {b.day} "
                f"({a.source_id} to {b.source_id}) exceeds {max_gap_days}"
            )


def build_history(
    chain: Sequence[SeriesInput],
    usd_per_gbp: Sequence[Observation] | None = None,
    *,
    max_gap_days: int = 10,
) -> History:
    """Splice ``chain`` (most real series first) into one GBP history."""
    if not chain:
        raise HistoryError("empty chain")
    fx = _clean(usd_per_gbp) if usd_per_gbp is not None else None
    prepared = [(s, _prepare(s, fx, max_gap_days)) for s in chain]

    primary, primary_pts = prepared[0]
    stitched = [HistoryPoint(d, v, primary.provenance, primary.source_id) for d, v in primary_pts]
    roles: dict[str, str] = {primary.source_id: "primary"}

    for s, pts in prepared[1:]:
        anchor = stitched[0]
        if pts[0][0] >= anchor.day:
            roles[s.source_id] = "unused: starts no earlier than the stitched history"
            continue
        base = _value_on_or_before(pts, anchor.day)
        if base is None or pts[-1][0] < anchor.day:
            raise HistorySpliceError(
                f"{s.source_id} does not overlap the stitched history starting {anchor.day}"
            )
        scale = anchor.level / base
        older = [
            HistoryPoint(d, v * scale, s.provenance, s.source_id) for d, v in pts if d < anchor.day
        ]
        stitched = older + stitched
        roles[s.source_id] = f"spliced before {anchor.day}"

    _check_gaps(stitched, max_gap_days)
    manifest: dict[str, Any] = {
        "max_gap_days": max_gap_days,
        "fx_sha": _content_hash(usd_per_gbp) if usd_per_gbp is not None else None,
        "sources": [
            {
                "source_id": s.source_id,
                "provenance": s.provenance,
                "currency": s.currency,
                "kind": s.kind,
                "annual_fee": s.annual_fee,
                "first_day": min(o.day for o in s.observations).isoformat(),
                "last_day": max(o.day for o in s.observations).isoformat(),
                "content_sha": _content_hash(s.observations),
                "role": roles[s.source_id],
            }
            for s in chain
        ],
    }
    return History(tuple(stitched), manifest)


def weekly(history: History, weekday: int) -> tuple[HistoryPoint, ...]:
    """One point per ISO week: the last point on or before that week's ``weekday`` (0 = Monday).

    A week with no point between its Monday and the target day is skipped, not filled.
    """
    days = [p.day for p in history.points]
    last = history.points[-1].day
    monday = history.points[0].day - timedelta(days=history.points[0].day.weekday())
    out: list[HistoryPoint] = []
    while monday <= last:
        i = bisect.bisect_right(days, monday + timedelta(days=weekday)) - 1
        if i >= 0 and days[i] >= monday:
            out.append(history.points[i])
        monday += timedelta(days=7)
    return tuple(out)
