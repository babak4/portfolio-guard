# Phase 0 backtest report

> **Draft: proxy data.** By time, the equity leg: 100.0% proxy, 0.0% index; the safe leg: 100.0% proxy, 0.0% index. A proxy is a stand-in series, not the instrument itself. Read every figure as provisional until the index file replaces the stand-ins.

## Summary of what the numbers say

Configured setting: **config defaults**. Targets from the requirements: drawdown within about -20% in each of 2008-2009, 2020, 2022, and a lag of at most 1.5% a year over rolling 3-year windows.

The drawdown target is met in 1 of 3 windows tested. 2008-2009: -26.3% against -33.7% for buy-and-hold (not met); 2020: -20.3% against -24.1% for buy-and-hold (not met); 2022: -13.3% against -14.7% for buy-and-hold (met on PROXY data only).

Rolling 3-year lag against buy-and-hold (the starting mix, never rebalanced): worst 4.37% a year, median 1.28%, best -3.02%; 36% of windows lag by more than 1.5% a year. The lag target is not met.

The two targets are not both met at this setting.

A window marked PROXY rests mostly on stand-in series for its equity or safe leg.

Figures for any row labelled IN-SAMPLE use parameters selected on this same history; only the walk-forward section is out of sample.

Out of sample (walk-forward), the strategy returned 9.2% a year with a worst drawdown of -20.3%, against 10.8% and -24.1% for buy-and-hold on the same dates.

## Crisis frontier: 2008, 2020, 2022

Drawdown inside each window, achieved against what the floor alone allows and against buy-and-hold. Return lag is buy-and-hold's return minus the strategy's over the window (positive: the strategy trailed). The proxy columns give the share of the window, by time, that rests on a stand-in series; a window met on mostly proxy data is marked.

| Setting | Window | Achieved drawdown | Floor-only drawdown (50% equity) | Buy-and-hold drawdown | Return lag over window | Drawdown target (-20%) | Equity leg proxy (by time) | Safe leg proxy (by time) |
|---|---|---|---|---|---|---|---|---|
| config defaults | 2008-2009 | -26.3% | -19.4% | -33.7% | -3.5% | not met | 100% | 100% |
| config defaults | 2020 | -20.3% | -12.9% | -24.1% | 5.1% | not met | 100% | 100% |
| config defaults | 2022 | -13.3% | -7.9% | -14.7% | 2.1% | met on PROXY data only | 100% | 100% |

**Why a cap may be unreachable with a floor.** The ladder never holds less than 50% in equity. If the safe asset stays flat, a fall of X in equity costs at least 50% of X in the portfolio however early the rules react. A cap of -20% therefore needs an equity fall of at most 40% while the portfolio sits at the floor. In the windows tested the floor alone stays inside the cap, so the cap is reachable.

| Window | Equity-only drawdown | Equity x 50% floor | Equity weight that caps at -20% |
|---|---|---|---|
| 2008-2009 | -39.0% | -19.5% | 51% |
| 2020 | -25.4% | -12.7% | 79% |
| 2022 | -15.3% | -7.7% | 100% |

## Rolling 3-year lag

Annual return lag over rolling 3-year windows (one window per month end; positive: the strategy trailed). **The lag target is measured against buy-and-hold of the ladder's starting 90/10 mix, never rebalanced.**

| Setting | Best | Median | Worst | Windows worse than 1.5%/yr |
|---|---|---|---|---|
| config defaults | -3.02% | 1.28% | 4.37% | 36% |

The same lag against a constant 90/10 portfolio rebalanced on the drift band (5%) with the strategy's own trading costs. This is a tougher reference, because rebalancing earns or loses something that never-rebalanced buy-and-hold does not.

| Setting | Best | Median | Worst | Windows worse than 1.5%/yr |
|---|---|---|---|---|
| config defaults | -2.80% | 1.23% | 5.24% | 35% |

## Benchmark comparison

