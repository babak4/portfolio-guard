"""Guards against fooling ourselves: plateaus instead of peaks, walk-forward, timing noise.

Pure functions over trial results and series. Nothing here chooses a parameter silently: each
function returns what it used so the report can show it.
"""

from __future__ import annotations

import itertools
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from guard.backtest import metrics as m
from guard.backtest.engine import BacktestResult, Series
from guard.backtest.sweep import (
    Params,
    TrialLog,
    TrialResult,
    WalkForwardSettings,
    as_log,
    buy_and_hold,
    default_params,
    expand_grid,
    rolling_lag_fast,
    run_trial,
)
from guard.config.models import GuardConfig


class InsufficientHistory(ValueError):
    """The history is too short for the requested analysis (and nothing else went wrong)."""


Cell = tuple[Any, Any]
_DD, _LAG = "max_drawdown", "median_lag"
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


# ---------------------------------------------------------------- plateau analysis


@dataclass(frozen=True)
class PlateauTable:
    """A 2-D table of one metric. ``values[row][col]`` is for ``ys[row]`` and ``xs[col]``;
    each cell is the median over the trials in it (None when no trial has the metric)."""

    x_key: str
    y_key: str
    metric: str
    xs: list[Any]
    ys: list[Any]
    values: list[list[float | None]]


@dataclass(frozen=True)
class PlateauProposal:
    """The centre of the largest plateau, with the single best point shown for contrast."""

    x_key: str
    y_key: str
    x: Any
    y: Any
    size: int
    score: float
    region: list[Cell]
    best_point: Cell
    params: Params
    meets_targets: bool = True


def _matches(t: TrialResult, fixed: Mapping[str, Any] | None) -> bool:
    return all(t.params.get(k) == v for k, v in (fixed or {}).items())


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _cells(
    trials: Sequence[TrialResult], x_key: str, y_key: str, fixed: Mapping[str, Any] | None
) -> tuple[list[Any], list[Any], dict[Cell, dict[str, float | None]]]:
    buckets: dict[Cell, list[TrialResult]] = defaultdict(list)
    for t in trials:
        if x_key in t.params and y_key in t.params and _matches(t, fixed):
            buckets[(t.params[x_key], t.params[y_key])].append(t)
    xs = sorted({c[0] for c in buckets})
    ys = sorted({c[1] for c in buckets})
    keys = {k for ts in buckets.values() for t in ts for k in t.metrics}
    cells = {
        c: {k: _median([v for t in ts if (v := t.metrics.get(k)) is not None]) for k in keys}
        for c, ts in buckets.items()
    }
    return xs, ys, cells


def plateau_table(
    trials: Sequence[TrialResult],
    x_key: str,
    y_key: str,
    metric: str,
    fixed: Mapping[str, Any] | None = None,
) -> PlateauTable:
    xs, ys, cells = _cells(trials, x_key, y_key, fixed)
    values = [[cells.get((x, y), {}).get(metric) for x in xs] for y in ys]
    return PlateauTable(x_key, y_key, metric, xs, ys, values)


def _close(a: float | None, b: float | None, tol: float, floor: float) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol * max(abs(b), floor)


def _similar(
    a: Mapping[str, float | None],
    b: Mapping[str, float | None],
    tol: float,
    floors: Mapping[str, float],
) -> bool:
    """Is cell ``a`` within ``tol`` of centre ``b`` on both max drawdown and lag?"""
    return all(_close(a.get(k), b.get(k), tol, floors.get(k, 0.0)) for k in (_DD, _LAG))


def plateau_score(
    trials: Sequence[TrialResult],
    x_key: str,
    y_key: str,
    centre: Mapping[str, Any],
    tolerance: float,
    floors: Mapping[str, float],
    fixed: Mapping[str, Any] | None = None,
) -> float | None:
    """Share of the centre's grid neighbours (up to eight, diagonals included) whose max drawdown
    and lag are both within ``tolerance`` of the centre's. None when the centre has no result."""
    xs, ys, cells = _cells(trials, x_key, y_key, fixed)
    c = (centre[x_key], centre[y_key])
    if c not in cells or any(cells[c].get(k) is None for k in (_DD, _LAG)):
        return None
    return _score(xs, ys, cells, c, tolerance, floors)


