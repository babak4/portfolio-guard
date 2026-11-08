# Owner decisions

| Date | Decision | Why | Where it applies |
|---|---|---|---|
| 2026-11-08 | Drawdown cap raised from -15% to -20%; equity floor stays at 50% | With a 50% equity floor, the 2008 crash alone costs about 19%, so -15% was unreachable. The owner treats 2008 as an unusual event and accepts a larger loss in a severe downturn | `PRD.md` (success criteria, signals, report, acceptance scenarios), `crisis_max_drawdown` in `src/guard/config/defaults/backtest_grid.yaml` |
| 2026-11-08 | Return-lag limit raised from 1% to 1.5% a year (over rolling 3-year windows) | Lag is the price of the crash protection; the draft shows a typical lag of about 1.3% a year | `PRD.md` (success criteria, failure line), `annual_lag_pct` in `src/guard/config/defaults/backtest_grid.yaml` |
| 2026-11-08 | The lag limit is judged on the typical 3-year window (the median); the worst window and the share of windows over the limit are always printed beside it | A strict every-window test fails any protective strategy in strong rallies, which is the cost of the protection | the lag verdict and plateau test in `src/guard/backtest/report.py` and `validation.py` |
| 2026-11-08 | GIA never sells for now: only new monthly contributions are steered toward safe or growth. Selling in the GIA only when the sale is estimated not to create a taxable gain is a planned later improvement | Avoids capital gains tax and keeps v1 simple; costs about 3-4 points of extra drawdown on the GIA's share in the draft backtest | Advice engine (rebalancing and contribution steering); `docs/plans/` follow-up work |
| 2026-11-09 | US exposure limit: at most 50% of the whole portfolio may sit in US-only funds. It is a threshold (a warning), not a target, and US exposure inside global funds is not counted | Simpler and easier to explain than looking through global funds; narrows the PRD's look-through wording | `PRD.md` (allocation constraints), `constraints.us_exposure_cap` in `src/guard/config/defaults/guard.yaml` |
| 2026-11-09 | Fee ceiling 0.15% ongoing charge and a 15% maximum weekly change, as the PRD proposed | Owner accepted the PRD's proposed limits | `constraints.fee_ceiling`, `ladder.max_weekly_change` in `src/guard/config/defaults/guard.yaml` |

No decisions are open before the advice engine.
