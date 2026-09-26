# W3 Production Dialer: Baseline and Acceptance Checklist

## Scope and freeze

- Workstream: Level 2 W3, production campaign execution only.
- Baseline commit: `237bc09206de9b1127375f7fc69641c9c374cc7a`.
- Baseline checkout: detached `VoiceOS-w2-cleanup-checkout`.
- W2 cleanup state: 29 existing modified W2 files are present at W3 start. Preserve them exactly; do not include them in W3 changes unless a specific W3 integration dependency is proven and documented.
- W1 remains frozen. No Level-3 intelligence or W4 work is in scope.
- No runtime or production claim follows from source or local test evidence.

## Existing ownership map (source inspection, not runtime evidence)

| Concern | Current owner | Evidence / limitation |
|---|---|---|
| Campaign model and basic lifecycle | `src/libs/contracts/models/campaign.py`, `src/services/campaign_management/lifecycle.py`, `service.py` | DRAFT → REVIEW → APPROVED → ACTIVE/PAUSED → COMPLETED → ARCHIVED; no CANCELLED state or durable scheduler lease found in the initial inspection. |
| Campaign persistence | `src/libs/repositories/campaign.py`; Alembic files under `scripts/db/migrations/alembic/versions/` | Campaign CRUD is tenant-scoped. No W3 queue/job persistence table found in inspected migrations. |
| Campaign leads | `src/libs/repositories/campaign_lead.py` | Existing lead repository supplies a queued-lead query and status updates. |
| Python dial queue and execution | `src/services/dialer/queue.py`, `engine.py`, `session.py` | Redis sorted set uses `ZPOPMIN`; claim removes work before durable attempt acknowledgement. Session task ownership is process-local. Queue key includes tenant and campaign; lease/recovery is absent in the inspected code. |
| Node campaign worker | `dialer_worker.js`; `scripts/systemd/voiceos-dialer-worker.service` | Separate Redis/Postgres worker includes schedule checking, active-call tracking, retry/callback keys and crash reconciliation. Requires deeper contract and SQL audit before deciding which runtime is authoritative. |
| API/manual controls | `src/services/web_api/api.py`, `bff.js` | Campaign lifecycle APIs and dialer start/stop/status surfaces exist. The Python dialer routes are wired only when a session manager is provided. |
| Telephony boundary | W2 `src/services/telephony/`, `src/services/media_gateway/`, telephony callback/provider modules | W3 must call the existing W2 provider/admission/caller-ID boundary; no parallel Twilio or CPS implementation. |
| Metrics | `src/services/dialer/metrics.py`, campaign metrics, `monitoring/prometheus/` | Existing metrics are partial; label/cardinality and event coverage require audit. |
| Deployment | `scripts/jobs/run_dialer_worker.py`, `scripts/systemd/voiceos-dialer-worker.service`, `docker-compose.yml` | Worker entrypoints exist; runtime deployment has not been exercised in this environment. |

## Baseline checks before W3 changes

Timestamp: 2026-09-26 (Asia/Kolkata). Commands ran against the W2 cleanup checkout before W3 source changes.

| Command / scope | Result | Classification |
|---|---|---|
| Python: `pytest tests/unit/services/test_campaign_management.py tests/unit/services/test_dialer.py tests/unit/services/test_dialer_dnd.py tests/unit/services/test_phase7_dialer_worker.py tests/unit/libs/repositories/test_campaign_repository.py tests/unit/services/test_web_api_campaigns.py -q -p no:cacheprovider --no-cov` (Python 3.12 venv under Ubuntu Proot) | 80 passed, 8 errors. All 8 are fixture setup failures from `NameError: health_live is not defined` while constructing the Web API app; no test body ran for those cases. | TESTED baseline; failure exists before W3 changes. |
| Jest: `jest --runInBand tests/jest/bff/campaigns.test.js tests/jest/bff/dialer.test.js tests/jest/dialer_worker` | 42 passed, 11 failed, 6 suites. The campaign suite sends non-UUID campaign IDs and receives 400 from existing UUID validation where its expectations are 404/200/409. Dialer and worker suites passed. | TESTED baseline; campaign test/contract mismatch requires separate evidence before any change. |
| Prior clean-room W2 regression (same cleanup files) | Python W2: 243 passed, 1 GPU integration skipped; Jest W2: 72 passed; W2 Ruff and direct mypy clean. Redis CPS and real GPU integration unavailable. | W2 evidence inherited from the immediately preceding final verification; it is not W3 runtime evidence. |

The first attempted Termux-host Python path `/tmp/voiceos-w1-py312/bin/python` did not exist in that namespace (exit 127); the command was rerun successfully inside Ubuntu Proot with `/tmp/voiceos-w1-py312/bin/python`.

## W3 acceptance checklist

Status values: `NOT_STARTED`, `IMPLEMENTED`, `TESTED`, `AUTOMATED_VERIFIED`, `RUNTIME_VERIFIED`, `BLOCKED`, `DEFERRED`, `FAILED`. Each item needs linked code/test evidence and a separate runtime status where infrastructure is required.

