"""The Phase 0 report: a markdown document that lets the owner choose parameters, floor and cap.

``render_phase0_report`` is a pure function of ``ReportInputs``. Plain-language headlines come
first, tables follow, and the decisions that need the owner come last. Numbers are never
rounded to hide a miss: a target is met only if the figure is on the right side of it.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from guard.backtest import metrics as m
from guard.backtest.engine import BacktestResult
from guard.backtest.scenarios import DEFAULT_WINDOWS, CrisisWindow, ScenarioSummary, crisis_summary
from guard.backtest.sweep import BacktestSettings, Params, TrialResult, rolling_lag_fast
from guard.backtest.validation import (
    PlateauProposal,
    PlateauTable,
    WalkForwardResult,
    WeekdayOffsetResult,
    ranges_by,
    wednesday_verdict,
)
from guard.config.models import GuardConfig
from guard.market.history import History

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
IN_SAMPLE = "IN-SAMPLE (selected on this history)"
BANNER = (
    "> **Draft: proxy data.** {legs} A proxy is a stand-in series, not the instrument itself. "
    "Read every figure as provisional until the index file replaces the stand-ins."
)
# A window counts as covered when the data reaches within this many days of each end, so a
# window that starts on a holiday or ends on a weekend is not dropped for a calendar accident.
EDGE_SLACK_DAYS = 5
PROXY_MAJORITY = 0.5
SUBSTITUTIONS = {
    "sonia_rate_pct": (
        "FRED UK 3-month interbank rate IR3TIB01GBM156N, which carries a credit spread over "
        "SONIA, notably in 2008"
    ),
    "bank_rate_pct": "an official UK short-term policy rate standing in for money-market returns",
    "msci_acwi_nr_usd": (
        "MSCI ACWI net total return index (the owner's file), in US dollars converted to "
        "pounds, less an assumed annual fee; an index, not the fund"
    ),
    "vt_usd": "VT, a US-listed all-world ETF in US dollars converted to pounds",
    "sp500_tr_usd": (
        "S&P 500 total return index in US dollars converted to pounds; US large companies "
        "only, not global equity"
    ),
    "gold_usd": "gold futures price in US dollars converted to pounds, not the gold ETC itself",
}


@dataclass(frozen=True)
class PlateauSection:
    """Tables of several metrics over one parameter pair, and the plateau centre proposed."""

    x_key: str
    y_key: str
    tables: dict[str, PlateauTable]
    proposal: PlateauProposal


@dataclass(frozen=True)
class ReportInputs:
    """Everything the report prints. ``strategies`` maps a label to a run; the first entry is the
    configured setting and is the one the headline numbers use."""

    settings: BacktestSettings
    cfg: GuardConfig
    config_version: str
    data_version: str
    histories: dict[str, History]
    equity_key: str
    strategies: dict[str, BacktestResult]
    buy_and_hold: BacktestResult
    single_tracker: BacktestResult
    benchmarks: dict[str, BacktestResult]
    floor_only: BacktestResult
    realistic: BacktestResult
    safe_strategies: dict[str, BacktestResult]
    safe_buy_and_hold: dict[str, BacktestResult]
    trials: list[TrialResult]
    trial_count: int
    weekday: WeekdayOffsetResult
    plateaus: list[PlateauSection]
    chosen_params: Params
    walk_forward: WalkForwardResult | None
    quick: bool
    windows: Sequence[CrisisWindow] = field(default=DEFAULT_WINDOWS)
    primary_safe_key: str = ""
    runs_this_invocation: int = 0
    distinct_combinations: int = 0
    cumulative_runs: int = 0
    carried_forward: dict[str, float] = field(default_factory=dict)
    rebalanced_reference: BacktestResult | None = None
    walk_forward_note: str = ""
    chosen_joint_score: float | None = None
    chosen_axes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- formatting


def pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def num(x: float | None, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _rng(r: object, fmt: str = "pct") -> str:
    if r is None:
        return "n/a"
    f = pct if fmt == "pct" else num
    return f"{f(r.min)} / {f(r.median)} / {f(r.max)}"  # type: ignore[attr-defined]


# ---------------------------------------------------------------- calculations


def proxy_share(histories: dict[str, History]) -> float:
    pts = [p for h in histories.values() for p in h.points]
    return sum(1 for p in pts if p.provenance == "proxy") / len(pts) if pts else 0.0


def window_share(
    h: History, start: date, end: date, *, is_match: Callable[[str], bool]
) -> float | None:
    """Share of the time inside ``start``..``end`` covered by points whose provenance matches.

    Each point stands for the span until the next point, so a sparse stretch weighs by the time
    it covers, not by how many points it has. None when the history has no span in the window.
    """
    pts = h.points
    hit = total = 0
    for a, b in zip(pts, pts[1:], strict=False):
        lo, hi = max(a.day, start), min(b.day, end)
        if hi <= lo:
            continue
        span = (hi - lo).days
        total += span
        hit += span if is_match(a.provenance) else 0
    return hit / total if total else None


def window_proxy_share(h: History, start: date, end: date) -> float | None:
    return window_share(h, start, end, is_match=lambda prov: prov == "proxy")


def _is_stand_in(prov: str) -> bool:
    return prov != "etf"  # anything but the instrument itself


def window_covered(w: CrisisWindow, first: date, last: date) -> bool:
    """Is the whole window inside the data (give or take a weekend or holiday at each end)?"""
    slack = timedelta(days=EDGE_SLACK_DAYS)
    return first <= w.start + slack and last >= w.end - slack


def _aligned_range(inputs: ReportInputs) -> tuple[date, date]:
    d = inputs.buy_and_hold.dates
    return d[0], d[-1]


def _untested(inputs: ReportInputs) -> list[str]:
    first, last = _aligned_range(inputs)
    return [w.name for w in inputs.windows if not window_covered(w, first, last)]


def _frontier(inputs: ReportInputs, strategy: BacktestResult) -> dict[str, ScenarioSummary | None]:
    out = crisis_summary(strategy, inputs.buy_and_hold, inputs.floor_only, inputs.windows)
    missing = set(_untested(inputs))
    return {k: (None if k in missing else v) for k, v in out.items()}


def _equity_drawdowns(inputs: ReportInputs) -> dict[str, float | None]:
    t = inputs.single_tracker
    out = crisis_summary(t, t, t, inputs.windows)
    missing = set(_untested(inputs))
    return {
        k: (v.achieved_max_drawdown if v and k not in missing else None) for k, v in out.items()
    }


def _legs(inputs: ReportInputs) -> tuple[History, History | None]:
    equity = inputs.histories[inputs.equity_key]
    key = inputs.primary_safe_key
    if key not in inputs.histories:
        key = next(
            (
                k
                for k in ("money_market", *inputs.histories)
                if k != inputs.equity_key and k in inputs.histories
            ),
            "",
        )
    return equity, inputs.histories.get(key)


def _window_leg_shares(inputs: ReportInputs, w: CrisisWindow) -> tuple[float | None, float | None]:
    equity, safe = _legs(inputs)
    return (
        window_proxy_share(equity, w.start, w.end),
        window_proxy_share(safe, w.start, w.end) if safe is not None else None,
    )


def drawdown_verdict(met: bool, equity_proxy: float | None, safe_proxy: float | None) -> str:
    """'met' or 'not met', with a warning when a met window rests mostly on stand-in data."""
    if not met:
        return "not met"
    if (equity_proxy or 0.0) > PROXY_MAJORITY or (safe_proxy or 0.0) > PROXY_MAJORITY:
        return "met on PROXY data only"
    return "met"


def _window_verdict(inputs: ReportInputs, name: str, s: ScenarioSummary) -> tuple[bool, str]:
    w = next(x for x in inputs.windows if x.name == name)
    eq_p, sf_p = _window_leg_shares(inputs, w)
    met = s.achieved_max_drawdown >= -inputs.settings.targets.crisis_max_drawdown
    return met, drawdown_verdict(met, eq_p, sf_p)


def _not_tested_line(inputs: ReportInputs) -> str:
    names = _untested(inputs)
    return f"NOT TESTED: {', '.join(names)}" if names else ""


def _lags(
    inputs: ReportInputs, strategy: BacktestResult, reference: BacktestResult | None = None
) -> list[float]:
    years = inputs.settings.targets.lag_window_years
    ref = reference or inputs.buy_and_hold
    return [v for _, v in rolling_lag_fast(strategy.dates, strategy.index, ref.index, years)]


def _lag_stats(lags: list[float], limit: float) -> tuple[float, float, float, float] | None:
    if not lags:
        return None
    worse = sum(1 for v in lags if v > limit) / len(lags)
    return min(lags), statistics.median(lags), max(lags), worse


def _core_row(label: str, r: BacktestResult) -> list[str]:
    dd = m.max_drawdown(r.dates, r.index)
    ttr = m.time_to_recover(r.dates, r.index)
    return [
        label,
        pct(m.cagr(r.dates, r.index)),
        pct(dd.depth),
        num(m.calmar(r.dates, r.index)),
        pct(m.volatility(r.index)),
        num(m.sharpe(r.index)),
        num(m.sortino(r.index)),
        "never" if ttr is None else f"{ttr} days",
        num(m.turnover(r.total_traded, r.dates, r.values)),
    ]


CORE_HEADERS = [
    "",
    "CAGR",
    "Max drawdown",
    "Calmar",
    "Volatility",
    "Sharpe",
    "Sortino",
    "Time to recover",
    "Turnover (x/yr)",
]


def _years(r: BacktestResult) -> float:
    return (r.dates[-1] - r.dates[0]).days / m.DAYS_PER_YEAR


# ---------------------------------------------------------------- sections


def _summary(inputs: ReportInputs) -> str:
    t = inputs.settings.targets
    cap = t.crisis_max_drawdown
    first = next(iter(inputs.strategies))
    strat = inputs.strategies[first]
    fr = _frontier(inputs, strat)
    tested = {k: v for k, v in fr.items() if v is not None}
    untested = [w.name for w in inputs.windows if w.name not in tested]
    names = ", ".join(w.name for w in inputs.windows)
    lines = [
        f"Configured setting: **{first}**. Targets from the requirements: drawdown within about "
        f"-{cap:.0%} in each of {names}, and a lag of at most "
        f"{t.annual_lag_pct:g}% a year over rolling {t.lag_window_years}-year windows."
    ]
    if untested:
        lines.append(
            f"**NOT TESTED: {', '.join(untested)}.** "
            + ("These windows are" if len(untested) > 1 else "This window is")
            + " not fully inside the aligned data, so nothing is claimed about "
            + ("them." if len(untested) > 1 else "it.")
        )
    drawdown_ok = False
    if tested:
        verdicts = {k: _window_verdict(inputs, k, v) for k, v in tested.items()}
        met = [k for k, (ok, _) in verdicts.items() if ok]
        detail = "; ".join(
            f"{k}: {pct(v.achieved_max_drawdown)} against {pct(v.buy_and_hold_max_drawdown)} "
            f"for buy-and-hold ({verdicts[k][1]})"
            for k, v in tested.items()
        )
        lines.append(
            f"The drawdown target is met in {len(met)} of {len(tested)} windows tested. "
            + detail
            + "."
        )
        drawdown_ok = len(met) == len(tested)
    else:
        lines.append(
            f"The drawdown target cannot be tested: none of the {names} windows lie fully "
            "inside this history."
        )
    stats = _lag_stats(_lags(inputs, strat), t.annual_lag_pct)
    lag_ok = False
    if stats is None:
        lines.append(
            f"The lag target cannot be tested: the history is shorter than "
            f"{t.lag_window_years} years."
        )
    else:
        lo, med, hi, worse = stats
        lag_ok = hi <= t.annual_lag_pct
        lines.append(
            f"Rolling {t.lag_window_years}-year lag against buy-and-hold (the starting mix, never "
            f"rebalanced): worst {hi:.2f}% a year, "
            f"median {med:.2f}%, best {lo:.2f}%; {worse:.0%} of windows lag by more than "
            f"{t.annual_lag_pct:g}% a year. The lag target is "
            f"{'met' if lag_ok else 'not met'}."
        )
    if tested and stats is not None:
        both = drawdown_ok and lag_ok
        if untested:
            lines.append(
                (
                    "Both targets are met in the windows tested; "
                    if both
                    else "The two targets are not both met in the windows tested; "
                )
                + f"{', '.join(untested)} not tested."
            )
        else:
            lines.append(
                "Both targets are met."
                if both
                else "The two targets are not both met at this setting."
            )
    if any("PROXY" in v for _, v in (verdicts.values() if tested else [])):
        lines.append(
            "A window marked PROXY rests mostly on stand-in series for its equity or safe leg."
        )
    lines.append(
        "Figures for any row labelled IN-SAMPLE use parameters selected on this same history; "
        "only the walk-forward section is out of sample."
    )
    if inputs.walk_forward is not None:
        w = inputs.walk_forward
        lines.append(
            f"Out of sample (walk-forward), the strategy returned {pct(w.metrics['cagr'])} a year "
            f"with a worst drawdown of {pct(w.metrics['max_drawdown'])}, against "
            f"{pct(w.benchmark_metrics['cagr'])} and {pct(w.benchmark_metrics['max_drawdown'])} "
            "for buy-and-hold on the same dates."
        )
    return "\n\n".join(lines)


def _frontier_section(inputs: ReportInputs) -> str:
    floor = inputs.cfg.ladder.equity_floor
    cap = inputs.settings.targets.crisis_max_drawdown
    rows = []
    for label, strat in inputs.strategies.items():
        for w in inputs.windows:
            s = _frontier(inputs, strat)[w.name]
            if s is None:
                note = "NOT TESTED" if w.name in _untested(inputs) else "no data"
                rows.append([label, w.name, *([note] * 7)])
                continue
            eq_p, sf_p = _window_leg_shares(inputs, w)
            rows.append(
                [
                    label,
                    w.name,
                    pct(s.achieved_max_drawdown),
                    pct(s.floor_only_max_drawdown),
                    pct(s.buy_and_hold_max_drawdown),
                    pct(s.return_lag),
                    _window_verdict(inputs, w.name, s)[1],
                    pct(eq_p, 0),
                    pct(sf_p, 0),
                ]
            )
    head = table(
        [
            "Setting",
            "Window",
            "Achieved drawdown",
            f"Floor-only drawdown ({floor:.0%} equity)",
            "Buy-and-hold drawdown",
            "Return lag over window",
            f"Drawdown target (-{cap:.0%})",
            "Equity leg proxy (by time)",
            "Safe leg proxy (by time)",
        ],
        rows,
    )
    eq_dd = _equity_drawdowns(inputs)
    arith_rows: list[list[str]] = []
    for wname, d in eq_dd.items():
        if d is None:
            note = "NOT TESTED" if wname in _untested(inputs) else "no data"
            arith_rows.append([wname, note, note, note])
            continue
        need = cap / abs(d) if d else 1.0
        arith_rows.append([wname, pct(d), pct(floor * d), pct(min(need, 1.0), 0)])
    arith = table(
        [
            "Window",
            "Equity-only drawdown",
            f"Equity x {floor:.0%} floor",
            f"Equity weight that caps at -{cap:.0%}",
        ],
        arith_rows,
    )
    unreachable = [n for n, d in eq_dd.items() if d is not None and floor * abs(d) > cap]
    reason = (
        f"The ladder never holds less than {floor:.0%} in equity. If the safe asset stays flat, a "
        f"fall of X in equity costs at least {floor:.0%} of X in the portfolio however early the "
        f"rules react. A cap of -{cap:.0%} therefore needs an equity fall of at most "
        f"{cap / floor:.0%} while the portfolio sits at the floor. "
    )
    if unreachable:
        reason += (
            "In " + ", ".join(unreachable) + " the equity fall was deeper than that, so a "
            f"-{cap:.0%} cap is out of reach with a {floor:.0%} floor: even the floor-only "
            "portfolio breaches it, whatever the signals do."
        )
    else:
        reason += (
            "In the windows tested the floor alone stays inside the cap, so the cap is reachable."
        )
    untested = _not_tested_line(inputs)
    return (
        "Drawdown inside each window, achieved against what the floor alone allows and against "
        "buy-and-hold. Return lag is buy-and-hold's return minus the strategy's over the "
        "window (positive: the strategy trailed). The proxy columns give the share of the "
        "window, by time, that rests on a stand-in series; a window met on mostly proxy data is "
        "marked.\n\n"
        + (f"**{untested}.**\n\n" if untested else "")
        + head
        + "\n\n**Why a cap may be unreachable with a floor.** "
        + reason
        + "\n\n"
        + arith
    )


def _lag_table(inputs: ReportInputs, reference: BacktestResult | None) -> str:
    t = inputs.settings.targets
    rows = []
    for label, strat in inputs.strategies.items():
        s = _lag_stats(_lags(inputs, strat, reference), t.annual_lag_pct)
        rows.append(
            [label, "n/a", "n/a", "n/a", "n/a"]
            if s is None
            else [label, f"{s[0]:.2f}%", f"{s[1]:.2f}%", f"{s[2]:.2f}%", f"{s[3]:.0%}"]
        )
    return table(
        ["Setting", "Best", "Median", "Worst", f"Windows worse than {t.annual_lag_pct:g}%/yr"],
        rows,
    )


def _lag_section(inputs: ReportInputs) -> str:
    t = inputs.settings.targets
    w0 = inputs.cfg.ladder.steps[0].equity
    mix = f"{round(w0 * 100)}/{round((1 - w0) * 100)}"
    parts = [
        f"Annual return lag over rolling {t.lag_window_years}-year windows (one window per month "
        "end; positive: the strategy trailed). **The lag target is measured against buy-and-hold "
        f"of the ladder's starting {mix} mix, never rebalanced.**",
        _lag_table(inputs, None),
    ]
    if inputs.rebalanced_reference is not None:
        parts += [
            f"The same lag against a constant {mix} portfolio rebalanced on the drift band "
            f"({inputs.cfg.rebalance.drift_band:.0%}) with the strategy's own trading costs. This "
            "is a tougher reference, because rebalancing earns or loses something that "
            "never-rebalanced buy-and-hold does not.",
            _lag_table(inputs, inputs.rebalanced_reference),
        ]
    return "\n\n".join(parts)


def _benchmark_section(inputs: ReportInputs) -> str:
    rows = [_core_row(k, v) for k, v in inputs.strategies.items()]
    rows += [_core_row(k, v) for k, v in inputs.benchmarks.items()]
    return table(CORE_HEADERS, rows)


def _realistic_section(inputs: ReportInputs) -> str:
    first = next(iter(inputs.strategies))
    full = inputs.strategies[first]
    rows = []
    for label, r in (
        ("Full ladder everywhere", full),
        ("Realistic (GIA never sells)", inputs.realistic),
    ):
        fr = _frontier(inputs, r)
        crisis = ", ".join(f"{k} {pct(v.achieved_max_drawdown)}" for k, v in fr.items() if v)
        rows.append(
            [
                label,
                pct(m.cagr(r.dates, r.index)),
                pct(m.max_drawdown(r.dates, r.index).depth),
                num(m.calmar(r.dates, r.index)),
                crisis or "no crisis data",
            ]
        )
    return (
        "In the realistic mode the ISA follows the ladder while the GIA balance only changes "
        "through returns and new monthly contributions (selling would trigger capital gains "
        "tax).\n\n" + table(["Mode", "CAGR", "Max drawdown", "Calmar", "Crisis drawdowns"], rows)
    )


def _safe_section(inputs: ReportInputs) -> str:
    if not inputs.safe_strategies:
        return "No safe asset shares enough dates with the equity history to be compared."
    rows = []
    for name, r in inputs.safe_strategies.items():
        bh = inputs.safe_buy_and_hold[name]
        lags = [
            v
            for _, v in rolling_lag_fast(
                r.dates, r.index, bh.index, inputs.settings.targets.lag_window_years
            )
        ]
        rows.append(
            [
                name,
                str(r.dates[0]),
                str(r.dates[-1]),
                pct(m.cagr(r.dates, r.index)),
                pct(m.max_drawdown(r.dates, r.index).depth),
                num(m.calmar(r.dates, r.index)),
                f"{max(lags):.2f}%" if lags else "n/a",
            ]
        )
    eq = inputs.histories[inputs.equity_key]
    eq_first, eq_last = eq.points[0].day, eq.points[-1].day
    note = ""
    if any(r.dates[0] > eq_first or r.dates[-1] < eq_last for r in inputs.safe_strategies.values()):
        starts = sorted({r.dates[0] for r in inputs.safe_strategies.values()})
        ends = sorted({r.dates[-1] for r in inputs.safe_strategies.values()})
        note = (
            f"\n\n**Note.** The aligned range ({starts[-1]} to {ends[0]}) is shorter than the "
            f"equity history ({eq_first} to {eq_last}). Every safe asset is compared on the dates "
            "all of them share, so these rows are not comparable with whole-history figures "
            "elsewhere."
        )
    return (
        "All safe assets run on the same dates: the intersection of their own history and "
        "the equity history.\n\n"
        + table(
            [
                "Safe asset",
                "Aligned start",
                "Aligned end",
                "CAGR",
                "Max drawdown",
                "Calmar",
                "Worst rolling lag (%/yr)",
            ],
            rows,
        )
        + note
    )


def _weekday_section(inputs: ReportInputs) -> str:
    w = inputs.weekday
    metric = inputs.settings.weekday_offsets.metric
    configured = inputs.settings.weekday_offsets.offsets
    used = len({off for _, off, _ in w.runs})
    rows = []
    for wd, per in w.per_weekday.items():
        rows.append(
            [
                _WEEKDAYS[wd],
                _rng(per.get("cagr")),
                _rng(per.get("max_drawdown")),
                _rng(per.get("worst_lag"), "num"),
                _rng(per.get(metric), "num"),
            ]
        )
    parts = [
        f"Every weekday at {used} weekly start offsets ({len(w.runs)} runs). Cells read "
        "min / median / max across offsets; worst lag is in percent a year.",
        table(
            ["Weekday", "CAGR", "Max drawdown", "Worst lag (%/yr)", metric.capitalize()],
            rows,
        ),
    ]
    if inputs.quick or used < configured:
        parts.append(
            f"**Wednesday verdict: not assessed.** Only {used} of the {configured} configured "
            "start offsets were run, so the spread across offsets is not known well enough to "
            "judge a weekday."
        )
    else:
        verdict = wednesday_verdict(w, metric)
        parts.append(
            "Rule: Wednesday stays unless another day beats it beyond noise, judged on its "
            "difference from Wednesday at the same start offset. Start offsets used: "
            f"{verdict.offsets_used}.\n\n**Verdict.** {verdict.text}"
        )
    cadence = []
    for key, label in (("calmar", "Calmar"), ("max_drawdown", "Max drawdown"), ("cagr", "CAGR")):
        for cad, r in ranges_by(inputs.trials, "cadence", key).items():
            cadence.append([str(cad), label, _rng(r, "num" if key == "calmar" else "pct")])
    if cadence:
        parts += [
            "Cadence across the sweep (min / median / max over all other settings):",
            table(["Cadence", "Metric", "Range"], cadence),
        ]
    return "\n\n".join(parts)


def _plateau_section(inputs: ReportInputs) -> str:
    if not inputs.plateaus:
        return "No parameter pair had enough sweep results for a table."
    t = inputs.settings.targets
    parts = [
        f"A plateau counts only where cells meet both targets: max drawdown no worse than "
        f"-{t.crisis_max_drawdown:.0%} and worst lag no more than {t.annual_lag_pct:g}% a year. "
        f"Everything chosen here is {IN_SAMPLE}: it was picked on the same history it is shown "
        "on, so it flatters the strategy. Only the walk-forward section is out of sample."
    ]
    for ps in inputs.plateaus:
        parts.append(f"### {ps.x_key} by {ps.y_key}")
        for metric, tbl in ps.tables.items():
            fmt = pct if metric in ("max_drawdown", "cagr") else num
            unit = " (percent a year)" if metric == "worst_lag" else ""
            rows = [
                [str(y)] + [fmt(v) for v in row] for y, row in zip(tbl.ys, tbl.values, strict=True)
            ]
            parts.append(
                f"{metric}{unit} (rows: {ps.y_key}, columns: {ps.x_key})\n\n"
                + table([ps.y_key + " \\ " + ps.x_key, *[str(x) for x in tbl.xs]], rows)
            )
        p = ps.proposal
        kind = (
            "Largest plateau meeting the targets"
            if p.meets_targets
            else "Best-scoring region over all cells (no region meets the targets, so this is not "
            "a recommendation)"
        )
        parts.append(
            f"{kind}: {p.size} grid points; centre {ps.x_key}={p.x}, {ps.y_key}={p.y} "
            f"(plateau score {p.score:.0%}). Best single point: {ps.x_key}={p.best_point[0]}, "
            f"{ps.y_key}={p.best_point[1]}, not chosen because it is a point, not a plateau."
        )
    chosen = ", ".join(f"{k}={v}" for k, v in inputs.chosen_params.items())
    parts.append(f"**Chosen plateau-centre parameters, {IN_SAMPLE}:** {chosen}.")
    axes = ", ".join(inputs.chosen_axes)
    if inputs.chosen_axes:
        score = inputs.chosen_joint_score
        parts.append(
            "The chosen point stitches together the centres of separate two-parameter tables, "
            f"so it is scored on its own with every chosen axis varied at once ({axes}). Joint "
            "plateau score: "
            + (
                f"{score:.0%}."
                if score is not None
                else "not available (this combination was not run in the sweep)."
            )
        )
    return "\n\n".join(parts)


def _walk_section(inputs: ReportInputs) -> str:
    w = inputs.walk_forward
    if w is None:
        return inputs.walk_forward_note or (
            "Not run: the history is too short for a training window plus one test block."
        )
    folds = table(
        [
            "Train through",
            "Test window",
            "Chosen parameters",
            "Training Calmar",
            "Test return",
            "Defaults test return",
        ],
        [
            [
                str(f.train_end),
                f"{f.test_start} to {f.test_end}",
                ", ".join(f"{k}={v}" for k, v in f.params.items()),
                (
                    "no valid training score (configured setting used)"
                    if f.no_valid_training_score
                    else num(f.train_score)
                ),
                pct(f.test_return),
                pct(f.default_test_return),
            ]
            for f in w.folds
        ],
    )
    rows = [
        [
            "Walk-forward (out of sample)",
            pct(w.metrics["cagr"]),
            pct(w.metrics["max_drawdown"]),
            num(w.metrics["calmar"]),
            num(w.metrics.get("worst_lag")),
        ],
        [
            "Buy-and-hold, same dates",
            pct(w.benchmark_metrics["cagr"]),
            pct(w.benchmark_metrics["max_drawdown"]),
            num(w.benchmark_metrics["calmar"]),
            "-",
        ],
    ]
    first, last = w.dates[0], w.dates[-1]
    inside = [c.name for c in inputs.windows if window_covered(c, first, last)]
    outside = [c.name for c in inputs.windows if c.name not in inside]
    coverage = (
        f"Crisis windows inside the out-of-sample span ({first} to {last}): "
        f"{', '.join(inside) or 'none'}. Not inside it: {', '.join(outside) or 'none'}. "
        "A verdict on a crisis the span misses rests on the in-sample figures alone."
    )
    return (
        "Anchored expanding window: parameters are chosen on data up to each training end and "
        "judged only on the next block, which the choice never saw. This walk-forward result is "
        "the only validation in this report; everything else is in-sample.\n\n"
        "**Limitation.** The engine cannot start from a given ladder state, so each fold's "
        "strategy is replayed from the start of the history with that fold's parameters. Its "
        "state at the start of the test block is what those parameters would have produced, not "
        "the state the previous fold's parameters left, and the stitched record is hypothetical "
        "at the fold boundaries. The last column runs the configured setting the same way as a "
        "comparison.\n\n"
        + coverage
        + "\n\n"
        + folds
        + "\n\n"
        + table(["", "CAGR", "Max drawdown", "Calmar", "Worst rolling lag (%/yr)"], rows)
    )


def _whipsaw_section(inputs: ReportInputs) -> str:
    step0 = inputs.cfg.ladder.steps[0].equity
    rows = []
    for label, r in inputs.strategies.items():
        n = m.whipsaw_round_trips(r.decisions)
        rows.append(
            [label, str(n), num(n / _years(r)), pct(m.time_defensive(r.advised_equity, step0))]
        )
    return (
        "A whipsaw is a de-risk followed by the start of re-entry within four weeks. Time "
        "defensive is the share of days the advised equity weight was below its starting step.\n\n"
        + table(["Setting", "Whipsaw round trips", "Per year", "Time defensive"], rows)
    )


def _etf_era(inputs: ReportInputs) -> str:
    eq = inputs.histories[inputs.equity_key]
    start: date | None = next((p.day for p in eq.points if p.provenance != "proxy"), None)
    if start is None:
        return "ETF-era-only view: the history has no non-proxy points."
    rows = []
    for label, r in inputs.strategies.items():
        pairs = [(d, v) for d, v in zip(r.dates, r.index, strict=True) if d >= start]
        if len(pairs) < 2 or (pairs[-1][0] - pairs[0][0]).days <= 0:
            continue
        ds, ix = [p[0] for p in pairs], [p[1] for p in pairs]
        rows.append(
            [
                label,
                str(ds[0]),
                pct(m.cagr(ds, ix)),
                pct(m.max_drawdown(ds, ix).depth),
                num(m.calmar(ds, ix)),
            ]
        )
    if not rows:
        return f"ETF-era-only view: too little history since {start}."
    return f"ETF-era-only view (from the first non-proxy point, {start}):\n\n" + table(
        ["Setting", "From", "CAGR", "Max drawdown", "Calmar"], rows
    )


def _substitutions(inputs: ReportInputs) -> str:
    """Every series used in place of the instrument itself, named with what it really is."""
    lines: dict[str, list[str]] = {}
    for name, h in inputs.histories.items():
        spans: dict[str, tuple[date, date, str]] = {}
        for p in h.points:
            if _is_stand_in(p.provenance):
                first, _, prov = spans.get(p.source_id, (p.day, p.day, p.provenance))
                spans[p.source_id] = (first, p.day, prov)
        for src, (first, last, prov) in spans.items():
            lines.setdefault(src, []).append(f"{name}, {prov}, {first} to {last}")
    if not lines:
        return "Substitutions: none. Every point comes from the instrument itself."
    out = []
    for src, where in lines.items():
        what = SUBSTITUTIONS.get(
            src, "a stand-in series with no description on file; check what it measures"
        )
        out.append(f"- {src} = {what} (used for {'; '.join(where)})")
    return (
        "Substitutions: series used in place of the instrument itself. Each one tracks the "
        "instrument imperfectly.\n\n" + "\n".join(out)
    )


def _data_section(inputs: ReportInputs) -> str:
    share = proxy_share(inputs.histories)
    parts = [
        f"Data version `{inputs.data_version}`; configuration version `{inputs.config_version}`.",
        f"Proxy-tagged share of the history: {share:.1%} (all series combined).",
        _substitutions(inputs),
    ]
    if inputs.carried_forward:
        parts.append(
            "Safe-asset levels are point-in-time: the last observed level is held flat until the "
            "next observation, never interpolated toward a later one. "
            + "; ".join(
                f"{k}: {v:.1%} of safe-asset days are carried forward"
                for k, v in inputs.carried_forward.items()
            )
            + "."
        )
    for name, h in inputs.histories.items():
        total = len(h.points)
        counts: Counter[tuple[str, str]] = Counter((p.source_id, p.provenance) for p in h.points)
        firsts: dict[tuple[str, str], date] = {}
        lasts: dict[tuple[str, str], date] = {}
        for p in h.points:
            k = (p.source_id, p.provenance)
            firsts.setdefault(k, p.day)
            lasts[k] = p.day
        rows = [
            [
                src,
                prov,
                str(n),
                f"{n / total:.1%}",
                str(firsts[(src, prov)]),
                str(lasts[(src, prov)]),
            ]
            for (src, prov), n in sorted(counts.items(), key=lambda kv: firsts[kv[0]])
        ]
        prox = sum(n for (_, prov), n in counts.items() if prov == "proxy") / total
        parts.append(
            f"**{name}** ({total} observations, {prox:.1%} proxy)\n\n"
            + table(["Source", "Provenance", "Observations", "Share", "First", "Last"], rows)
        )
    parts.append(_etf_era(inputs))
    return "\n\n".join(parts)


def _trials_section(inputs: ReportInputs) -> str:
    cumulative = inputs.cumulative_runs or inputs.trial_count
    return (
        f"Trials recorded: {cumulative}\n\n"
        f"- Runs this invocation: {inputs.runs_this_invocation}\n"
        f"- Distinct parameter combinations in the whole log: {inputs.distinct_combinations}\n"
        f"- Cumulative runs including earlier invocations: {cumulative}\n\n"
        "A run is one strategy simulation: the grid sweep, the configured, realistic, per-safe-"
        "asset and plateau-centre runs, the walk-forward training and test runs, and the weekday "
        "and offset runs. The more settings tried, the more the best of them is flattered by "
        "luck; later invocations append to the same log, so repeated tries still count. The "
        "plateau tables and the walk-forward result are the guard against that. The trial log "
        "lists them all." + ("\n\nThis was a quick run on a reduced grid." if inputs.quick else "")
    )


def _decisions_section(inputs: ReportInputs) -> str:
    cfg = inputs.cfg
    floor, cap = cfg.ladder.equity_floor, inputs.settings.targets.crisis_max_drawdown
    first = next(iter(inputs.strategies))
    fr = _frontier(inputs, inputs.strategies[first])
    eq_dd = _equity_drawdowns(inputs)
    untested = _not_tested_line(inputs)
    fl = (
        "; ".join(
            f"{k}: achieved {pct(v.achieved_max_drawdown)}, "
            f"floor-only {pct(v.floor_only_max_drawdown)}"
            f", equity fell {pct(eq_dd.get(k))}"
            for k, v in fr.items()
            if v
        )
        or "no crisis window lies fully inside this history"
    )
    if untested:
        fl += f". {untested}"
    full = inputs.strategies[first]
    real = inputs.realistic
    return (
        "Two questions block the next phase.\n\n"
        f"1. **Equity floor and drawdown cap.** The configured floor is {floor:.0%} equity and the "
        f"requirement is a -{cap:.0%} cap. The numbers that bear on it: {fl}. If the floor alone "
        "breaches the cap, choose a lower floor, a looser cap, or accept the gap; the tool "
        "will not decide this.\n\n"
        "2. **GIA policy.** Selling in the GIA may trigger capital gains tax, so the realistic "
        "mode "
        "lets the GIA move only through contributions. Full ladder: CAGR "
        f"{pct(m.cagr(full.dates, full.index))}, max drawdown "
        f"{pct(m.max_drawdown(full.dates, full.index).depth)}. Realistic: CAGR "
        f"{pct(m.cagr(real.dates, real.index))}, max drawdown "
        f"{pct(m.max_drawdown(real.dates, real.index).depth)}. Decide whether the protection "
        "that survives a no-sell GIA is enough, or whether GIA sales are acceptable.\n\n"
        "**Cooldown note.** The fast path (a weekly fall of "
        f"{cfg.ladder.fast_path_weekly_drop:.0%} or more) skips the confirmation requirement but "
        f"not the {cfg.ladder.cooldown_weeks}-week "
        "cooldown, so a second cut cannot follow within the cooldown even in a crash."
    )


# ---------------------------------------------------------------- document


def _banner(inputs: ReportInputs) -> str | None:
    """Shown when the equity or the safe leg has any point that is not the instrument itself,
    however small the share."""
    equity, safe = _legs(inputs)
    legs = []
    for label, h in (("equity", equity), ("safe", safe)):
        if h is None or not any(_is_stand_in(p.provenance) for p in h.points):
            continue
        first, last = h.points[0].day, h.points[-1].day
        proxy = window_proxy_share(h, first, last) or 0.0
        other = (window_share(h, first, last, is_match=_is_stand_in) or 0.0) - proxy
        legs.append(f"{label} leg: {_share(proxy)} proxy, {_share(other)} index")
    if not legs:
        return None
    return BANNER.format(legs="By time, the " + "; the ".join(legs) + ".")


def _share(x: float) -> str:
    return "<0.1%" if 0 < x < 0.0005 else f"{x:.1%}"


def render_phase0_report(inputs: ReportInputs) -> str:
    t = inputs.settings.targets
    out = ["# Phase 0 backtest report"]
    banner = _banner(inputs)
    if banner:
        out.append(banner)
    if inputs.quick:
        out.append(
            "> **QUICK RUN: reduced grid, Wednesday verdict not assessed.** Plateau tables, "
            "ranges and the trial count come from a subset of the grid; do not choose "
            "parameters from this report."
        )
    sections = [
        ("Summary of what the numbers say", _summary(inputs)),
        ("Crisis frontier: 2008, 2020, 2022", _frontier_section(inputs)),
        (f"Rolling {t.lag_window_years}-year lag", _lag_section(inputs)),
        ("Benchmark comparison", _benchmark_section(inputs)),
        ("Full ladder versus realistic (GIA no-sell)", _realistic_section(inputs)),
        ("Safe-asset comparison", _safe_section(inputs)),
        ("Weekday and cadence results", _weekday_section(inputs)),
        (
            f"Plateau tables and chosen parameters ({IN_SAMPLE})",
            _plateau_section(inputs),
        ),
        ("Walk-forward out-of-sample result", _walk_section(inputs)),
        ("Whipsaws and time defensive", _whipsaw_section(inputs)),
        ("Data", _data_section(inputs)),
        ("Trials", _trials_section(inputs)),
        ("Decisions for the owner", _decisions_section(inputs)),
    ]
    out += [f"## {title}\n\n{body}" for title, body in sections]
    return "\n\n".join(out) + "\n"


__all__ = [
    "BANNER",
    "IN_SAMPLE",
    "PlateauSection",
    "ReportInputs",
    "drawdown_verdict",
    "proxy_share",
    "render_phase0_report",
    "window_covered",
    "window_proxy_share",
]