def _score(
    xs: list[Any],
    ys: list[Any],
    cells: dict[Cell, dict[str, float | None]],
    c: Cell,
    tol: float,
    floors: Mapping[str, float],
) -> float:
    ix, iy = xs.index(c[0]), ys.index(c[1])
    neighbours = [
        (xs[ix + dx], ys[iy + dy])
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx or dy) and 0 <= ix + dx < len(xs) and 0 <= iy + dy < len(ys)
    ]
    neighbours = [n for n in neighbours if n in cells]
    if not neighbours:
        return 0.0
    return sum(_similar(cells[n], cells[c], tol, floors) for n in neighbours) / len(neighbours)


def propose_plateau_centre(
    trials: Sequence[TrialResult],
    x_key: str,
    y_key: str,
    *,
    tolerance: float,
    floors: Mapping[str, float],
    fixed: Mapping[str, Any] | None = None,
    objective: str = "calmar",
    max_drawdown_limit: float | None = None,
    lag_limit: float | None = None,
) -> PlateauProposal:
    """The centre of the largest plateau in the (x, y) slice, not the best single point.

    A plateau around a cell is the set of cells reachable through grid-adjacent steps without
    leaving ``tolerance`` of that cell's max drawdown and lag. The largest plateau wins (ties go
    to the higher plateau score, then the higher objective); its centre is the cell nearest the
    plateau's centroid, with ties broken toward the higher objective.

    With ``max_drawdown_limit`` (a negative fraction) and ``lag_limit`` (percent a year) only cells
    that meet both targets can form a plateau, so a large flat region of poor results cannot win
    on size. When no cell meets them the best-scoring region over all cells is returned with
    ``meets_targets`` False.
    """
    xs, ys, cells = _cells(trials, x_key, y_key, fixed)
    usable = {c: v for c, v in cells.items() if v.get(_DD) is not None and v.get(_LAG) is not None}
    if not usable:
        raise ValueError("no trials with drawdown and lag in this slice")

    def meets(v: Mapping[str, float | None]) -> bool:
        dd, lag = v.get(_DD), v.get(_LAG)
        assert dd is not None and lag is not None
        return (max_drawdown_limit is None or dd >= max_drawdown_limit) and (
            lag_limit is None or lag <= lag_limit
        )

    eligible = {c: v for c, v in usable.items() if meets(v)}
    meets_targets = bool(eligible)
    pool = eligible if meets_targets else usable

    def obj(c: Cell) -> float:
        v = usable[c].get(objective)
        return float("-inf") if v is None else v

    def region_of(seed: Cell) -> list[Cell]:
        seen = {seed}
        stack = [seed]
        while stack:
            cx, cy = stack.pop()
            ix, iy = xs.index(cx), ys.index(cy)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                jx, jy = ix + dx, iy + dy
                if not (0 <= jx < len(xs) and 0 <= jy < len(ys)):
                    continue
                n = (xs[jx], ys[jy])
                if (
                    n in pool
                    and n not in seen
                    and _similar(usable[n], usable[seed], tolerance, floors)
                ):
                    seen.add(n)
                    stack.append(n)
        return sorted(seen, key=lambda c: (xs.index(c[0]), ys.index(c[1])))

    best_region: list[Cell] = []
    best_key: tuple[float, ...] | None = None
    for seed in pool:
        reg = region_of(seed)
        key = (len(reg), _score(xs, ys, usable, seed, tolerance, floors), obj(seed))
        if best_key is None or key > best_key:
            best_key, best_region = key, reg
    gx = sum(xs.index(c[0]) for c in best_region) / len(best_region)
    gy = sum(ys.index(c[1]) for c in best_region) / len(best_region)

    def centre_key(c: Cell) -> tuple[float, float]:
        d = (xs.index(c[0]) - gx) ** 2 + (ys.index(c[1]) - gy) ** 2
        return (d, -obj(c))

    centre = min(best_region, key=centre_key)
    best_point = max(usable, key=lambda c: (obj(c), -xs.index(c[0]), -ys.index(c[1])))
    return PlateauProposal(
        x_key=x_key,
        y_key=y_key,
        x=centre[0],
        y=centre[1],
        size=len(best_region),
        score=_score(xs, ys, usable, centre, tolerance, floors),
        region=best_region,
        best_point=best_point,
        params={x_key: centre[0], y_key: centre[1]},
        meets_targets=meets_targets,
    )