| ID | Acceptance evidence required | Initial status |
|---|---|---|
| W3-1 Scheduler/lifecycle | Durable scheduled start/stop, activation/pause/resume/cancel/completion, tenant ownership, timezone/DST and restart idempotency tests | NOT_STARTED |
| W3-2 Eligibility | Deterministic audited eligibility reasons for tenant/campaign/lead/DNC/number/state/retry/callback/window/compliance/active-attempt rules | NOT_STARTED |
| W3-3 Concurrency | Atomic global/tenant/campaign/worker/provider capacity acquire/release, lease expiry and crash recovery under races | NOT_STARTED |
| W3-4 Pacing | Bounded CPS/burst/backpressure integrated with W2 provider CPS controls | NOT_STARTED |
| W3-5 Queue | Durable idempotent enqueue/claim/ack, priority/delay/retry/DLQ/visibility timeout and stale-job recovery | NOT_STARTED |
| W3-6 Retry | W2 failure classification mapped to bounded retry delay/count and durable next state | NOT_STARTED |
| W3-7 Callbacks | Tenant-owned durable schedule, timezone/working-hour checks, priority, cancellation, completion and deduplication | NOT_STARTED |
| W3-8 Number rotation | Reuse W2 caller-ID selection and test active/outbound/provider/tenant constraints | NOT_STARTED |
| W3-9 Pause/resume | No new claims while paused; in-flight calls preserved; queued, retry and callback work recover on resume | NOT_STARTED |
| W3-10 Compliance | One authoritative W2 timezone/calling-window policy on scheduler, retry, callback and manual paths | NOT_STARTED |
| W3-11 Crash recovery | Failure-injection evidence at pre/post claim, pre/post provider request, active call, callback and process restart | NOT_STARTED |
| W3-12 Duplicate protection | Durable atomic idempotency across duplicate messages, workers, callbacks and pause/resume races | NOT_STARTED |
| W3-13 Capacity | Queryable global/tenant/campaign/worker/provider capacity and queue/retry/callback/stale counts | NOT_STARTED |
| W3-14 Completion | No premature completion; deterministic exhausted-work handling across pending, active, retry, callback and blocked leads | NOT_STARTED |
| W3-15 Manual operations | Authenticated, authorized, tenant-scoped, idempotent and audited campaign/lead/queue/active-call operations | NOT_STARTED |
| W3-16 Observability | Bounded-label metrics and structured, PII-safe logs for attempts/outcomes/queues/capacity/retries/failures | NOT_STARTED |
| W3-17 Operational visibility | Reliable campaign status, queue/callback/retry/active counts, completion progress, throttling and worker health | NOT_STARTED |
| W3-18 Persistence/migrations | W3 schema, constraints/indexes, linear migration graph, upgrade/downgrade verification | NOT_STARTED |
| W3-19 Redis design | Inventory every W3 key with tenant scope, TTL, owner, atomicity and recovery/cleanup evidence | NOT_STARTED |
| W3-20 Security | Authorization, SQL and Redis tenant predicates, secret/PII handling, webhook and queue trust boundaries | NOT_STARTED |
| W3-21 Automated coverage | Campaign, eligibility, concurrency, pacing, retry, callback, queue, rotation, pause/resume, recovery, security and telemetry tests | NOT_STARTED |
| W3-22 Load | Local simulated multi-tenant/campaign/worker pressure measurements; production capacity remains a separate runtime gate | NOT_STARTED |
| W3-23 Failure injection | Redis/DB/provider/worker/queue/callback failure scenarios with observed recovery | NOT_STARTED |
| W3-24 Quality | Scoped Ruff, direct W3 mypy, syntax, migration graph, dependency/security checks, diff and debug/secret scan | NOT_STARTED |
| W3-25 Operations docs | Architecture, lifecycle, queue/key model, recovery commands, monitoring and known blockers | NOT_STARTED |
| W3-26 Runtime gate | Real Twilio, Redis, PostgreSQL, worker, CPU/GPU, Media Streams and carrier evidence only from actual infrastructure | BLOCKED pending infrastructure |

## Infrastructure gate

The local environment has no verified production Redis/PostgreSQL, dialer worker deployment, Twilio account/number/webhook ingress, GPU/voice services, or production Media Streams path. Redis/PostgreSQL-backed integration, load, and failure drills must be attempted only where real local services are available; otherwise record `BLOCKED` with the missing prerequisite. Real carrier and production-scale claims remain blocked until the real environment is exercised.

## W3 start blocker discovered during integration-boundary review

**Status: BLOCKED pending W2 boundary resolution; no W3 behavior changes have been made.** The deployed systemd entrypoint runs `scripts/jobs/run_dialer_worker.py`, which calls `deployment/cpu/app.py::build_dialer_services()`. That builder requires one process-wide `TWILIO_CALLER_ID` and injects it into `TwilioOutboundCallService`; `place_call()` always submits that value as Twilio `From`. This path does not select an active outbound number for the authenticated tenant/campaign. The Node `dialer_worker.js::TwilioDialer` has a separate tenant/campaign query against `telephony_phone_numbers`, but the systemd unit does not launch that Node worker. Therefore W3 number rotation cannot safely be wired through the currently deployed Python path without resolving whether the W2 outbound number-selection boundary is the Node query, a missing reusable service, or a different authoritative contract. This is reported as a W2 integration-contract defect; this audit does not alter either path.

Evidence: `scripts/systemd/voiceos-dialer-worker.service` runs the Python entrypoint; `deployment/cpu/app.py:720-758`; `src/services/dialer/outbound_call.py:42-56,72-75`; `dialer_worker.js:368-430`. Static caller ID behavior was observed from source only; no provider call was made.
