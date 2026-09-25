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
| L2-B007 | W1 / tracing | MEDIUM | The deployed Jaeger 1.60 all-in-one previously did not consume the documented 7-day retention field. | Traces could grow without an enforceable retention path. | IMPLEMENTATION RESOLVED; RUNTIME BLOCKED | Runtime-verify the deployed Badger TTL with controlled aged traces. |
| L2-B008 | W1 / backup verification | MEDIUM | Backup verifier is scheduled, but its systemd timer and real artifact checks have not executed in this environment. | Backup validity is not proven by source/configuration alone. | BLOCKED | Run the timer and verifier on the CPU node, then perform authorized restore drills separately. |
| L2-B009 | W1 / alerting | MEDIUM | Alertmanager routing configuration exists, but no live firing/resolution test was executed. | Alert definitions alone do not prove an on-call notification path. | BLOCKED | Fire a controlled staging alert and record Alertmanager/receiver evidence. |


### 2026-09-25 execution re-check (2026-09-25T07:51:30Z UTC)
The execution-environment blocker was reproduced, not merely carried forward: a fresh branch clone failed with exit 128 because `github.com` could not be resolved. No automated or runtime W1 test was therefore executed. MongoDB's PARTIAL status is also confirmed as an implementation gap in the inspected monitoring layer: Compose healthchecking exists, but no MongoDB-specific Prometheus exporter/scrape target or service-health alert was identified. This remains open pending an implementation decision and runtime verification.


### MongoDB monitoring runtime blocker
| ID | Workstream | Severity | Description | Status | Exact next action |
|---|---|---|---|---|---|
| L2-B010 | W1 / MongoDB runtime monitoring | HIGH | MongoDB exporter and alerts are source/configuration verified, but no authorized Prometheus/MongoDB/Alertmanager runtime is attached. | BLOCKED | Execute exporter scrape, MongoDB failure/recovery, exporter failure, and Alertmanager fire → route → resolve drills on authorized staging infrastructure. |


### 2026-09-25 — Jaeger retention implementation status
L2-B007 implementation is resolved: the actual Jaeger Deployment now passes `--badger.span-store-ttl=168h0m0s`. Automated tests are **NOT EXECUTED — ENVIRONMENT BLOCKED** and runtime retention remains **RUNTIME EVIDENCE REQUIRED**. No additional W1 implementation work is planned after this task unless runtime evidence exposes a genuine defect.


## Workstream 2 blockers
| ID | Area | Severity | Status | Description | Next action |
|---|---|---|---|---|---|
| L2-W2-001 | Telephony tests | HIGH | BLOCKED | No repository checkout/network-enabled execution environment; github.com DNS fails. | Execute W2 test suites in authorized checkout. |
| L2-W2-002 | Twilio runtime | HIGH | BLOCKED | No authorized Twilio account/phone-number/callback/WSS runtime attached. | Run real outbound/inbound/media/webhook drills. |
| L2-W2-003 | Webhook/state ordering | HIGH | OPEN | BFF callback deduplicates terminal events but no single authoritative transition guard exists for all out-of-order events. | Implement canonical transition guard. |
| L2-W2-004 | Recording lifecycle | MEDIUM | OPEN | CallRecorder uses local files; production storage, signed access and deletion lifecycle are not established. | Add configurable production storage lifecycle. |
| L2-W2-005 | Provider rate limiting | MEDIUM | OPEN | Worker tracks calls/minute but does not enforce provider CPS/tenant burst limits authoritatively. | Add bounded limiter at call creation boundary. |
| L2-W2-006 | Callback scheduling | MEDIUM | OPEN | Callback timestamp lacks a canonical tenant/campaign timezone/no-call-window validation boundary. | Add timezone-aware callback validation. |

## Workstream 2 — current blockers
- **L2-W2-001 TEST EXECUTION:** no executable repository checkout/runtime; tests cannot be honestly marked PASS.
- **L2-W2-002 DB MIGRATION:** migration 038 not applied/verified against authorized PostgreSQL.
- **L2-W2-003 PROVIDER CPS:** limiter implemented but Redis-backed concurrent behavior not executed.
- **L2-W2-004 REAL TELEPHONY:** real Twilio outbound/inbound/webhook/media/recording/caller-ID drills remain RUNTIME EVIDENCE REQUIRED.
- **L2-W2-005 RECORDING:** production object-storage retention/deletion lifecycle remains open.
- **L2-W2-006 CALLBACK TIMEZONE:** campaign/tenant timezone-aware callback policy remains open.
