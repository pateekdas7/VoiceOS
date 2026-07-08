# DR Drill Report — <YYYY-MM-DD>

**Sprint:** Sprint-027
**Conducted by:** <name / session>
**Scenario:** <e.g. "Postgres primary failure — PITR restore drill">

---

## Timeline

| Time (UTC) | Event |
|---|---|
| | Drill initiated (`infra/dr/scripts/trigger-db-failover.sh`) |
| | Failure simulated / component stopped |
| | Recovery procedure executed (runbook: `infra/dr/runbooks/<name>.md`) |
| | Component restarted / promoted |
| | `infra/dr/scripts/verify-recovery.sh` passed |
| | Regression suite re-run, confirmed green |

## Result

- **Target RTO:** ≤ 30 min
- **Actual RTO:** <measured, seconds/minutes>
- **Target RPO:** ≤ 5 min
- **Actual data loss:** <none / describe>
- **Pass/Fail vs. targets:** <PASS / FAIL>

## Observations

<Anything unexpected during the drill — timing surprises, a step that required manual intervention not covered by the runbook, a script bug found and fixed, etc.>

## Action Items

| Item | Owner | Priority |
|---|---|---|
| | | |

---

*Template — copy to `infra/dr/DR_DRILL_REPORT_<date>.md` for each real drill.*
