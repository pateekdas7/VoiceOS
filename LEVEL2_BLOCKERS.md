# VoiceOS Level-2 Blockers

| ID | Workstream | Severity | Description | Why it matters | Status | Next action |
|---|---|---|---|---|---|---|
| L2-B001 | All/testing | HIGH | Coding container cannot clone GitHub because github.com DNS/network resolution is unavailable. | Prevents actual local pytest/Jest/ruff/mypy/build execution. | BLOCKED | Use a network-enabled or repository-mounted execution environment and run baseline matrix. |
| L2-B002 | CI/CD | HIGH | No GitHub Actions workflow runs returned for baseline HEAD. | CI cannot currently serve as execution evidence for this commit. | BLOCKED | Obtain/trigger a workflow run and record job results. |
| L2-B003 | Dashboard/deployment | MEDIUM | GitHub reports Vercel status failure for baseline HEAD. | Frontend deployment is not currently evidenced green. | BLOCKED | Inspect Vercel deployment logs, fix, redeploy and record evidence. |
| L2-B004 | Runtime verification | HIGH | Live CPU/GPU/DB/Redis/Mongo/Vault/Twilio environment is not attached to this coding execution environment. | Real runtime gates cannot be marked verified. | BLOCKED | Run the runtime evidence matrix from authorized staging infrastructure. |

Implementation gaps that do not have an external dependency are tracked in LEVEL2_MASTER_TRACKER.md rather than hidden here.

## Workstream 1 blockers

| ID | Workstream | Severity | Description | Why it matters | Status | Exact next action |
|---|---|---|---|---|---|---|
| L2-B005 | W1 / automated testing | HIGH | Coding container cannot clone the designated branch because github.com DNS resolution fails. | Prevents execution of the actual pytest/Jest/Ruff/mypy/coverage suites. | BLOCKED | Provide a repository-mounted or network-enabled execution environment and run the Workstream-1 test matrix. |
| L2-B006 | W1 / runtime | HIGH | No authorized CPU/GPU/DB/Redis/Mongo/Vault/Prometheus runtime is attached to this session. | Service recovery, dependency failure, GPU and alert tests cannot be marked verified. | BLOCKED | Execute the runtime acceptance matrix on the authorized staging environment. |
| L2-B007 | W1 / tracing | MEDIUM | Jaeger configuration documents a 7-day retention intent, but no actual purge job/mechanism was found in the repository. | Traces can grow without a proven retention enforcement path. | BLOCKED | Establish and runtime-verify an actual Jaeger retention mechanism supported by the deployed Jaeger version. |
| L2-B008 | W1 / backup verification | MEDIUM | Backup verifier is scheduled, but its systemd timer and real artifact checks have not executed in this environment. | Backup validity is not proven by source/configuration alone. | BLOCKED | Run the timer and verifier on the CPU node, then perform authorized restore drills separately. |
| L2-B009 | W1 / alerting | MEDIUM | Alertmanager routing configuration exists, but no live firing/resolution test was executed. | Alert definitions alone do not prove an on-call notification path. | BLOCKED | Fire a controlled staging alert and record Alertmanager/receiver evidence. |


### 2026-09-25 execution re-check (2026-09-25T07:51:30Z UTC)
The execution-environment blocker was reproduced, not merely carried forward: a fresh branch clone failed with exit 128 because `github.com` could not be resolved. No automated or runtime W1 test was therefore executed. MongoDB's PARTIAL status is also confirmed as an implementation gap in the inspected monitoring layer: Compose healthchecking exists, but no MongoDB-specific Prometheus exporter/scrape target or service-health alert was identified. This remains open pending an implementation decision and runtime verification.