def joint_plateau_score(
    trials: Sequence[TrialResult],
    axes: Sequence[str],
    centre: Mapping[str, Any],
    tolerance: float,
    floors: Mapping[str, float],
    fixed: Mapping[str, Any] | None = None,
) -> float | None:
    """Plateau score of ``centre`` with every axis in ``axes`` varied at once.

    The neighbours are all grid points one step away on any subset of the axes (diagonals
    included). Other parameters are held at ``fixed``. None when the centre has no result.
    """
    buckets: dict[tuple[Any, ...], list[TrialResult]] = defaultdict(list)
    for t in trials:
        if all(a in t.params for a in axes) and _matches(t, fixed):
            buckets[tuple(t.params[a] for a in axes)].append(t)
    cells = {
        k: {
            m_: _median([v for t in ts if (v := t.metrics.get(m_)) is not None])
            for m_ in (_DD, _LAG)
        }
        for k, ts in buckets.items()
    }
    key = tuple(centre[a] for a in axes)
    if key not in cells or any(cells[key][m_] is None for m_ in (_DD, _LAG)):
        return None
    levels = [sorted({k[i] for k in cells}) for i in range(len(axes))]
    pos = [lv.index(key[i]) for i, lv in enumerate(levels)]
    steps = [
        [j for j in (p - 1, p, p + 1) if 0 <= j < len(lv)]
        for p, lv in zip(pos, levels, strict=True)
    ]
    neighbours = []
    for combo in itertools.product(*steps):
        if list(combo) == pos:
            continue
        n = tuple(levels[i][j] for i, j in enumerate(combo))
        if n in cells:
            neighbours.append(n)
    if not neighbours:
        return 0.0
    return sum(_similar(cells[n], cells[key], tolerance, floors) for n in neighbours) / len(
        neighbours
    )


# ---------------------------------------------------------------- ranges


@dataclass(frozen=True)
class Range:
    min: float
    median: float
    max: float


def make_range(values: Sequence[float | None]) -> Range | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return Range(min(vals), statistics.median(vals), max(vals))


def ranges_by(trials: Sequence[TrialResult], key: str, metric: str) -> dict[Any, Range]:
    """Min/median/max of ``metric`` across the trials for each value of parameter ``key``."""
    groups: dict[Any, list[float | None]] = defaultdict(list)
    for t in trials:
        if key in t.params:
            groups[t.params[key]].append(t.metrics.get(metric))
    out = {}
    for k, vals in groups.items():
        r = make_range(vals)
        if r is not None:
            out[k] = r
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))


# ---------------------------------------------------------------- walk-forward


def choose_parameters(
    scored: Sequence[tuple[Params, float | None]], default: Params, tie_tolerance: float
) -> tuple[Params, float | None]:
    """The best-scoring setting; scores within ``tie_tolerance`` of the best tie, and the tie goes
    to the setting differing from ``default`` on the fewest axes (then the earliest listed).
    When no setting has a score, the result is ``default`` on the grid's axes with score None."""
    usable = [(p, s) for p, s in scored if s is not None]
    if not usable:
        # No training score at all: keep the configured setting rather than an arbitrary point.
        return {k: default.get(k, v) for k, v in scored[0][0].items()}, None
    top = max(s for _, s in usable)
    tied = [(p, s) for p, s in usable if top - s <= tie_tolerance]

    def distance(p: Params) -> int:
        return sum(1 for k, v in default.items() if p.get(k, v) != v)

    p, s = min(tied, key=lambda ps: distance(ps[0]))
    return dict(p), s


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def walk_forward_windows(
    first: date, last: date, *, train_years: int, test_years: int, min_test_days: int
) -> list[tuple[date, date]]:
    """``(train_end, test_end)`` boundaries. Training always starts at ``first`` (anchored); each
    test block starts where the previous one ended; a final short block is kept only if it spans
    at least ``min_test_days``."""
    out: list[tuple[date, date]] = []
    train_end = _add_years(first, train_years)
    while train_end < last:
        test_end = _add_years(train_end, test_years)
        if test_end > last:
            if (last - train_end).days >= min_test_days:
                out.append((train_end, last))
            break
        out.append((train_end, test_end))
        train_end = test_end
    return out