|  | CAGR | Max drawdown | Calmar | Volatility | Sharpe | Sortino | Time to recover | Turnover (x/yr) |
|---|---|---|---|---|---|---|---|---|
| config defaults | 6.4% | -32.2% | 0.20 | 11.5% | 0.58 | 0.81 | 1085 days | 0.45 |
| current_mix | 7.0% | -41.0% | 0.17 | 13.7% | 0.55 | 0.77 | 1072 days | 0.00 |
| static_90_10 | 6.9% | -42.0% | 0.16 | 13.7% | 0.54 | 0.77 | 1062 days | 0.02 |
| global_tracker | 7.3% | -46.4% | 0.16 | 15.3% | 0.52 | 0.74 | 1114 days | 0.00 |
| constant 90/10, rebalanced on the drift band | 7.1% | -41.5% | 0.17 | 13.9% | 0.54 | 0.77 | 1030 days | 0.01 |

## Full ladder versus realistic (GIA no-sell)

In the realistic mode the ISA follows the ladder while the GIA balance only changes through returns and new monthly contributions (selling would trigger capital gains tax).

| Mode | CAGR | Max drawdown | Calmar | Crisis drawdowns |
|---|---|---|---|---|
| Full ladder everywhere | 6.4% | -32.2% | 0.20 | 2008-2009 -26.3%, 2020 -20.3%, 2022 -13.3% |
| Realistic (GIA never sells) | 6.7% | -36.2% | 0.19 | 2008-2009 -29.9%, 2020 -22.7%, 2022 -14.1% |

## Safe-asset comparison

All safe assets run on the same dates: the intersection of their own history and the equity history.

| Safe asset | Aligned start | Aligned end | CAGR | Max drawdown | Calmar | Worst rolling lag (%/yr) |
|---|---|---|---|---|---|---|
| money_market | 2000-12-29 | 2026-01-01 | 6.4% | -32.2% | 0.20 | 4.37% |

**Note.** The aligned range (2000-12-29 to 2026-01-01) is shorter than the equity history (2000-12-29 to 2026-11-06). Every safe asset is compared on the dates all of them share, so these rows are not comparable with whole-history figures elsewhere.

## Weekday and cadence results

Every weekday at 4 weekly start offsets (20 runs). Cells read min / median / max across offsets; worst lag is in percent a year.

| Weekday | CAGR | Max drawdown | Worst lag (%/yr) | Calmar |
|---|---|---|---|---|
| Monday | 5.9% / 6.0% / 6.0% | -33.0% / -32.9% / -32.7% | 4.90 / 4.96 / 4.99 | 0.18 / 0.18 / 0.18 |
| Tuesday | 5.9% / 6.0% / 6.1% | -32.7% / -32.5% / -32.2% | 4.95 / 4.96 / 4.97 | 0.18 / 0.19 / 0.19 |
| Wednesday | 6.2% / 6.3% / 6.4% | -32.7% / -32.4% / -32.2% | 4.35 / 4.37 / 4.38 | 0.19 / 0.20 / 0.20 |
| Thursday | 6.3% / 6.4% / 6.4% | -32.4% / -32.2% / -32.1% | 4.41 / 4.43 / 4.45 | 0.19 / 0.20 / 0.20 |
| Friday | 6.2% / 6.3% / 6.3% | -33.0% / -32.9% / -32.6% | 4.47 / 4.53 / 4.56 | 0.19 / 0.19 / 0.19 |

Rule: Wednesday stays unless another day beats it beyond noise, judged on its difference from Wednesday at the same start offset. Start offsets used: 4.

**Verdict.** Wednesday is clearly beaten on calmar by Thursday: the median paired difference exceeds its spread; paired differences against Wednesday at the same offset over 4 start offsets (Monday minus Wednesday: median -0.012, spread 0.003; Tuesday minus Wednesday: median -0.010, spread 0.001; Thursday minus Wednesday: median +0.004, spread 0.002; Friday minus Wednesday: median -0.003, spread 0.003).

