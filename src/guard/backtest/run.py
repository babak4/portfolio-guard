"""``python -m guard.backtest.run``: build the histories, run the study, write the Phase 0 report.

Everything that touches the network or the disk is injected (``Loaders``), so the study itself
(``run_study``) is a function of histories and configuration. Nothing is invented: if the
global-equity chain has no available series the run stops and says where the index file goes.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from guard.backtest.benchmarks import BENCHMARKS
from guard.backtest.engine import CODE_VERSION, BacktestResult, Series
from guard.backtest.report import (
    PlateauSection,
    ReportInputs,
    proxy_share,
    render_phase0_report,
)
from guard.backtest.scenarios import floor_only_run
from guard.backtest.sweep import (
    BacktestSettings,
    Params,
    TrialLog,
    buy_and_hold,
    default_params,
    load_settings,
    run_sweep,
    run_trial,
)
from guard.backtest.validation import (
    plateau_table,
    propose_plateau_centre,
    walk_forward,
    weekday_offsets,
)
from guard.config.models import GuardConfig
from guard.market.base import Listing, Observation, ProviderError, ProviderUnavailable
from guard.market.chains import assemble, load_chains
from guard.market.history import History, HistoryError, build_history
from guard.paths import REPO_ROOT, data_dir, subdir

PLATEAU_METRICS = ("calmar", "max_drawdown", "worst_lag")
MSCI_FILE_NAME = "msci_acwi_nr.csv"
HISTORY_START = date(1999, 1, 1)
UK_SHORT_RATE_SERIES = "IR3TIB01GBM156N"
_BENCHMARK_KINDS = {
    "buy_and_hold_current": "current_mix",
    "static_rebalanced": "static_rebalanced",
    "single_tracker": "single_tracker",
}


class NoEquityHistory(RuntimeError):
    """No series in the global-equity chain could be loaded."""


@dataclass(frozen=True)
class Loaders:
    """Where observations come from. ``series(source_id)`` raises ``ProviderUnavailable`` for a
    source that cannot be had; ``usd_per_gbp()`` returns US dollars per pound."""

    series: Callable[[str], Sequence[Observation]]
    usd_per_gbp: Callable[[], Sequence[Observation]]


def default_loaders(start: date = HISTORY_START, end: date | None = None) -> Loaders:
    """Live loaders: Yahoo then Stooq listings, the MSCI file, FRED rates, FRED/Frankfurter FX."""
    from guard.market.fetch import DEFAULT_MACRO_ALIASES, fetch_macro, fetch_prices
    from guard.market.providers.frankfurter import FrankfurterProvider
    from guard.market.providers.fred import FredProvider
    from guard.market.providers.msci_file import SERIES_ID, MsciFileProvider
    from guard.market.providers.stooq import StooqProvider
    from guard.market.providers.yahoo import YahooProvider

    last = end or date.today()
    listings = {
        "vwrp_gbp": Listing.from_listing_id("VWRP:XLON", "GBP"),
        "vt_usd": Listing.from_listing_id("VT:XNYS", "USD"),
        "csh2_gbp": Listing.from_listing_id("CSH2:XLON", "GBP"),
        "igln_gbp": Listing.from_listing_id("IGLN:XLON", "GBP"),
        "sp500_tr_usd": Listing("SPXTR:INDEX", "USD", yahoo_symbol="^SP500TR"),
        "gold_usd": Listing("XAU:SPOT", "USD", yahoo_symbol="GC=F"),
    }

    def series(source: str) -> Sequence[Observation]:
        try:
            if source == "msci_acwi_nr_usd":
                return MsciFileProvider().series(SERIES_ID, start, last)
            if source == "sonia_rate_pct":  # the UK 3-month interbank rate stands in for SONIA
                return FredProvider().series(UK_SHORT_RATE_SERIES, start, last)
            if source in listings:
                res = fetch_prices(
                    listings[source], [YahooProvider(), StooqProvider()], start, last, max_sources=1
                )
                if res.sources:
                    return res.sources[0].observations
                raise ProviderUnavailable("; ".join(res.errors.values()) or f"{source}: no data")
        except ProviderUnavailable:
            raise
        except ProviderError as e:
            raise ProviderUnavailable(str(e)) from e
        raise ProviderUnavailable(f"{source}: no free source is configured")

    def usd_per_gbp() -> Sequence[Observation]:
        res = fetch_macro(
            "USD_PER_GBP",
            [FredProvider(), FrankfurterProvider()],
            start,
            last,
            max_sources=1,
            aliases=DEFAULT_MACRO_ALIASES,
        )
        if not res.sources:
            raise ProviderUnavailable(
                "GBP/USD history unavailable: " + "; ".join(res.errors.values())
            )
        return res.sources[0].observations

    return Loaders(series, usd_per_gbp)


# ---------------------------------------------------------------- histories


def resample_to_days(points: Mapping[date, float], days: Sequence[date]) -> dict[date, float]:
    """Levels on ``days`` by log-linear interpolation between the series' own points.

    Days before the first or after the last point are dropped, not extrapolated. A sparse
    (monthly) accrued level thus becomes a smooth daily one on the equity calendar.
    """
    keys = sorted(points)
    out: dict[date, float] = {}
    for d in days:
        if d < keys[0] or d > keys[-1]:
            continue
        i = bisect.bisect_right(keys, d) - 1
        if keys[i] == d or i + 1 >= len(keys):
            out[d] = points[keys[i]]
            continue
        a, b = keys[i], keys[i + 1]
        w = (d - a).days / (b - a).days
        out[d] = math.exp((1 - w) * math.log(points[a]) + w * math.log(points[b]))
    return out


def _chain_history(
    asset: str, loaders: Loaders, max_gap_days: int, fx: Callable[[], Sequence[Observation]]
) -> tuple[History | None, dict[str, str]]:
    chain, skipped = assemble(asset, loaders.series, load_chains())
    if not chain:
        return None, skipped
    needs_fx = any(s.currency == "USD" for s in chain)
    return build_history(chain, fx() if needs_fx else None, max_gap_days=max_gap_days), skipped


def build_histories(
    loaders: Loaders, cfg: GuardConfig, settings: BacktestSettings
) -> tuple[History, dict[str, History], dict[str, str]]:
    """The global-equity history and the named safe-asset histories (money market first).

    Raises ``NoEquityHistory`` when no equity series is available and ``HistoryError`` when
    available series cannot be spliced without guessing.
    """
    cache: list[Sequence[Observation]] = []

    def fx() -> Sequence[Observation]:
        if not cache:
            cache.append(loaders.usd_per_gbp())
        return cache[0]

    notes: dict[str, str] = {}
    equity, skipped = _chain_history("global_equity", loaders, cfg.data.max_gap_days, fx)
    notes.update({f"global_equity/{k}": v for k, v in skipped.items()})
    if equity is None:
        raise NoEquityHistory("no global-equity series is available")
    safes: dict[str, History] = {}
    for asset, gap in (
        ("money_market", settings.data.rate_series_max_gap_days),
        ("gold", cfg.data.max_gap_days),
    ):
        try:
            h, skipped = _chain_history(asset, loaders, gap, fx)
        except (HistoryError, ProviderUnavailable) as e:
            if asset == "money_market":
                raise
            notes[asset] = str(e)
            continue
        notes.update({f"{asset}/{k}": v for k, v in skipped.items()})
        if h is not None:
            safes[asset] = h
    if not safes:
        raise HistoryError("no safe-asset history is available")
    return equity, safes, notes


# ---------------------------------------------------------------- the study


def _nearest(values: Sequence[Any], want: Any) -> Any:
    return want if want in values else values[len(values) // 2]


def run_study(
    equity: History,
    safes: Mapping[str, History],
    cfg: GuardConfig,
    config_version: str,
    settings: BacktestSettings,
    *,
    quick: bool,
    weekday: int | None,
    trial_log: TrialLog,
    progress: Callable[[str], None] | None = None,
) -> ReportInputs:
    """Run the benchmarks, the strategy, the sweep and the validation; return the report inputs."""
    say = progress or (lambda _msg: None)
    eq: Series = {p.day: p.level for p in equity.points}
    days = list(eq)
    safe_series = {
        k: resample_to_days({p.day: p.level for p in h.points}, days) for k, h in safes.items()
    }
    safe_series = {k: v for k, v in safe_series.items() if len(v) > 1}
    primary = "money_market" if "money_market" in safe_series else next(iter(safe_series))
    sf = safe_series[primary]
    histories = {"global_equity": equity, **{k: safes[k] for k in safe_series}}
    data_version = hashlib.sha256(
        "|".join(f"{k}:{h.data_version}" for k, h in sorted(histories.items())).encode()
    ).hexdigest()[:16]
    lag_years = settings.targets.lag_window_years

    base = default_params(cfg, primary)
    if weekday is not None:
        base["weekday"] = weekday
    dv = f"{data_version}:{primary}"
    bh = buy_and_hold(eq, sf, cfg, data_version=dv, config_version=config_version)

    def trial(params: Params, safe_name: str) -> BacktestResult:
        s = safe_series[safe_name]
        b = buy_and_hold(eq, s, cfg, data_version=f"{data_version}:{safe_name}")
        return run_trial(
            eq,
            s,
            params,
            cfg,
            benchmark=b,
            data_version=f"{data_version}:{safe_name}",
            config_version=config_version,
            lag_years=lag_years,
        )[1]

    say("benchmarks and configured strategy")
    benchmarks: dict[str, BacktestResult] = {}
    single = BENCHMARKS["single_tracker"](eq, sf, data_version=dv, config_version=config_version)
    for b in cfg.benchmarks:
        opts: dict[str, Any] = {"data_version": dv, "config_version": config_version}
        if b.equity is not None:
            opts["equity_weight"] = b.equity
        name = _BENCHMARK_KINDS[b.kind]
        benchmarks[b.id] = (
            bh if name == "current_mix" and b.equity is None else BENCHMARKS[name](eq, sf, **opts)
        )
    strategies: dict[str, BacktestResult] = {"config defaults": trial(base, primary)}
    realistic = trial({**base, "mode": "realistic"}, primary)
    floor_only = floor_only_run(
        eq,
        sf,
        cfg.ladder,
        drift_band=cfg.rebalance.drift_band,
        data_version=dv,
        config_version=config_version,
    )
    safe_strategies = {k: trial({**base, "safe_asset": k}, k) for k in safe_series}
    safe_bh = {
        k: buy_and_hold(eq, s, cfg, data_version=f"{data_version}:{k}")
        for k, s in safe_series.items()
    }

    grid = settings.quick_grid if quick else settings.grid
    say(f"sweep over {len(safe_series)} safe asset(s)")
    trials = run_sweep(
        eq,
        safe_series,
        grid,
        cfg,
        trial_log=trial_log,
        data_version=data_version,
        config_version=config_version,
        lag_years=lag_years,
    )

    say("plateau analysis")
    pl = settings.plateau
    plateaus: list[PlateauSection] = []
    chosen: Params = dict(base)
    taken: set[str] = set()
    for x_key, y_key in pl.pairs:
        if x_key not in grid or y_key not in grid:
            continue
        fixed: dict[str, Any] = {"safe_asset": primary}
        fixed.update({k: _nearest(v, base[k]) for k, v in grid.items() if k not in (x_key, y_key)})
        prop = propose_plateau_centre(
            trials,
            x_key,
            y_key,
            tolerance=pl.tolerance,
            floors=pl.floors,
            fixed=fixed,
            objective=pl.objective,
        )
        tables = {mt: plateau_table(trials, x_key, y_key, mt, fixed) for mt in PLATEAU_METRICS}
        plateaus.append(PlateauSection(x_key, y_key, tables, prop))
        for k, v in prop.params.items():
            if k not in taken:
                chosen[k] = v
                taken.add(k)
    if any(chosen[k] != base[k] for k in taken):
        strategies["plateau centre"] = trial(chosen, primary)

    say("walk-forward")
    wf_grid = {k: v for k, v in grid.items() if k not in ("weekday", "mode", "safe_asset")}
    wf_grid["weekday"] = [base["weekday"]]
    try:
        wf = walk_forward(
            eq,
            sf,
            wf_grid,
            cfg,
            settings=settings.walk_forward,
            data_version=dv,
            config_version=config_version,
            trial_log=trial_log,
            lag_years=lag_years,
        )
    except ValueError:
        wf = None

    say("weekday and offset runs")
    offsets = (
        min(settings.weekday_offsets.offsets, 2) if quick else settings.weekday_offsets.offsets
    )
    wd = weekday_offsets(
        eq,
        sf,
        cfg,
        offsets=offsets,
        params=base,
        data_version=dv,
        config_version=config_version,
        trial_log=trial_log,
        lag_years=lag_years,
    )
    return ReportInputs(
        settings=settings,
        cfg=cfg,
        config_version=config_version,
        data_version=data_version,
        histories=histories,
        equity_key="global_equity",
        strategies=strategies,
        buy_and_hold=bh,
        single_tracker=single,
        benchmarks=benchmarks,
        floor_only=floor_only,
        realistic=realistic,
        safe_strategies=safe_strategies,
        safe_buy_and_hold=safe_bh,
        trials=trials,
        trial_count=trial_log.count,
        weekday=wd,
        plateaus=plateaus,
        chosen_params=chosen,
        walk_forward=wf,
        quick=quick,
    )


# ---------------------------------------------------------------- CLI


def _weekday_arg(text: str) -> int:
    names = ["monday", "tuesday", "wednesday", "thursday", "friday"]
    t = text.strip().lower()
    if t in names:
        return names.index(t)
    try:
        n = int(t)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a weekday: {text!r}") from None
    if not 0 <= n <= 4:
        raise argparse.ArgumentTypeError("weekday must be 0 (Monday) to 4 (Friday)")
    return n


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m guard.backtest.run", description=__doc__)
    p.add_argument("--quick", action="store_true", help="run a small subset of the grid")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="report path (default: docs/backtests/phase0-report.md in the repo)",
    )
    p.add_argument(
        "--weekday",
        type=_weekday_arg,
        default=None,
        help="decision weekday, 0-4 or a name (default: from configuration)",
    )
    return p


def main(argv: Sequence[str] | None = None, *, loaders: Loaders | None = None) -> int:
    args = _parser().parse_args(argv)
    from guard.config import load_config

    cfg, config_version = load_config()
    settings = load_settings()
    out = args.out or REPO_ROOT / "docs" / "backtests" / "phase0-report.md"
    loaders = loaders or default_loaders()
    try:
        equity, safes, notes = build_histories(loaders, cfg, settings)
    except NoEquityHistory:
        target = data_dir() / "inputs" / MSCI_FILE_NAME
        print(
            "No global-equity history is available, so there is nothing to test. Download the "
            "MSCI ACWI net total return history and place it at "
            f"{target} (or point GUARD_MSCI_FILE at it); see the README. "
            "Nothing was invented or substituted.",
            file=sys.stderr,
        )
        return 2
    except (HistoryError, ProviderUnavailable) as e:
        print(f"History could not be built: {e}", file=sys.stderr)
        return 2

    folder = subdir("backtests")
    log = TrialLog(folder / "trials.jsonl", reset=True)
    inputs = run_study(
        equity,
        safes,
        cfg,
        config_version,
        settings,
        quick=args.quick,
        weekday=args.weekday,
        trial_log=log,
        progress=lambda msg: print(msg, file=sys.stderr),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_phase0_report(inputs))
    manifest = {
        "code_version": CODE_VERSION,
        "config_version": config_version,
        "data_version": inputs.data_version,
        "history_manifests": {k: h.manifest for k, h in inputs.histories.items()},
        "skipped_sources": notes,
        "proxy_share": proxy_share(inputs.histories),
        "parameters": {
            "quick": args.quick,
            "weekday": args.weekday,
            "grid": settings.quick_grid if args.quick else settings.grid,
            "chosen": inputs.chosen_params,
        },
        "seed": None,  # nothing here is random
        "trial_count": log.count,
        "report": str(out),
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=repr)
    )
    print(f"wrote {out} ({log.count} trials)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
