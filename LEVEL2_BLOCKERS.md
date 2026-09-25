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


## 2026-09-25 — W2 continuation blockers

| ID | Area | Severity | Status | Description | Exact next action |
|---|---|---|---|---|---|
| L2-W2-007 | Recording runtime | HIGH | BLOCKED | Recording lifecycle is implemented, but migration 039 and production S3 upload/presign/retention behavior have not been executed. | Apply migrations on authorized staging DB and run real recording upload/access/retention/deletion drills. |
| L2-W2-008 | Callback timezone runtime | MEDIUM | BLOCKED | Canonical timezone policy is implemented and unit tests are present, but no executable test environment is available. | Execute timezone/DST/working-window matrix in repository checkout. |
| L2-W2-009 | Provider failure runtime | MEDIUM | BLOCKED | Failure classifier and retry persistence are implemented, but Twilio/provider failure injection has not been executed. | Execute provider network/rate-limit/timeout/invalid-destination/auth failure drills. |
| L2-W2-010 | Canonical event DB | HIGH | BLOCKED | Migration 041 is not applied to an authorized PostgreSQL runtime. | Apply migration 041 and verify durable event ordering/idempotency under duplicate callbacks. |
| L2-W2-011 | Observability runtime | MEDIUM | BLOCKED | New telephony metrics are implemented but Prometheus scrape/query evidence is unavailable. | Scrape BFF metrics and verify lifecycle, webhook latency, setup latency, and failure signals. |
| L2-W2-012 | Recording access API | MEDIUM | OPEN | Storage authorization/presigned URL logic exists in the recording boundary, but no user-facing recording access endpoint is yet wired through the existing authenticated BFF surface. | Add the authenticated access endpoint without exposing object keys or credentials; keep W4/W5 out of scope. |


## Recording access endpoint resolution — 2026-09-25
L2-W2-012 is RESOLVED at implementation level: the authenticated BFF recording-access endpoint verifies tenant ownership, creates a 5-minute HMAC-bound internal request, and returns only the media gateway's short-lived presigned object URL. Object keys and storage credentials are not returned. Automated/integration/runtime/production verification remains blocked.


## 2026-09-25 — W2 continuation blockers

- **L2-W2-013 TEST EXECUTION:** BLOCKED — no executable repository checkout/runtime; all new W2 tests are NOT EXECUTED.
- **L2-W2-014 DATABASE:** BLOCKED — migrations 039–041 have not been applied against an authorized PostgreSQL runtime.
- **L2-W2-015 TELEPHONY RUNTIME:** BLOCKED — no authorized Twilio credentials/carrier runtime/Media Streams endpoint for real call and failure drills.
- **L2-W2-016 OBJECT STORAGE:** BLOCKED — production S3 upload/presigned access/retention/deletion has not been executed.
- **L2-W2-017 OBSERVABILITY:** BLOCKED — Prometheus scrape/query evidence for the new W2 telemetry is unavailable.


## W2 continuation blockers — canonical event delivery

| ID | Area | State | Exact blocker / required evidence |
|---|---|---|---|
| L2-W2-018 | Event boundary test execution | BLOCKED | Repository/test runtime is unavailable; new canonical boundary, relay, and migration tests are not executed. |
| L2-W2-019 | PostgreSQL outbox | BLOCKED | No authorized PostgreSQL runtime; migration 042 and transactional outbox persistence are not applied or integration-verified. |
| L2-W2-020 | Redis event delivery | BLOCKED | No authorized Redis runtime; tenant queue delivery, retry/backoff, stale-lock recovery and downstream-unavailable behavior are not executed. |
| L2-W2-021 | Event DLQ | BLOCKED | No runtime failure injection has exercised max-attempt transition into `telephony_event_dlq`. |
| L2-W2-022 | Downstream consumer | BLOCKED | No authorized downstream consumer/runtime is attached; at-least-once delivery and consumer-side event-id deduplication remain unverified. |


## W2 continuation blockers — 2026-09-25
| ID | Area | State | Exact blocker / required evidence |
|---|---|---|---|
| L2-W2-018 | W2 test execution | BLOCKED | No executable repository checkout/runtime in this session; Jest/pytest/ruff/mypy cannot be executed. |
| L2-W2-019 | PostgreSQL migrations 037–042 | BLOCKED | No authorized PostgreSQL runtime; ordering/syntax can be inspected but application/rollback cannot be claimed. |
| L2-W2-020 | Redis event relay | BLOCKED | No authorized Redis runtime; delivery/retry/stale-lock/DLQ behavior not executed. |
| L2-W2-021 | Canonical event DLQ | BLOCKED | No failure-injection runtime to exhaust attempts and inspect durable DLQ. |
| L2-W2-022 | Downstream consumer | BLOCKED | No authorized consumer/runtime; delivery and consumer-side idempotency remain unverified. |
| L2-W2-023 | Twilio runtime | BLOCKED | No authorized Twilio credentials/number/public WSS runtime. |
| L2-W2-024 | S3 recording runtime | BLOCKED | No authorized S3-compatible production storage runtime. |
| L2-W2-025 | Prometheus runtime | BLOCKED | No authorized monitoring runtime for scrape/telemetry verification. |