@dataclass(frozen=True)
class Fold:
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    params: Params
    train_score: float | None
    test_return: float | None = None
    default_test_return: float | None = None
    no_valid_training_score: bool = False


@dataclass(frozen=True)
class WalkForwardResult:
    """The stitched out-of-sample record. ``dates[0]`` is the close before the first test block."""

    folds: list[Fold]
    dates: list[date]
    index: list[float]
    benchmark_index: list[float]
    metrics: dict[str, float | None]
    benchmark_metrics: dict[str, float | None]


def _basic_metrics(dates: Sequence[date], index: Sequence[float]) -> dict[str, float | None]:
    return {
        "cagr": m.cagr(dates, index),
        "max_drawdown": m.max_drawdown(dates, index).depth,
        "calmar": m.calmar(dates, index),
        "sortino": m.sortino(index),
        "volatility": m.volatility(index),
    }


def _slice(series: Series, last: date) -> dict[date, float]:
    return {d: v for d, v in series.items() if d <= last}


def _block_return(
    res: BacktestResult, pos: Mapping[date, int], test_dates: Sequence[date]
) -> float:
    first, last = pos[test_dates[0]], pos[test_dates[-1]]
    return res.index[last] / res.index[first - 1] - 1.0


def walk_forward(
    equity: Series,
    safe: Series,
    grid: Mapping[str, Sequence[Any]],
    cfg: GuardConfig,
    *,
    settings: WalkForwardSettings,
    data_version: str,
    config_version: str = "",
    trial_log: TrialLog | None = None,
    lag_years: int = 3,
    anchor: Params | None = None,
) -> WalkForwardResult:
    """Anchored expanding-window walk-forward with the stitched out-of-sample index.

    For each block: choose parameters on the data up to the training end only (best
    ``settings.objective``, ties toward the configured setting), then run those parameters and
    keep only the next ``test_years`` of the run. The strategy has seen no data after the
    training end when its parameters are picked; blocks are disjoint and chained so the stitched
    index is time-weighted. ``settings.pinned`` axes are held at one value.

    Limitation: the engine cannot start from a given ladder state, so each fold's strategy is
    replayed from the start of the history with that fold's parameters. Its ladder state at the
    block start is what those parameters would have produced, not the state the previous fold's
    parameters actually left. The configured setting is run the same way on every block
    (``default_test_return``) as the comparison. Raises ``InsufficientHistory`` when the history
    cannot hold one training window plus one test block.
    """
    dates = sorted(set(equity) & set(safe))
    if len(dates) < 2:
        raise InsufficientHistory("fewer than two dates in common")
    full_grid = {**grid, **{k: [v] for k, v in settings.pinned.items()}}
    points = expand_grid(full_grid)
    anchor = dict(anchor) if anchor is not None else default_params(cfg)
    windows = walk_forward_windows(
        dates[0],
        dates[-1],
        train_years=settings.train_years,
        test_years=settings.test_years,
        min_test_days=settings.min_test_days,
    )
    out_dates: list[date] = []
    out_idx: list[float] = []
    out_bench: list[float] = []
    folds: list[Fold] = []
    for train_bound, test_bound in windows:
        train_end = max(d for d in dates if d <= train_bound)
        test_dates = [d for d in dates if train_end < d <= test_bound]
        if not test_dates:
            continue
        eq_tr, sf_tr = _slice(equity, train_end), _slice(safe, train_end)
        bench_tr = buy_and_hold(eq_tr, sf_tr, cfg, data_version=data_version)
        scored: list[tuple[Params, float | None]] = []
        for p in points:
            trial, _ = run_trial(
                eq_tr,
                sf_tr,
                p,
                cfg,
                benchmark=bench_tr,
                data_version=data_version,
                config_version=config_version,
                lag_years=lag_years,
                log=trial_log,
                stage="walk_forward_train",
            )
            scored.append((p, trial.metrics.get(settings.objective)))
        chosen, score = choose_parameters(scored, anchor, settings.tie_tolerance)

        test_end = test_dates[-1]
        eq_te, sf_te = _slice(equity, test_end), _slice(safe, test_end)
        bench_te = buy_and_hold(eq_te, sf_te, cfg, data_version=data_version)
        _, res = run_trial(
            eq_te,
            sf_te,
            chosen,
            cfg,
            benchmark=bench_te,
            data_version=data_version,
            config_version=config_version,
            lag_years=lag_years,
            log=trial_log,
            stage="walk_forward_test",
        )
        _, default_res = run_trial(
            eq_te,
            sf_te,
            anchor,
            cfg,
            benchmark=bench_te,
            data_version=data_version,
            config_version=config_version,
            lag_years=lag_years,
            log=trial_log,
            stage="walk_forward_default_test",
        )
        pos = {d: i for i, d in enumerate(res.dates)}
        if not out_dates:
            out_dates, out_idx, out_bench = [train_end], [1.0], [1.0]
        for d in test_dates:
            k = pos[d]
            out_dates.append(d)
            out_idx.append(out_idx[-1] * res.index[k] / res.index[k - 1])
            out_bench.append(out_bench[-1] * bench_te.index[k] / bench_te.index[k - 1])
        folds.append(
            Fold(
                dates[0],
                train_end,
                test_dates[0],
                test_end,
                chosen,
                score,
                test_return=_block_return(res, pos, test_dates),
                default_test_return=_block_return(default_res, pos, test_dates),
                no_valid_training_score=score is None,
            )
        )
    if len(out_dates) < 2:
        raise InsufficientHistory("not enough history for even one walk-forward block")
    metrics = _basic_metrics(out_dates, out_idx)
    bench_metrics = _basic_metrics(out_dates, out_bench)
    lags = rolling_lag_fast(out_dates, out_idx, out_bench, lag_years)
    metrics["median_lag"] = statistics.median(v for _, v in lags) if lags else None
    metrics["worst_lag"] = max((v for _, v in lags), default=None)
    metrics["cagr_lag_pct"] = ((bench_metrics["cagr"] or 0.0) - (metrics["cagr"] or 0.0)) * 100.0
    return WalkForwardResult(folds, out_dates, out_idx, out_bench, metrics, bench_metrics)


