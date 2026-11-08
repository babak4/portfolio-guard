# Paper-trading plan (about 3 months)

Advice only, no reliance: Guard produces the weekly advice and the daily check, you record what it
said and what you would have done. Real use starts only when the exit criteria below are met.
Commands here are unverified (written from memory); correct them on first use.

## Start conditions
- Phase 0 backtest acceptance criteria met (about -20% cap in 2008, 2020 and 2022; typical-window
  lag within 1.5% a year), with the report saved.
- Host deployed per `ops/deploy.md`: both services healthy, port scan passed, restore drill passed
  (date: ____).
- Start date: ____ . Planned end: start + about 13 weeks (extend if a week is lost to an incident).

## Weekly checklist (Wednesday, after 12:00 London)
- [ ] The weekly report arrived on time (note the time). If not, why, and did an alert explain it?
- [ ] `make status`: both jobs and the snapshot are fresh; not paused unexpectedly
- [ ] Read the advice: status light, signals fired, ladder step, trades, blocked items
- [ ] Record a row in the table below (advice, followed or not, what you would have done)
- [ ] `python -m guard.worker respond <run_id> followed|changed|ignored --note "..."`
- [ ] Any daily alerts this week: confirmed or not, and was the reaction right?
- [ ] Anything odd in the data (prices, FX, holdings)? Log an incident
- [ ] Backup ran; latest backup is copied off the host

## Weekly record

| Week | Run id | Report on time (y/n) | Advice (one line, no balances) | Followed? | What I would have done | Notes |
|---|---|---|---|---|---|---|
| 1 | | | | | | |

"Followed?" is one of followed, changed, ignored (the same values as `respond`). "What I would have
done" is your honest view independent of the advice, so disagreements are visible later.

## Incident log

Classify every incident:
- **data**: wrong, stale, missing or implausible input got through, or a stop was triggered by a bad
  source (for example a price jump, FX gap, wrong holdings).
- **logic**: the engine computed something wrong or contradictory (signal, ladder step, rebalancing,
  guardrail, report), or a job failed with a code error.
- **ops**: host, network or deployment trouble that did not change the numbers (does not count as
  data or logic, but a missed weekly report still counts against on-time delivery).

Template, one block per incident:

```text
Incident: #__   Date: ____-__-__   Week: __
Class: data | logic | ops
What happened:
How it was found (alert, report, my own check):
Effect on advice (none / wrong / missing):
Fix and date:
Test added (path):  
Counts against exit criteria: yes | no (why)
```

## Exit criteria (from the PRD)
Real use starts only when all are true:
1. Backtest acceptance criteria are met (recorded in the Phase 0 report).
2. The paper-trading period ended with no data or logic incidents. An incident resets the clock for
   the affected part: fix it, add a test, and run at least 4 clean weeks after the fix.
3. The weekly report arrived on time in 100% of weeks, or an alert explained each exception.

## Sign-off
- Criteria 1: ____  2: ____  3: ____  Date: ____
- Decision: go / extend / stop. Reason:
