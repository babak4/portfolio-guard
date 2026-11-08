# Runbook: what to do when something fires

> **Unverified: the commands here were written from memory and have not been run on a real host.**
> Alert wording may differ from what the notifier sends; correct this file the first time each
> situation happens. Guard only advises: nothing here involves trades. Run `docker compose exec worker`
> in front of `python -m guard.worker ...` when the stack runs in Docker; locally use `make` targets.

Start with `make status` (or `python -m guard.worker status`): it shows whether advice is paused and
the last successful weekly, daily and snapshot runs.

## Stopped run (data or sanity stop)
A run stopped on purpose because data was missing, stale or implausible, or sources disagreed.
Guard does not guess, and there is no advice for that week.
1. Read the alert text and `docker compose logs worker` for the reason (never contains keys).
2. If a data source was briefly down, re-run: `make weekly ARGS="--slot <a date this week>"`.
3. If it stops again on the same check, do not override the threshold. Log a **data** incident
   (`docs/paper-trading.md`) and wait for the source or fix the provider.
4. Meanwhile keep to the previous advice; do not act on a stale report.

## Failed run (error, not a stop)
An exception, not a deliberate stop. Log a **logic** incident unless the cause was clearly
environmental (disk full, container killed).
1. `docker compose logs --tail 200 worker`; check disk (`df -h`) and `docker compose ps`.
2. Re-run once with `make weekly` / `make daily`. Runs are idempotent for the same slot.
3. If it fails again, `make pause`, open an issue with the traceback (remove any amounts), and fix
   before resuming.

## Missed heartbeat
No successful run when one was due (the worker may be down, the host rebooted, or the scheduler hung).
1. `docker compose ps` and `docker compose up -d`; check the worker is `healthy`.
2. `docker compose exec worker python -m guard.worker status`: compare "last ok run" ages with the
   schedule (weekly Wednesday 12:00 London, daily check, daily snapshot).
3. Run the missed job now (`make weekly`, `make daily`). A missed snapshot day is a gap, not
   backfilled; note it as an incident if it cost data.
4. If the host was down, check the clock zone and the DST note in `ops/deploy.md`.

## Unconfirmed daily move
The daily check saw a large fall (day, week or volatility spike) that a second source did not confirm.
1. Look at the reference index on two independent sites yourself.
2. If both show the move, treat it as real: read the dashboard status light, and run
   `make weekly` early if you want the ladder to react. The daily check never trades.
3. If only one source shows it, it is probably a bad print. Record it (data incident if the bad
   price got into stored history) and wait for the next daily check.

## Blocked advice
The engine produced no trades because a guardrail blocked them (for example a cap, floor, fee
ceiling, US exposure warning, GIA no-sell rule, or the pause switch).
1. Read the blocking reason in the report. Guardrails are config, not bugs.
2. If `paused: yes` in `status`, decide whether to `make resume`.
3. If the reason is wrong for your situation, change the value in
   `$GUARD_DATA_DIR/config/overrides.yaml` with a note in the decision log; never edit code to get
   advice through.

## Unknown fund
A holding is not in the fund registry, so it cannot be classified.
1. Add the symbol to `$GUARD_DATA_DIR/config/funds.yaml` (see `src/guard/config/defaults/funds.yaml` for
   the shape: index group, fee, currency, accumulating or distributing).
2. Re-run `make weekly`. Until then the fund is excluded from advice.

## Pause and resume
```bash
make pause     # stop all advice until resumed (python -m guard.worker pause)
make resume    # allow advice again
```
Pause before maintenance windows or whenever you do not trust the data. Runs while paused do not
advise.

## Respond to advice
Record what you actually did, so the audit log and the paper-trading table stay honest:
```bash
python -m guard.worker respond <run_id> followed|changed|ignored --note "what I did and why"
```
The run id is printed by `make weekly` and shown in the report.

## Restore
See `ops/deploy.md` section 8. In short: `make restore-drill` proves the latest backup works;
a real restore is `python -m guard.storage.backup restore <backup folder> --data-dir <dir> [--force]`
with the stack stopped. Then restore `.env` from your password manager and run `status`.
A backup that fails its manifest check is refused; use the next older one. A restore stops if any job
holds a lock, checks the staged copy before swapping, and keeps the old data as `<dir>.pre-restore-<stamp>`.

Backups: encrypt them (for example with `age`) before they leave the host and keep a copy off the VM.
The scheduled backup must report failure (the cron line in `ops/deploy.md` pings a healthchecks-style
URL and its `/fail` endpoint). `last_backup_at` in the control table shows when the last one finished.