# ---------------------------------------------------------------- weekday and offsets


@dataclass(frozen=True)
class WeekdayVerdict:
    """``determined`` is False when there is not enough data to say; then ``wednesday_stays`` is
    False too and the text says so. ``offsets_used`` counts the start offsets that were paired."""

    wednesday_stays: bool
    beaten_by: list[int]
    text: str
    determined: bool = True
    offsets_used: int = 0


@dataclass(frozen=True)
class WeekdayOffsetResult:
    """Results for each weekday and weekly start offset, summarised as ranges."""

    runs: list[tuple[int, int, dict[str, float | None]]]
    per_weekday: dict[int, dict[str, Range]] = field(default_factory=dict)
    overall: dict[str, Range] = field(default_factory=dict)

    @classmethod
    def from_runs(
        cls, runs: Sequence[tuple[int, int, dict[str, float | None]]]
    ) -> WeekdayOffsetResult:
        keys = sorted({k for _, _, mt in runs for k in mt})
        per: dict[int, dict[str, Range]] = {}
        for wd in sorted({w for w, _, _ in runs}):
            sub = [mt for w, _, mt in runs if w == wd]
            per[wd] = {
                k: r for k in keys if (r := make_range([mt.get(k) for mt in sub])) is not None
            }
        overall = {
            k: r for k in keys if (r := make_range([mt.get(k) for _, _, mt in runs])) is not None
        }
        return cls(list(runs), per, overall)


