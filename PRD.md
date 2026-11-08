# PRD: Portfolio Guard (working title)

**Status:** Draft v1, ready for implementation planning
**Date:** 2026-11-02
**Owner:** Single user (UK-based retail investor, software developer)
**Source:** Brainstorm session; every "Decided" item below is an answer given by the owner.

> This is a personal decision-support tool, not financial advice. Rules are tested on historical data, and past performance does not guarantee future results.

---

## 1. Summary

A tool that watches a long-term ETF portfolio every week and tells the owner when and how to reduce equity risk during market downturns, and how to step back in. It also keeps the long-term allocation on target, steers new contributions, and reports progress against several benchmarks.

v1 is **advice-only**: it reads portfolio data from the Trading212 API and sends weekly advice with exact trades. The owner places the trades. Automatic execution is a later phase.

## 2. Problem and goals

**Problem.** Buy-and-hold exposes the owner to deep drawdowns. Cap-weighted global ETFs are increasingly concentrated in a few US mega-caps, and large new listings (e.g. SpaceX, with OpenAI and Anthropic reportedly preparing listings) add further concentration. The owner wants protection in crashes without constantly watching markets or trading actively.

**Primary goal (Decided):** cap drawdown first; the 6-8% a year return is an expectation, not a target.

**Success criteria (Decided):**
- Portfolio drawdown stays within about **-15%** from peak in the 2008, 2020 and 2022 backtest scenarios.
- Return lag versus the buy-and-hold benchmark is no more than **1% a year**, judged over rolling windows of 3+ years.
- After backtests pass, a **~3-month paper-trading period** (advice only, no reliance) before real use.

**Failure line (Decided):** lagging buy-and-hold by more than 1% a year over 3+ years.

## 3. Users and context

- **One user**, UK resident, GBP base currency.
- **Accounts (Decided):**
  - **Stocks & Shares ISA(s):** long horizon (10+ years), annual lump-sum contributions. Currently split across Trading212 and InvestEngine; the owner intends to **consolidate all ISA holdings on Trading212**.
  - **GIA on Trading212:** ETF-based, growing by **£1,000 a month**, 10+ year horizon.
- **BRK.B** is held on Trading212 as a fixed satellite position and is **excluded** from tactical changes (Decided).
- Holdings include global/developed ETFs (e.g. VHVG, ACWI, VWRP/VWCE), Europe (VERG), S&P 500 (VUAG) and a money-market ETF (CSH2). Values come from the API at runtime; none are hard-coded.
- FSCS: consolidating to one platform means only part of the portfolio is within the £85,000 per-firm limit. The owner has accepted this trade-off.

## 4. Scope

### In scope (v1)
1. Portfolio ingestion (Trading212 API, read-only).
2. Market data layer with swappable sources.
3. Signal engine and weekly decision logic (equity "ladder").
4. Rebalancing and contribution steering.
5. Allocation constraints and fund suggestions.
6. Weekly report, daily crisis check, phone alerts, web dashboard.
7. Backtesting and benchmark comparison framework.
8. Full audit log including the owner's overrides.
9. Guardrails.

### Out of scope for v1 (Decided)
- Automatic order execution (planned later phase; see section 11).
- Tax and CGT reporting.
- Individual stock analysis (BRK.B stays a fixed holding).
- Leverage, shorting, derivatives and crypto. Long positions in ETFs and money-market funds only.
- Multi-user support.

## 5. Functional requirements

### 5.1 Portfolio ingestion
- Read holdings, cash, account summary, order history, dividends and transactions from the **Trading212 API** (Invest/GIA and Stocks ISA). Use **read-only** keys in v1.
- Model each holding with an **account** and **platform** label so remaining InvestEngine holdings can be entered manually until the ISA transfer is complete.
- Treat ISA and GIA as separate accounts in one overall portfolio view.
- Known API constraints: documented as **beta**; orders are in the account's primary currency only (no multi-currency); no documented price history or live quotes.

### 5.2 Market data layer
- **Adapter interface:** each data source is a plugin that returns prices and indices in a common format. New sources can be added without changing the engine.
- **Defaults (Decided):** free sources (e.g. Yahoo Finance for ETF/index prices, FRED for macro). Optional adapters for other providers, including unofficial endpoints of market-data websites.
- **Rules for unofficial sources:** they may break or breach terms of service, so they are optional, never the sole input to a decision, and cross-checked against a second independent source before a signal is trusted.
- **Staleness and sanity checks:** if data is missing, stale, or implausible (e.g. a price jump beyond a configurable threshold), the run **stops and alerts** rather than guessing.
- Store raw and cleaned price history locally.

