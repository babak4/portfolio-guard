"""The Phase 0 report: a markdown document that lets the owner choose parameters, floor and cap.

``render_phase0_report`` is a pure function of ``ReportInputs``. Plain-language headlines come
first, tables follow, and the decisions that need the owner come last. Numbers are never
rounded to hide a miss: a target is met only if the figure is on the right side of it.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

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
BANNER = (
    "> **Draft: proxy data.** {share:.1%} of the history behind these numbers is proxy-tagged "
    "(a stand-in series, not the instrument or index itself). Read every figure as provisional "
    "until the index file replaces the stand-ins."
)


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


def _frontier(inputs: ReportInputs, strategy: BacktestResult) -> dict[str, ScenarioSummary | None]:
    return crisis_summary(strategy, inputs.buy_and_hold, inputs.floor_only, inputs.windows)


def _equity_drawdowns(inputs: ReportInputs) -> dict[str, float | None]:
    t = inputs.single_tracker
    out = crisis_summary(t, t, t, inputs.windows)
    return {k: (v.achieved_max_drawdown if v else None) for k, v in out.items()}


def _lags(inputs: ReportInputs, strategy: BacktestResult) -> list[float]:
    years = inputs.settings.targets.lag_window_years
    bh = inputs.buy_and_hold
    return [v for _, v in rolling_lag_fast(strategy.dates, strategy.index, bh.index, years)]


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
    lines = [
        f"Configured setting: **{first}**. Targets from the requirements: drawdown within about "
        f"-{cap:.0%} in each of 2008, 2020 and 2022, and a lag of at most "
        f"{t.annual_lag_pct:g}% a year over rolling {t.lag_window_years}-year windows."
    ]
    if tested:
        met = [k for k, v in tested.items() if v.achieved_max_drawdown >= -cap]
        detail = "; ".join(
            f"{k}: {pct(v.achieved_max_drawdown)} against {pct(v.buy_and_hold_max_drawdown)} "
            f"for buy-and-hold ({'met' if k in met else 'not met'})"
            for k, v in tested.items()
        )
        verdict = (
            f"The drawdown target is met in {len(met)} of {len(tested)} testable crisis windows. "
        )
        lines.append(verdict + detail + ".")
    else:
        lines.append(
            "The drawdown target cannot be tested: none of the 2008, 2020 and 2022 windows lie "
            "inside this history."
        )
    stats = _lag_stats(_lags(inputs, strat), t.annual_lag_pct)
    drawdown_ok = bool(tested) and len(met) == len(tested)
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
            f"Rolling {t.lag_window_years}-year lag against buy-and-hold: worst {hi:.2f}% a year, "
            f"median {med:.2f}%, best {lo:.2f}%; {worse:.0%} of windows lag by more than "
            f"{t.annual_lag_pct:g}% a year. The lag target is "
            f"{'met' if lag_ok else 'not met'}."
        )
    if tested and stats is not None:
        both = (
            "Both targets are met."
            if drawdown_ok and lag_ok
            else ("The two targets are not both met at this setting.")
        )
        lines.append(both)
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
        for w, s in _frontier(inputs, strat).items():
            if s is None:
                rows.append([label, w, "no data", "no data", "no data", "no data"])
            else:
                rows.append(
                    [
                        label,
                        w,
                        pct(s.achieved_max_drawdown),
                        pct(s.floor_only_max_drawdown),
                        pct(s.buy_and_hold_max_drawdown),
                        pct(s.return_lag),
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
        ],
        rows,
    )
    eq_dd = _equity_drawdowns(inputs)
    arith_rows = []
    for w, d in eq_dd.items():
        if d is None:
            arith_rows.append([w, "no data", "no data", "no data"])
            continue
        need = cap / abs(d) if d else 1.0
        arith_rows.append([w, pct(d), pct(floor * d), pct(min(need, 1.0), 0)])
    arith = table(
        [
            "Window",
            "Equity-only drawdown",
            f"Equity x {floor:.0%} floor",
            f"Equity weight that caps at -{cap:.0%}",
        ],
        arith_rows,
    )
    unreachable = [w for w, d in eq_dd.items() if d is not None and floor * abs(d) > cap]
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
    return (
        "Drawdown inside each window, achieved against what the floor alone allows and against "
        "buy-and-hold. Return lag is buy-and-hold's return minus the strategy's over the "
        "window (positive: the strategy trailed).\n\n"
        + head
        + "\n\n**Why a cap may be unreachable with a floor.** "
        + reason
        + "\n\n"
        + arith
    )


def _lag_section(inputs: ReportInputs) -> str:
    t = inputs.settings.targets
    rows = []
    for label, strat in inputs.strategies.items():
        s = _lag_stats(_lags(inputs, strat), t.annual_lag_pct)
        rows.append(
            [label, "n/a", "n/a", "n/a", "n/a"]
            if s is None
            else [label, f"{s[0]:.2f}%", f"{s[1]:.2f}%", f"{s[2]:.2f}%", f"{s[3]:.0%}"]
        )
    return (
        f"Annual return lag against buy-and-hold over rolling {t.lag_window_years}-year windows "
        f"(one window per month end; positive: the strategy trailed).\n\n"
        + table(
            ["Setting", "Best", "Median", "Worst", f"Windows worse than {t.annual_lag_pct:g}%/yr"],
            rows,
        )
    )


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
                pct(m.cagr(r.dates, r.index)),
                pct(m.max_drawdown(r.dates, r.index).depth),
                num(m.calmar(r.dates, r.index)),
                f"{max(lags):.2f}%" if lags else "n/a",
            ]
        )
    return table(["Safe asset", "CAGR", "Max drawdown", "Calmar", "Worst rolling lag (%/yr)"], rows)


def _weekday_section(inputs: ReportInputs) -> str:
    w = inputs.weekday
    metric = inputs.settings.weekday_offsets.metric
    rows = []
    for wd, per in w.per_weekday.items():
        rows.append(
            [
                _WEEKDAYS[wd],
                _rng(per.get("cagr")),
                _rng(per.get("max_drawdown")),
                _rng(per.get(metric), "num"),
            ]
        )
    verdict = wednesday_verdict(w, metric)
    cadence = []
    for key, label in (("calmar", "Calmar"), ("max_drawdown", "Max drawdown"), ("cagr", "CAGR")):
        for cad, r in ranges_by(inputs.trials, "cadence", key).items():
            cadence.append([str(cad), label, _rng(r, "num" if key == "calmar" else "pct")])
    parts = [
        f"Every weekday at every weekly start offset ({len(w.runs)} runs). Cells read "
        "min / median / max across offsets.",
        table(["Weekday", "CAGR", "Max drawdown", metric.capitalize()], rows),
        "Rule: Wednesday stays unless another day beats it beyond noise (the spread across start "
        f"offsets).\n\n**Verdict.** {verdict.text}",
    ]
    if cadence:
        parts += [
            "Cadence across the sweep (min / median / max over all other settings):",
            table(["Cadence", "Metric", "Range"], cadence),
        ]
    return "\n\n".join(parts)


def _plateau_section(inputs: ReportInputs) -> str:
    if not inputs.plateaus:
        return "No parameter pair had enough sweep results for a table."
    parts = []
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
        parts.append(
            f"Largest plateau: {p.size} grid points; centre {ps.x_key}={p.x}, {ps.y_key}={p.y} "
            f"(plateau score {p.score:.0%}). Best single point: {ps.x_key}={p.best_point[0]}, "
            f"{ps.y_key}={p.best_point[1]}, not chosen because it is a point, not a plateau."
        )
    chosen = ", ".join(f"{k}={v}" for k, v in inputs.chosen_params.items())
    parts.append(f"**Chosen plateau-centre parameters:** {chosen}.")
    return "\n\n".join(parts)


def _walk_section(inputs: ReportInputs) -> str:
    w = inputs.walk_forward
    if w is None:
        return "Not run: the history is too short for a training window plus one test block."
    folds = table(
        ["Train through", "Test window", "Chosen parameters", "Training Calmar"],
        [
            [
                str(f.train_end),
                f"{f.test_start} to {f.test_end}",
                ", ".join(f"{k}={v}" for k, v in f.params.items()),
                num(f.train_score),
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
    return (
        "Anchored expanding window: parameters are chosen on data up to each training end and "
        "judged only on the next block, which the choice never saw. This is the headline "
        "evidence; the sample holds about four crises.\n\n"
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


def _data_section(inputs: ReportInputs) -> str:
    share = proxy_share(inputs.histories)
    parts = [
        f"Data version `{inputs.data_version}`; configuration version `{inputs.config_version}`.",
        f"Proxy-tagged share of the history: {share:.1%} (all series combined).",
    ]
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
    return (
        f"Trials recorded: {inputs.trial_count}\n\n"
        "That is every parameter setting run in this study: the grid sweep, the walk-forward "
        "training and test runs, and the weekday and offset runs. The more settings tried, the "
        "more the best of them is flattered by luck; the plateau tables and the walk-forward "
        "result are the guard against that. The trial log lists them all."
        + ("\n\nThis was a quick run on a reduced grid." if inputs.quick else "")
    )


def _decisions_section(inputs: ReportInputs) -> str:
    cfg = inputs.cfg
    floor, cap = cfg.ladder.equity_floor, inputs.settings.targets.crisis_max_drawdown
    first = next(iter(inputs.strategies))
    fr = _frontier(inputs, inputs.strategies[first])
    eq_dd = _equity_drawdowns(inputs)
    fl = (
        "; ".join(
            f"{k}: achieved {pct(v.achieved_max_drawdown)}, "
            f"floor-only {pct(v.floor_only_max_drawdown)}"
            f", equity fell {pct(eq_dd.get(k))}"
            for k, v in fr.items()
            if v
        )
        or "no crisis window lies inside this history"
    )
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


def render_phase0_report(inputs: ReportInputs) -> str:
    share = proxy_share({inputs.equity_key: inputs.histories[inputs.equity_key]}) or proxy_share(
        inputs.histories
    )
    t = inputs.settings.targets
    out = ["# Phase 0 backtest report"]
    if share > 0:
        out.append(BANNER.format(share=share))
    sections = [
        ("Summary of what the numbers say", _summary(inputs)),
        ("Crisis frontier: 2008, 2020, 2022", _frontier_section(inputs)),
        (f"Rolling {t.lag_window_years}-year lag", _lag_section(inputs)),
        ("Benchmark comparison", _benchmark_section(inputs)),
        ("Full ladder versus realistic (GIA no-sell)", _realistic_section(inputs)),
        ("Safe-asset comparison", _safe_section(inputs)),
        ("Weekday and cadence results", _weekday_section(inputs)),
        ("Plateau tables and chosen parameters", _plateau_section(inputs)),
        ("Walk-forward out-of-sample result", _walk_section(inputs)),
        ("Whipsaws and time defensive", _whipsaw_section(inputs)),
        ("Data", _data_section(inputs)),
        ("Trials", _trials_section(inputs)),
        ("Decisions for the owner", _decisions_section(inputs)),
    ]
    out += [f"## {title}\n\n{body}" for title, body in sections]
    return "\n\n".join(out) + "\n"


__all__ = ["BANNER", "PlateauSection", "ReportInputs", "proxy_share", "render_phase0_report"]