def weekday_offsets(
    equity: Series,
    safe: Series,
    cfg: GuardConfig,
    *,
    offsets: int = 4,
    params: Mapping[str, Any] | None = None,
    data_version: str = "",
    config_version: str = "",
    trial_log: TrialLog | None = None,
    weekdays: Sequence[int] = (0, 1, 2, 3, 4),
    lag_years: int = 3,
) -> WeekdayOffsetResult:
    """Run every weekday at every weekly start offset (the run begins ``offset`` weeks later).

    The spread across offsets is the timing luck of the first decision day; a weekday only
    counts as better if it clears that spread.
    """
    dates = sorted(set(equity) & set(safe))
    base = dict(params or {})
    runs: list[tuple[int, int, dict[str, float | None]]] = []
    for wd in weekdays:
        for off in range(offsets):
            cutoff = dates[0] + timedelta(days=7 * off)
            eq = {d: v for d, v in equity.items() if d >= cutoff}
            sf = {d: v for d, v in safe.items() if d >= cutoff}
            dv = f"{data_version}:offset{off}"
            bench = buy_and_hold(eq, sf, cfg, data_version=dv)
            trial, _ = run_trial(
                eq,
                sf,
                {**base, "weekday": wd},
                cfg,
                benchmark=bench,
                data_version=dv,
                config_version=config_version,
                lag_years=lag_years,
                log=trial_log,
                stage="weekday_offsets",
            )
            runs.append((wd, off, trial.metrics))
    return WeekdayOffsetResult.from_runs(runs)


def _by_offset(result: WeekdayOffsetResult, weekday: int, metric: str) -> dict[int, float]:
    return {
        off: v for wd, off, mt in result.runs if wd == weekday and (v := mt.get(metric)) is not None
    }


def wednesday_verdict(
    result: WeekdayOffsetResult,
    metric: str = "calmar",
    *,
    higher_is_better: bool = True,
    reference: int = 2,
) -> WeekdayVerdict:
    """Wednesday stays unless another weekday beats it beyond noise, judged on paired results.

    Each other weekday is compared with Wednesday at the same start offset. It beats Wednesday if
    the median of those paired differences exceeds their own spread (maximum minus minimum), so a
    start-offset swing that moves both days together does not count as noise. Without a
    Wednesday result, or with fewer than two paired offsets, the verdict is undetermined.
    """
    name = _WEEKDAY_NAMES[reference]
    ref = _by_offset(result, reference, metric)
    if not ref:
        return WeekdayVerdict(
            False, [], f"Undetermined: there is no {name} result on {metric}.", determined=False
        )
    sign = 1.0 if higher_is_better else -1.0
    beaten: list[int] = []
    details: list[str] = []
    offsets_used = 0
    for wd in sorted(result.per_weekday):
        if wd == reference:
            continue
        other = _by_offset(result, wd, metric)
        diffs = [sign * (other[o] - ref[o]) for o in sorted(set(other) & set(ref))]
        offsets_used = max(offsets_used, len(diffs))
        if len(diffs) < 2:
            continue
        med, spread = statistics.median(diffs), max(diffs) - min(diffs)
        details.append(f"{_WEEKDAY_NAMES[wd]} minus {name}: median {med:+.3f}, spread {spread:.3f}")
        if med > spread:
            beaten.append(wd)
    if offsets_used < 2:
        return WeekdayVerdict(
            False,
            [],
            f"Undetermined: fewer than two start offsets pair {name} with another weekday on "
            f"{metric}.",
            determined=False,
            offsets_used=offsets_used,
        )
    basis = (
        f"paired differences against {name} at the same offset over {offsets_used} start offsets "
        f"({'; '.join(details)})"
    )
    if not beaten:
        text = f"{name} stays: no other weekday beats it on {metric} beyond noise; {basis}."
    else:
        names = ", ".join(_WEEKDAY_NAMES[w] for w in beaten)
        text = (
            f"{name} is clearly beaten on {metric} by {names}: the median paired difference "
            f"exceeds its spread; {basis}."
        )
    return WeekdayVerdict(not beaten, beaten, text, offsets_used=offsets_used)


__all__ = [
    "Fold",
    "InsufficientHistory",
    "PlateauProposal",
    "PlateauTable",
    "Range",
    "WalkForwardResult",
    "WeekdayOffsetResult",
    "WeekdayVerdict",
    "as_log",
    "choose_parameters",
    "joint_plateau_score",
    "make_range",
    "plateau_score",
    "plateau_table",
    "propose_plateau_centre",
    "ranges_by",
    "walk_forward",
    "walk_forward_windows",
    "weekday_offsets",
    "wednesday_verdict",
]
