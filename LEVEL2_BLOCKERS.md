# VoiceOS Level-2 Blockers

| ID | Workstream | Severity | Description | Why it matters | Status | Next action |
|---|---|---|---|---|---|---|
| L2-B001 | All/testing | HIGH | Coding container cannot clone GitHub because github.com DNS/network resolution is unavailable. | Prevents actual local pytest/Jest/ruff/mypy/build execution. | BLOCKED | Use a network-enabled or repository-mounted execution environment and run baseline matrix. |
| L2-B002 | CI/CD | HIGH | No GitHub Actions workflow runs returned for baseline HEAD. | CI cannot currently serve as execution evidence for this commit. | BLOCKED | Obtain/trigger a workflow run and record job results. |
| L2-B003 | Dashboard/deployment | MEDIUM | GitHub reports Vercel status failure for baseline HEAD. | Frontend deployment is not currently evidenced green. | BLOCKED | Inspect Vercel deployment logs, fix, redeploy and record evidence. |
| L2-B004 | Runtime verification | HIGH | Live CPU/GPU/DB/Redis/Mongo/Vault/Twilio environment is not attached to this coding execution environment. | Real runtime gates cannot be marked verified. | BLOCKED | Run the runtime evidence matrix from authorized staging infrastructure. |

Implementation gaps that do not have an external dependency are tracked in LEVEL2_MASTER_TRACKER.md rather than hidden here.