Cadence across the sweep (min / median / max over all other settings):

| Cadence | Metric | Range |
|---|---|---|
| monthly | Calmar | 0.16 / 0.19 / 0.22 |
| weekly | Calmar | 0.18 / 0.19 / 0.21 |
| monthly | Max drawdown | -39.5% / -35.8% / -30.8% |
| weekly | Max drawdown | -37.4% / -34.9% / -30.7% |
| monthly | CAGR | 5.9% / 6.7% / 7.1% |
| weekly | CAGR | 5.8% / 6.6% / 6.9% |

## Plateau tables and chosen parameters (IN-SAMPLE (selected on this history))

A plateau counts only where cells meet both targets: max drawdown no worse than -20% and worst lag no more than 1.5% a year. Everything chosen here is IN-SAMPLE (selected on this history): it was picked on the same history it is shown on, so it flatters the strategy. Only the walk-forward section is out of sample.

### trend_window by drawdown_threshold

calmar (rows: drawdown_threshold, columns: trend_window)

| drawdown_threshold \ trend_window | 150 | 200 | 250 |
|---|---|---|---|
| 0.08 | 0.19 | 0.20 | 0.20 |
| 0.1 | 0.19 | 0.20 | 0.20 |
| 0.15 | 0.20 | 0.20 | 0.20 |

max_drawdown (rows: drawdown_threshold, columns: trend_window)

| drawdown_threshold \ trend_window | 150 | 200 | 250 |
|---|---|---|---|
| 0.08 | -32.0% | -32.2% | -31.6% |
| 0.1 | -32.0% | -32.2% | -31.6% |
| 0.15 | -32.7% | -32.8% | -32.2% |

worst_lag (percent a year) (rows: drawdown_threshold, columns: trend_window)

| drawdown_threshold \ trend_window | 150 | 200 | 250 |
|---|---|---|---|
| 0.08 | 4.58 | 4.71 | 4.94 |
| 0.1 | 4.20 | 4.37 | 4.99 |
| 0.15 | 3.64 | 3.88 | 4.40 |

Best-scoring region over all cells (no region meets the targets, so this is not a recommendation): 8 grid points; centre trend_window=200, drawdown_threshold=0.1 (plateau score 88%). Best single point: trend_window=200, drawdown_threshold=0.15, not chosen because it is a point, not a plateau.

### drawdown_threshold by volatility_threshold

calmar (rows: volatility_threshold, columns: drawdown_threshold)

| volatility_threshold \ drawdown_threshold | 0.08 | 0.1 | 0.15 |
|---|---|---|---|
| 0.2 | 0.19 | 0.20 | 0.20 |
| 0.25 | 0.20 | 0.20 | 0.20 |
| 0.3 | 0.19 | 0.20 | 0.20 |

max_drawdown (rows: volatility_threshold, columns: drawdown_threshold)

| volatility_threshold \ drawdown_threshold | 0.08 | 0.1 | 0.15 |
|---|---|---|---|
| 0.2 | -32.1% | -32.1% | -32.7% |
| 0.25 | -32.2% | -32.2% | -32.8% |
| 0.3 | -32.4% | -32.4% | -33.0% |

worst_lag (percent a year) (rows: volatility_threshold, columns: drawdown_threshold)

| volatility_threshold \ drawdown_threshold | 0.08 | 0.1 | 0.15 |
|---|---|---|---|
| 0.2 | 4.86 | 4.62 | 4.36 |
| 0.25 | 4.71 | 4.37 | 3.88 |
| 0.3 | 4.71 | 4.37 | 3.88 |

Best-scoring region over all cells (no region meets the targets, so this is not a recommendation): 9 grid points; centre drawdown_threshold=0.1, volatility_threshold=0.25 (plateau score 100%). Best single point: drawdown_threshold=0.15, volatility_threshold=0.3, not chosen because it is a point, not a plateau.

### confirmation_runs by trend_window

calmar (rows: trend_window, columns: confirmation_runs)

