# Provenance of copied code

Guard copies leaf modules from the sibling project `portfolio-compass` (commit `7a2f6de`) instead of
depending on it. Each entry lists the Compass path, the Guard path and what changed. Copied
external-facing code (API payload shapes, symbol maps, runbooks) is unverified until checked against the
real service; see `docs/queries/`.

| Compass path | Guard path | Changes |
|---|---|---|
| `src/compass/secrets.py` | `src/guard/secrets.py` | `COMPASS_*` env names became `GUARD_*`; the data directory comes from `guard.paths` |
| `src/compass/paths.py` | `src/guard/paths.py` | `COMPASS_DATA_DIR` became `GUARD_DATA_DIR` |
| `src/compass/worker/lock.py` | `src/guard/storage/lock.py` | prefix change; adds an optional wait so a short dashboard write retries instead of failing |
| `src/compass/storage/ops.py` (idea) | `src/guard/storage/ops.py` | rewritten: numbered SQL migrations, append-only triggers |
| `src/compass/market/base.py` | `src/guard/market/base.py` | pyarrow tables replaced by an `Observation` dataclass; one listing or series per call; adds the `DataIntegrityError` hierarchy and a raw-payload sink type |
| `src/compass/market/fetch.py` | `src/guard/market/fetch.py` | first-provider-wins replaced by collecting up to N sources with per-provider error records; entry-point loading dropped |
| `src/compass/market/providers/yfinance_provider.py` | `src/guard/market/providers/yahoo.py` | landing store replaced by an injectable sink; lazy `yfinance` import |
| `src/compass/market/providers/stooq.py` | `src/guard/market/providers/stooq.py` | landing store replaced by an injectable sink |
| `src/compass/market/providers/frankfurter.py` | `src/guard/market/providers/frankfurter.py` | landing store replaced by an injectable sink |
| `src/compass/market/providers/_http.py` | `src/guard/market/providers/_http.py` | logs only source and status, never URL or parameters |
| `src/compass/ingest/adapters/t212_api.py` | `src/guard/ingest/t212.py` | only the read-only path allowlist, 429 backoff and `nextPagePath` pagination logic, re-implemented as a standalone client; HTTP Basic auth, injected sleep/clock, no landing store, pyarrow or dbt |
| `src/compass/market/history.py` (splice rule) | `src/guard/market/history.py` | re-derived, not copied: daily/weekly instead of monthly, rebases at the stitched series start, per-point provenance, USD conversion, rate accrual, fee drag, data-version manifest |
