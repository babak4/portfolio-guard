# Portfolio Guard

An advice-only drawdown-protection advisor for a long-term UK ETF portfolio.

Buy-and-hold exposes a global ETF portfolio to deep drawdowns. Guard watches the portfolio every week
and tells you when and how to cut equity risk during a downturn, and how to step back in. It also keeps
the allocation on target and steers new contributions. It never places trades: you make them.

> A personal decision-support tool, not financial advice. Rules are tested on historical data, and past
> performance does not guarantee future results.

## What it does

- **Reads your portfolio** from the Trading212 API with a read-only key (Invest/GIA and Stocks ISA), plus
  manually entered holdings for other platforms, and keeps a daily snapshot because the API has no valuation history.
- **Watches the market** through swappable data adapters (Yahoo, Stooq, Frankfurter, FRED, a user-supplied MSCI file).
  A price is trusted only when two independent sources agree. Missing, stale or implausible data stops the run
  and alerts; nothing is guessed.
- **Builds long GBP history** for a global equity index, with proxy series marked for the years before each ETF existed.
- **Decides weekly** (Wednesday 12:00 Europe/London) from trend, drawdown and volatility signals, stepping equity
  down a ladder (90/10, 75/25, 60/40, 50/50) with confirmation, cooldown and staged re-entry. Never below 50% equity.
- **Reports** exact trades by ETF, account and amount, with a daily crisis check, phone alerts and a web dashboard.

Requirements: [PRD.md](PRD.md). Implementation plan: [docs/plans/](docs/plans/).

## Status

The project is delivered in four pull-request groups:

| Group | Contents | State |
|---|---|---|
| PR1 | Scaffold, config, storage, market data layer, Trading212 ingestion, long GBP history builder | built |
| PR2 | Signals, ladder, backtest harness and the Phase 0 report | built; the report needs your MSCI file to run on real data |
| PR3 | Advice engine: rebalancing, contribution steering, constraints, guardrails, run orchestration, audit log | not started |
| PR4 | Reports, alerts, dashboard, deployment, paper trading | not started |

PR3 does not start until the Phase 0 backtest report has been reviewed: the report decides the equity floor,
the drawdown cap, the signal thresholds and the GIA policy.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14 (uv installs it from `.python-version`).

```bash
make setup      # uv sync
```

Guard keeps real data outside the repository. Point it at a directory of your choice:

```bash
export GUARD_DATA_DIR=~/guard-data      # must not be inside this repo
```

Under it Guard creates `state/` (SQLite: snapshots, runs, audit), `warehouse/` (DuckDB: market history),
`config/` (versioned configuration) and `inputs/` (user-supplied files).

### Configuration

Every parameter and its default is in `src/guard/config/defaults/guard.yaml`. Override any of them in
`$GUARD_DATA_DIR/config/overrides.yaml`, which is merged over the defaults. Unknown keys are rejected so a typo
cannot silently fall back to a default. Each load is hashed and archived so a past run can name its exact configuration.

### Secrets

Secrets are never logged or committed. Guard looks each one up as an upper-cased name, for example
`guard_t212_key_gia` is `GUARD_T212_KEY_GIA`, in this order: the real environment, then a `.env` file, then
an optional sops-encrypted file.

```bash
cp .env.example .env     # .env is git-ignored
chmod 600 .env           # Guard refuses a .env that other users can read
# then fill in the values
```

The `.env` file is read from `GUARD_ENV_FILE` if set, otherwise from the repo root, otherwise from `$GUARD_DATA_DIR`.
Names used so far:

- `GUARD_T212_KEY_<ACCOUNT>` and `GUARD_T212_SECRET_<ACCOUNT>` for each account in the config (`GIA`, `ISA`): a
  **read-only** Trading212 API key (tick only account data, portfolio and history; no orders). Restrict the key to the
  host's IP where you can.

### Optional inputs

- **MSCI ACWI net total return** history: download the end-of-day file from MSCI (personal-use licence) and save it as
  `$GUARD_DATA_DIR/inputs/msci_acwi_nr.csv` (or point `GUARD_MSCI_FILE` at it). It is never committed.
- **Manual holdings** (for example InvestEngine): `$GUARD_DATA_DIR/manual_holdings.yaml`.
- **Yahoo Finance** prices need the optional extra: `uv sync --extra market`.

## Running

The worker schedules three jobs in `Europe/London` time (all times come from `guard.yaml`):

- the weekly advice run, Wednesday 12:00;
- the daily light check, 22:30, which alerts on a fall of 5% in a day, 8% in a week or a volatility spike, says whether
  the fast path applies, and never trades or changes the ladder;
- the daily portfolio snapshot, 22:30.

