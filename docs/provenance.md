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