| trend_window \ confirmation_runs | 1 | 2 | 3 |
|---|---|---|---|
| 150 | 0.18 | 0.19 | 0.19 |
| 200 | 0.19 | 0.20 | 0.20 |
| 250 | 0.20 | 0.20 | 0.20 |

max_drawdown (rows: trend_window, columns: confirmation_runs)

| trend_window \ confirmation_runs | 1 | 2 | 3 |
|---|---|---|---|
| 150 | -32.8% | -32.0% | -32.4% |
| 200 | -32.3% | -32.2% | -32.3% |
| 250 | -32.0% | -31.6% | -32.1% |

worst_lag (percent a year) (rows: trend_window, columns: confirmation_runs)

| trend_window \ confirmation_runs | 1 | 2 | 3 |
|---|---|---|---|
| 150 | 4.23 | 4.20 | 3.95 |
| 200 | 4.35 | 4.37 | 4.17 |
| 250 | 4.47 | 4.99 | 4.30 |

Best-scoring region over all cells (no region meets the targets, so this is not a recommendation): 9 grid points; centre confirmation_runs=2, trend_window=200 (plateau score 100%). Best single point: confirmation_runs=1, trend_window=250, not chosen because it is a point, not a plateau.

**Chosen plateau-centre parameters, IN-SAMPLE (selected on this history):** trend_window=200, drawdown_threshold=0.1, volatility_threshold=0.25, confirmation_runs=2, cadence=weekly, weekday=2, mode=full, safe_asset=money_market.

The chosen point stitches together the centres of separate two-parameter tables, so it is scored on its own with every chosen axis varied at once (trend_window, drawdown_threshold, volatility_threshold, confirmation_runs). Joint plateau score: 90%.

## Walk-forward out-of-sample result

Anchored expanding window: parameters are chosen on data up to each training end and judged only on the next block, which the choice never saw. This walk-forward result is the only validation in this report; everything else is in-sample.

**Limitation.** The engine cannot start from a given ladder state, so each fold's strategy is replayed from the start of the history with that fold's parameters. Its state at the start of the test block is what those parameters would have produced, not the state the previous fold's parameters left, and the stitched record is hypothetical at the fold boundaries. The last column runs the configured setting the same way as a comparison.

Crisis windows inside the out-of-sample span (2008-12-29 to 2026-01-01): 2020, 2022. Not inside it: 2008-2009. A verdict on a crisis the span misses rests on the in-sample figures alone.

| Train through | Test window | Chosen parameters | Training Calmar | Test return | Defaults test return |
|---|---|---|---|---|---|
| 2008-12-29 | 2008-12-30 to 2011-12-29 | trend_window=200, drawdown_threshold=0.1, volatility_threshold=0.25, confirmation_runs=2, cadence=weekly, weekday=2, mode=full | 0.02 | 23.8% | 23.8% |
| 2011-12-29 | 2011-12-30 to 2014-12-29 | trend_window=200, drawdown_threshold=0.1, volatility_threshold=0.25, confirmation_runs=2, cadence=weekly, weekday=2, mode=full | 0.08 | 39.3% | 39.3% |
| 2014-12-29 | 2014-12-30 to 2017-12-29 | trend_window=200, drawdown_threshold=0.1, volatility_threshold=0.25, confirmation_runs=2, cadence=weekly, weekday=2, mode=full | 0.14 | 38.5% | 38.5% |
| 2017-12-29 | 2018-01-01 to 2020-12-29 | trend_window=200, drawdown_threshold=0.1, volatility_threshold=0.25, confirmation_runs=2, cadence=weekly, weekday=2, mode=full | 0.17 | 19.6% | 19.6% |
| 2020-12-29 | 2020-12-30 to 2023-12-29 | trend_window=200, drawdown_threshold=0.1, volatility_threshold=0.25, confirmation_runs=2, cadence=weekly, weekday=2, mode=full | 0.18 | 19.2% | 19.2% |
| 2023-12-29 | 2024-01-01 to 2026-01-01 | trend_window=200, drawdown_threshold=0.1, volatility_threshold=0.25, confirmation_runs=2, cadence=monthly, weekday=2, mode=full | 0.19 | 31.4% | 30.4% |