```bash
make worker      # python -m guard.worker: run the scheduler
make weekly      # run this week's advice now (ARGS="--slot <a date this week> --isa-lump-sum 2000")
make daily       # run the daily check now
make pause       # stop all advice until resumed
make resume      # allow advice again
make status      # pause switch and the last successful run of each job, with its age
make snapshot    # uv run python -m guard.ingest.snapshot_job (the snapshot job on its own)
uv run python -m guard.worker respond RUN_ID followed|changed|ignored --note "..."
```

A weekly run reads Trading212 and any manual holdings, builds the live signal series (VWRP closes, cross-checked by return
against VT converted to pounds with two agreeing USD/GBP sources), moves the ladder one week, and stores one audit record:
the inputs, signals, ladder state before and after, and the advice. It stores nothing unless every step succeeded. Bad or
stale data records a `stopped` run with the reason and leaves the ladder alone; fix the cause and run it again for the same
slot. The weekly slot is the Monday of the London ISO week, so a week has one run: a second run on another day of the same
week returns the first unchanged and does not move the ladder, and `--slot` is accepted only for a date in the current
week (exit 2 otherwise). The live series allows no lag (the latest close of both ETFs must be the latest completed
session) and needs enough closes for the configured windows; the USD/GBP rate is the ECB rate (Frankfurter) checked
against FRED on the dates both have. The daily check needs that day's close, and when the second ETF does not confirm a
fall it still alerts, saying so. While paused, a run is recorded as `paused` with no advice and stored advice is not
shown. `respond` accepts only an `ok` weekly run, ties the response to a hash of the advice stored for it (shown in the
output) and writes to an append-only journal; a later response supersedes the earlier one without editing it. Advice
and `ok` runs cannot be edited or deleted. Stopped, failed and locked runs and daily events go to `Deps.notify`
(a warning log until the alert layer is added); messages that can leave the process have all numbers replaced by `#`.
`GUARD_T212_BASE_URL` selects the Trading212 host (default `https://live.trading212.com/api/v0`; use
`https://demo.trading212.com/api/v0` for the demo account). `run-now` exits 2 when a run stopped.

The Trading212 API is in beta and its response field names are assumptions until checked against the demo host;
see [docs/queries/t212-payload-shapes.md](docs/queries/t212-payload-shapes.md).

### Backtest and the Phase 0 report

```bash
make backtest-quick    # small grid, a few minutes
make backtest          # full grid
```

It builds the GBP global-equity history, replays the weekly ladder, runs the benchmarks, a parameter sweep,
walk-forward validation and a weekday check, and writes `docs/backtests/phase0-report.md`, a trial log and a
manifest under `$GUARD_DATA_DIR/backtests/`. The report's headline says whether the PRD targets (drawdown about -15%
in 2008, 2020 and 2022; lag at most 1% a year over rolling 3-year windows) were met, and what decisions it needs from you.

It needs the MSCI ACWI net total return file (see Optional inputs). Without it the command stops and says so; it never
invents data. Anything built from stand-in series is marked "Draft: proxy data".

## Testing

```bash
uv run pytest -m "not live and not perf" --ignore=tests/integration   # unit, golden and property tests
uv run ruff check src tests                                            # lint
uv run mypy src/guard                                                  # types
```

A change is done when all three are clean. `make check` runs all three; `make help` lists every local
command (setup, hooks, tests, snapshot, backtest).

Install the git hooks once with `make hooks`. Commits run ruff and mypy; pushes also run the unit tests.
The same checks run on every pull request in GitHub Actions (`.github/workflows/ci.yml`).

- Tests never touch the network (`pytest-socket` blocks it).
- Integration tests are marked `live` and skipped by default. Run **one directory per invocation**:
  `uv run pytest tests/integration/market -m live --force-enable-socket`. The Trading212 demo test also needs
  a demo read-only key set as `GUARD_T212_KEY_DEMO` and `GUARD_T212_SECRET_DEMO` in `.env`, and its
  skip marker removed.
- Golden tests have expected values worked out independently of the code; do not edit them to make code pass.

## Layout

```text
src/guard/
  config/      layered, versioned configuration (defaults in config/defaults/)
  storage/     SQLite and DuckDB access, migrations, single-writer lock
  market/      data adapters, cross-checks, sanity stops, long GBP history builder
  ingest/      Trading212 client, manual holdings, daily snapshot job
  signals/     trend, drawdown and volatility signals (pure)
  ladder/      the weekly equity/safe-asset state machine (pure)
  backtest/    simulator, metrics, benchmarks, sweeps, validation, Phase 0 report
tests/         unit, golden, property, integration, fixtures
docs/          plans, provenance of code copied from portfolio-compass, open queries
```

Conventions for contributors and agents are in [CLAUDE.md](CLAUDE.md). Code copied from the sibling project
`portfolio-compass` is listed in [docs/provenance.md](docs/provenance.md).