### 5.3 Signal engine (Decided: trend + drawdown + volatility)
- **Reference market (Decided):** one global equity index (a proxy for MSCI ACWI or FTSE All-World), converted to GBP.
- **Signals:**
  1. *Trend:* index price versus a long moving average (candidates: 10-month, 200-day).
  2. *Drawdown from peak:* distance below the trailing high, tied to the -15% cap.
  3. *Volatility spike:* realised volatility or VIX-style measure above a threshold.
- Parameters are set by backtesting with a **small parameter count** and out-of-sample validation to limit overfitting.

### 5.4 Weekly decision cycle
- **Schedule (Decided):** weekly, **Wednesday 12:00 Europe/London**. Signals use the latest completed closes (US close is Tuesday's). The weekday and time are a **tested parameter**: the backtest runs all weekdays; Wednesday stays unless another day clearly beats it beyond noise.
- **Weekly confirmation (Decided):** a de-risking step needs the signal to hold for **2 consecutive weekly runs**.
- **Fast-path:** a severe fall (default **-10% in a week** on the reference index) allows an immediate recommendation without waiting for confirmation, still subject to the weekly maximum change.
- **De-risking in steps (Decided).** Proposed ladder (equity / safe asset), to be finalised in backtesting:

  | Step | Equity | Safe asset |
  |---|---|---|
  | 0 | 90% | 10% |
  | 1 | 75% | 25% |
  | 2 | 60% | 40% |
  | 3 | 50% | 50% |

  Proposed mapping: step = number of negative signals (0-3), with the fast-path allowed to jump to a higher step.
- **Equity floor (Decided):** safe sleeve may rise **up to 50%**; never advise equity below 50%.
- **Re-entry (Decided):** once the main signal turns positive, return to equities in **equal weekly tranches** (default 4, a parameter). Tranches are exempt from the cooldown.
- **Trade limits (Decided):** only recommend a change when it shifts **5%+ of the portfolio**, with a **2-week cooldown** between changes.

### 5.5 Rebalancing and contributions (Decided)
- **Threshold bands**: rebalance when a holding drifts more than ~**5 percentage points** from target, checked **weekly**, with weekly advice (not quarterly).
- **New money first:** use contributions before selling.
- **ISA:** annual lump sum (typically around the April allowance reset) is allocated according to the current ladder step and drift.
- **GIA (Decided):** no tactical sells. Each **£1,000 monthly contribution** is steered to the safe or growth side according to the current ladder step, which avoids capital gains tax.
- **Safe asset (Decided):** default is a **money-market ETF** (e.g. CSH2; XSTR as an alternative). Other instruments (short gilts, gold, and so on) can be added as selectable alternatives. The tool shows a comparison and advice on the trade-offs of each so the owner chooses, and backtests can compare them.

### 5.6 Allocation constraints and fund suggestions
- **US exposure cap (Decided):** flag when **look-through US exposure** (including the US share inside global ETFs) exceeds a configured limit.
- **Fee ceiling (Decided):** flag any holding whose ongoing charge exceeds a configured limit and suggest a cheaper equivalent.
- **Fund universe (Decided):** owned funds plus a **watchlist** the owner controls. The tool may suggest alternatives from the watchlist with reasons (initial seeds from earlier analysis: CUKX, VMID, XDEW).
- Suggestions only count if the fund is available on Trading212.

### 5.7 Benchmarks (Decided)
- Three built-in comparisons: (a) current mix, buy-and-hold; (b) static 90/10, rebalanced periodically; (c) a single global tracker.
- **Extensible:** a registry lets the owner add further portfolios or funds for comparison later.

### 5.8 Reporting and alerts
- **Weekly report (Decided contents):** status light (green/amber/red) with the signals that fired and reasons; current ladder step; **exact trades** (ETF, account, amount, including where to direct the monthly GIA contribution); allocation versus target; benchmark comparison and current drawdown versus the -15% cap.
- **Channels (Decided):** **phone notification** (e.g. Telegram or push) and a **web dashboard**.
- **Daily light check (Decided):** a quick daily scan that alerts the phone only on severe moves (defaults: reference index **-5% in a day**, **-8% in a week**, or a volatility spike). It states whether the fast-path applies. It never trades.
- Push messages should avoid including balances; detail lives in the dashboard.

### 5.9 Audit log (Decided)
- Each run stores inputs, signal values, ladder step, advice and reasons.
- The owner can record whether they followed, changed or ignored advice, with a note, so the effect of overrides can be reviewed later.

### 5.10 Guardrails (Decided)
- **Pause switch** that stops all advice and any future automation.
- **Equity floor** at 50%.
- **Maximum change per week** (value to be set; proposed 15% of portfolio).
- **Sanity checks** on data (implausible jumps, mismatched holdings) and on any order versus the advice.

## 6. Backtesting and validation

- **History (Decided):** index or longer-lived proxy data, **converted to GBP**, for years before each ETF existed. Proxy periods are clearly marked in results.
- **Acceptance scenarios (Decided):** hold the -15% cap in **2008, 2020 and 2022**; run paper-trading after.
- **Also test:** weekly versus monthly cadence, all weekdays, confirmation lengths, signal thresholds, and different safe assets.
- **Costs modelled:** bid-ask spreads and any account costs; Trading212 charges no commission on ETFs in an ISA.
- **Metrics:** CAGR, max drawdown, time to recover, volatility, Sharpe/Sortino, Calmar, turnover, time spent defensive, and the number of whipsaw round trips.
- **Overfitting controls:** few parameters, walk-forward or out-of-sample tests, and results shown as ranges rather than a single best set.
- **Reproducible:** every backtest records data versions and parameters.

## 7. Technical requirements

- **Language:** open (owner is a software developer). Python is the likely default for data work, but the implementation plan decides.
- **Database:** relational; **DuckDB preferred, SQLite acceptable**. Open decision: DuckDB suits time-series and backtests; SQLite suits frequent small writes such as logs. A split of the two is allowed.
- **Hosting (Decided):** a small cloud host, always on, so daily and weekly jobs, alerts and the dashboard run when the owner's computer is off.
- **Scheduling:** weekly job (Wednesday 12:00 Europe/London) and daily light check; handle UK time-zone and daylight-saving changes, and market holidays.
- **Secrets:** API keys stored in the host's secret store, never in code or logs; Trading212 keys restricted by IP where possible; read-only in v1.
- **Privacy:** portfolio data stays on the owner's infrastructure; third-party notification messages avoid balances.
- **Reliability:** failed runs alert the owner; jobs are idempotent; clear "last successful run" on the dashboard.
- **Testing:** unit tests for signal and allocation logic, golden-file tests for backtests, and a Trading212 demo environment for integration testing.

## 8. Success metrics

- Backtest acceptance criteria met (section 2).
- Paper-trading period completes with no data or logic incidents.
- Weekly report delivered on schedule (target: 100% of weeks, or an alert explaining why not).
- Fewer than a small agreed number of whipsaw round trips per year in backtests.

## 9. Risks and open questions

**Risks**
- Weekly cadence can raise whipsaw frequency; mitigated by confirmation, cooldown and tranche re-entry, and verified in backtests.
- Fast crashes can outrun any weekly rule; the daily alert and fast-path reduce but do not remove this.
- Trading212 API is in beta and has no price data; the data layer must tolerate changes and gaps.
- Unofficial data endpoints may break or breach terms.
- Rule performance on past crises may not repeat.
- ISA transfer from InvestEngine to Trading212: process and whether it is cash or in-specie were **not confirmed**; verify with Trading212 before starting. Use the official ISA transfer route, not a withdrawal.

**Open decisions (to settle in implementation planning)**
1. Maximum change per week (proposed 15% of portfolio).
2. US exposure cap value (proposed ~50% look-through).
3. Fee ceiling value (proposed 0.15% ongoing charge).
4. Ladder-to-signal mapping and the thresholds for each signal.
5. Number of re-entry tranches (proposed 4).
6. DuckDB versus SQLite versus both.
7. Notification provider (Telegram or push) and dashboard technology.
8. Which proxy series to use for each ETF's pre-launch history.
9. Whether Wednesday 12:00 holds up in the weekday backtest.

## 10. Benchmarks and data glossary

- **Ladder step:** the current equity/safe allocation level (0-3).
- **Fast-path:** immediate recommendation on a severe fall, bypassing the 2-week confirmation.
- **Look-through exposure:** the country or company exposure inside funds, e.g. US weight within a global ETF.

## 11. Phasing

- **Phase 0 (foundation):** data layer, portfolio ingestion, backtester, benchmarks.
- **Phase 1 (advice engine):** signals, ladder, rebalancing, reports, alerts, dashboard, audit log.
- **Phase 2 (paper trading):** ~3 months of live advice that is tracked but not relied on.
- **Phase 3 (optional):** execution via the Trading212 API with "propose, then approve", guarded by the pause switch and weekly maximum change. Fully automated execution only if proven.

## 12. Handoff notes for Claude Code

- Start by producing an implementation plan covering the architecture, data model, adapter interfaces, and the backtest harness.
- Build Phase 0 first and report backtest results before building the advice engine.
- Keep all parameters in configuration with defaults from this document.
- Treat the "Decided" items as fixed requirements and the "Open decisions" as questions to confirm with the owner.