|  | CAGR | Max drawdown | Calmar | Worst rolling lag (%/yr) |
|---|---|---|---|---|
| Walk-forward (out of sample) | 9.2% | -20.3% | 0.45 | 4.37 |
| Buy-and-hold, same dates | 10.8% | -24.1% | 0.45 | - |

## Whipsaws and time defensive

A whipsaw is a de-risk followed by the start of re-entry within four weeks. Time defensive is the share of days the advised equity weight was below its starting step.

| Setting | Whipsaw round trips | Per year | Time defensive |
|---|---|---|---|
| config defaults | 18 | 0.72 | 30.0% |

## Data

Data version `70e51342fa2a502f`; configuration version `df452806cefd4712`.

Proxy-tagged share of the history: 100.0% (all series combined).

Substitutions: series used in place of the instrument itself. Each one tracks the instrument imperfectly.

- msci_acwi_nr_usd = MSCI ACWI net total return index (the owner's file), in US dollars converted to pounds, less an assumed annual fee; an index, not the fund (used for global_equity, proxy, 2000-12-29 to 2026-11-06)
- sonia_rate_pct = FRED UK 3-month interbank rate IR3TIB01GBM156N, which carries a credit spread over SONIA, notably in 2008 (used for money_market, proxy, 1999-01-01 to 2026-01-01)

Safe-asset levels are point-in-time: the last observed level is held flat until the next observation, never interpolated toward a later one. money_market: 96.7% of safe-asset days are carried forward.

**global_equity** (6746 observations, 100.0% proxy)

| Source | Provenance | Observations | Share | First | Last |
|---|---|---|---|---|---|
| msci_acwi_nr_usd | proxy | 6746 | 100.0% | 2000-12-29 | 2026-11-06 |

**money_market** (325 observations, 100.0% proxy)

| Source | Provenance | Observations | Share | First | Last |
|---|---|---|---|---|---|
| sonia_rate_pct | proxy | 325 | 100.0% | 1999-01-01 | 2026-01-01 |

ETF-era-only view: the history has no non-proxy points.

## Trials

Trials recorded: 8095

- Runs this invocation: 2627
- Distinct parameter combinations in the whole log: 1782
- Cumulative runs including earlier invocations: 8095

A run is one strategy simulation: the grid sweep, the configured, realistic, per-safe-asset and plateau-centre runs, the walk-forward training and test runs, and the weekday and offset runs. The more settings tried, the more the best of them is flattered by luck; later invocations append to the same log, so repeated tries still count. The plateau tables and the walk-forward result are the guard against that. The trial log lists them all.

## Decisions for the owner

Two questions block the next phase.

1. **Equity floor and drawdown cap.** The configured floor is 50% equity and the requirement is a -20% cap. The numbers that bear on it: 2008-2009: achieved -26.3%, floor-only -19.4%, equity fell -39.0%; 2020: achieved -20.3%, floor-only -12.9%, equity fell -25.4%; 2022: achieved -13.3%, floor-only -7.9%, equity fell -15.3%. If the floor alone breaches the cap, choose a lower floor, a looser cap, or accept the gap; the tool will not decide this.

2. **GIA policy.** Selling in the GIA may trigger capital gains tax, so the realistic mode lets the GIA move only through contributions. Full ladder: CAGR 6.4%, max drawdown -32.2%. Realistic: CAGR 6.7%, max drawdown -36.2%. Decide whether the protection that survives a no-sell GIA is enough, or whether GIA sales are acceptable.

**Cooldown note.** The fast path (a weekly fall of 10% or more) skips the confirmation requirement but not the 2-week cooldown, so a second cut cannot follow within the cooldown even in a crash.
