# Owner decisions

| Date | Decision | Why | Where it applies |
|---|---|---|---|
| 2026-11-08 | Drawdown cap raised from -15% to -20%; equity floor stays at 50% | With a 50% equity floor, the 2008 crash alone costs about 19%, so -15% was unreachable. The owner treats 2008 as an unusual event and accepts a larger loss in a severe downturn | `PRD.md` (success criteria, signals, report, acceptance scenarios), `crisis_max_drawdown` in `src/guard/config/defaults/backtest_grid.yaml` |

Still open: the GIA policy, and the return-lag target (the Phase 0 draft shows a median 3-year lag of about 1.3% a year against a 1% limit).
