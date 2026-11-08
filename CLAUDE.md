# CLAUDE.md

Portfolio Guard: an advice-only drawdown-protection advisor for a UK retail ETF portfolio.
Requirements: `PRD.md`. Implementation plan: `docs/plans/` (latest file). This is a decision-support tool,
not financial advice, and v1 never places trades.

## Commands

```bash
uv sync
uv run pytest -m "not live and not perf" --ignore=tests/integration
uv run pytest tests/integration/<dir>     # one integration directory per invocation
uv run ruff check src tests
uv run mypy src/guard
```

A change is done when pytest, ruff and mypy are all clean.

## Rules

- No live network in tests (pytest-socket is on). Real-service checks use the `live` marker and are manual.
- Golden tests are authored independently and never edited to make code pass.
- Secrets only through `guard.secrets.get_secret` (environment, then a git-ignored `.env` that must be `chmod 600`, then sops); never logged, printed or committed. Tests use `GUARD_FAKE_SECRETS`. Only `.env.example` (empty values) is tracked.
- The data directory (`GUARD_DATA_DIR`) must be outside the repo. Real data and the MSCI file are never committed.
- Only `guard.storage` imports `duckdb` or `sqlite3`. One writer at a time (`guard.storage.lock`).
- Signals, ladder, rebalancing and guardrails are pure functions; I/O lives at the edges.
- Money uses `Decimal`; weights are fractions in [0, 1]; vectorised signal code may use float64.
- Every parameter lives in config (`src/guard/config/defaults/guard.yaml`) with the PRD default.
- Push messages never contain balances or account names.
- Files copied from `portfolio-compass` are listed in `docs/provenance.md`. Do not import `compass`.
- Keep plan IDs (U1, R7, KTD3) out of code and comments; write what the rule says.
- Contract questions go in `docs/queries/<n>.md`; stop work on that part rather than guess.
