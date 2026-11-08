# Review findings deferred from the foundation PR

Raised by the code review of the first PR group and not fixed there. Each belongs to a later unit.

- Staleness checks default to "UK and US both open". A London line should check against the UK calendar only; derive the market from the listing's MIC and make the parameter required. (run orchestration)
- Observations dated on or after the run date are not excluded before cross-check or save, so a mid-session Yahoo price could be stored as a close at the Wednesday 12:00 run. (run orchestration)
- The pence flag on a listing is a manual boolean; both sources would agree on a wrong value. Compare the latest checked close in GBP with the Trading212 holding price for the same ISIN and reject ratios near 100 or 0.01. (run orchestration)
- Independence of two sources is judged by provider id only. Yahoo and Stooq for London lines share exchange data lineage.
- `save_clean_series` replaces previously stored closes on restated history without a journal entry.
- The history splice rebases on one anchor-day value and does not check tracking difference over the overlap.
- `build_history` has no jump check on its inputs; the guards are opt-in at the call site.
- Reconciliation reuses the cross-check tolerance (0.5% of the account total), so a unit error on a very small holding can pass.
- Manual holdings are not reconciled against an account total.
- `config.store.read_version` builds a path from its argument; only validated hashes reach it today.
- The snapshot job holds the writer lock across all Trading212 network calls.
