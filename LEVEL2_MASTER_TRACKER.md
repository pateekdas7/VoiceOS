# VoiceOS Level-2 Master Tracker

Baseline branch: claude/ssh-gpu-cpu-servers-y99fib
Baseline HEAD: 1646e0896322733e351740f9522db95026956d18
Current pre-Workstream-1 HEAD: f9a96abdade81a6717dc154bdc80b19ed1a336fc
Baseline UTC: 2026-09-25T07:27:25Z

Status vocabulary: NOT_STARTED, IN_PROGRESS, IMPLEMENTED, TESTED, RUNTIME_VERIFIED, BLOCKED, FAILED, DEFERRED. Never use COMPLETE.

| # | Workstream | Status | Current evidence | Verification state / remaining work |
|---|---|---|---|---|
| 1 | Production stabilization | IN_PROGRESS | Health, observability, DR, deployment/rollback and runbooks exist. | Current production SLO/recovery evidence still required. |
| 2 | Real telephony production layer | IN_PROGRESS | Twilio dialer, callbacks, media WebSocket entrypoint and CPU composition root exist. | Live carrier/media/callback/failure tests required. |
| 3 | Production dialer | IMPLEMENTED | dialer_worker.js, Redis queues, scheduling, retries, callbacks and concurrency tracking exist. | Real Redis/Twilio/load/crash tests required. |
| 4 | Complete CRM integration | IN_PROGRESS | CRM/collections services, repositories and synchronization primitives exist. | External connector, reconciliation and real-CRM verification required. |
| 5 | Billing + commercial infrastructure | IN_PROGRESS | Billing service/admin surface and migrations exist. | Payment-provider lifecycle, reconciliation and production metering require verification. |
| 6 | Tenant administration | IN_PROGRESS | Auth/authz, admin portal, tenant-oriented services and RBAC exist. | Full lifecycle and live tenant-isolation verification required. |
| 7 | Customer dashboard | IN_PROGRESS | Next.js frontend has admin/client areas, auth and API-facing components. | Full production journeys require E2E verification; Vercel status is currently failure. |
| 8 | Analytics / BI | IN_PROGRESS | Analytics, BI, reporting/export and observability data tooling exist. | Metric reconciliation against source-of-truth data required. |
| 9 | Compliance + governance | IN_PROGRESS | Compliance monitoring, policy engine, consent/audit/PII/governance components exist. | Live enforcement/retention/deletion/audit tests required. |
| 10 | Security hardening | IN_PROGRESS | JWT/mTLS, Vault, encryption, rate limits, audit and security scripts/tests exist. | Current scans and deployed-control verification required. |
| 11 | Scalability | IN_PROGRESS | K8s/IaC, queues, concurrency and load harness exist. | Target-capacity and sustained-load evidence required. |
| 12 | Multi-GPU / compute orchestration | IN_PROGRESS | GPU scheduler, fleet monitoring, warmup/capacity tooling and deployment assets exist. | Real routing/drain/failover evidence required. |
| 13 | Deployment engineering | IMPLEMENTED | Terraform, Helm/K8s, systemd, deploy/rollback scripts and CI/release workflows exist. | Actual staging promotion/rollback evidence required. |
| 14 | Data platform | IN_PROGRESS | Postgres/Alembic, MongoDB, Redis, event bus, repositories and reporting exist. | Data-quality, retention, backup/restore and reconciliation evidence required. |
| 15 | Model/voice infrastructure | IN_PROGRESS | STT/LLM/TTS adapters, GPU services, model manifests, warmup and circuit breakers exist. | Live health/latency/failure/version rollback required; no intelligence expansion. |
| 16 | Customer onboarding / self-service | IN_PROGRESS | Signup/auth/admin/campaign/lead flows and rollout tooling exist. | Fully self-service credential/test-call/launch journey requires E2E verification. |
| 17 | Operational support system | IN_PROGRESS | Admin, monitoring, audit, runbooks and operational tooling exist. | Unified support/SLA/incident workflow requires implementation/verification. |
| 18 | Cost optimization | IN_PROGRESS | cost_optimizer and GPU/ops analytics exist. | End-to-end cost attribution and margin verification require real usage. |

## Baseline counts
- Assessed: 18/18
- IMPLEMENTED: 2
- IN_PROGRESS: 16
- TESTED in this session: 0
- RUNTIME_VERIFIED in this session: 0
- Level-2 automated failures observed in this session: 0, because tests could not execute here
- Known external status: GitHub Vercel check = failure
- GitHub workflow runs returned for baseline HEAD: none

## Dependency order
1. Baseline test/release gates
2. Runtime stabilization, observability and recovery
3. Canonical data/event contracts and tenant isolation
4. Telephony production boundary
5. Dialer reliability/concurrency
6. CRM synchronization
7. Billing/entitlements/usage
8. Tenant administration and onboarding
9. Customer dashboard
10. Compliance/security enforcement
11. Analytics and cost attribution
12. GPU fleet/model infrastructure
13. Scale/load/backpressure
14. Deployment promotion/rollback
15. Operational support/SLA
16. Production acceptance/commercial readiness

## Evidence rule
Source code proves implementation only. Automated tests prove tested only when actually executed. Integration/runtime evidence must come from the real dependency/environment. Production Ready requires all applicable acceptance criteria and evidence.

## Workstream 1 — Production Stabilization

**Status: IN_PROGRESS**

Scope is operational stability only; no conversation-intelligence expansion was started.

| Area | Status | Evidence / wiring |
|---|---|---|
| BFF liveness/readiness | IMPLEMENTED | `/health/live`, dependency-aware `/health/ready` |
| Web API liveness/readiness | IMPLEMENTED | `/health/live`, `/health/ready`, HealthAggregator |
| systemd supervision | IMPLEMENTED | bounded restart/start-limit/backoff and graceful stop |
| CPU/RAM/disk/network | IMPLEMENTED | node-exporter + Prometheus infrastructure rules |
| GPU health | IMPLEMENTED | GPU scrape + fleet/unavailable alerts; runtime evidence pending |
| Redis/PostgreSQL | IMPLEMENTED | readiness, exporters/alerts, DR runbooks |
| MongoDB | IMPLEMENTED | Percona exporter + Prometheus scrape + exporter/database alerts + K8s/Compose wiring; runtime verification pending |
| Vault | IMPLEMENTED | file-backend DR backup; scheduled wrapper corrected in W1 |
| Backup verification | IMPLEMENTED | verifier + systemd service/timer + alert |
| Error-rate tracking | IMPLEMENTED | Python RED metrics + BFF counters/`/metrics` |
| Alerting | IMPLEMENTED | service/resource/application/BFF-dialer/backup rules + Alertmanager |
| Logs/rotation | IMPLEMENTED | structured logging + daily logrotate |
| Tracing | IMPLEMENTED | OTel/Jaeger assets; fresh runtime evidence pending |
| Runbooks | IMPLEMENTED | W1 stabilization set + existing DR procedures |
| Rollback/recovery | IMPLEMENTED | deploy/rollback/DR scripts; execution evidence pending |

### W1 implementation in this pass
1. Fixed duplicate `randomUUID` declaration in `bff.js`.
2. Added BFF Prometheus counters and `/metrics`.
3. Added BFF 5xx error-ratio alerts.
4. Removed the false hard-coded `Twilio/SIP=healthy` claim; it is `unknown` unless `TELEPHONY_HEALTH_URL` is configured.
5. Corrected scheduled Vault backup to use the existing file-backend DR implementation.
6. Enabled Node/Jest CI execution on this Level-2 branch.
7. Added required W1 operational runbooks.

### W1 verification state
- IMPLEMENTED: yes for the changes above.
- TESTED: not established by local execution.
- INTEGRATION VERIFIED: not established.
- RUNTIME VERIFIED: **RUNTIME EVIDENCE REQUIRED**.
- PRODUCTION VERIFIED: **RUNTIME EVIDENCE REQUIRED**.
- Pre-implementation SHA: `f9a96abdade81a6717dc154bdc80b19ed1a336fc`.
- W1 implementation commits: `a8b33f676c80d8ba2b12207e2954a4a800308af0`, `9b4d4b8d14234ee01874d2996340b0d0880b6e21`, `3f66005768a1dd01903cac8218f7257089df7ade`.
- Final W1 implementation SHA for this pass: `2bcf6b62f4cb4f48216deb31ce22b6a8e05bc2d4`.
- Runtime/production verification remains pending.


### 2026-09-25 execution attempt — environment re-check
At 2026-09-25T07:51:30Z UTC, the execution environment was re-tested before marking any W1 criterion verified. `git status --short` was unavailable because the runtime had no repository checkout. A fresh `git clone --branch claude/ssh-gpu-cpu-servers-y99fib --depth 1 https://github.com/pateekdas7/VoiceOS.git /tmp/VoiceOS` failed with exit 128: `Could not resolve host: github.com`. Therefore no automated suite or repository-local command was executed, and no W1 gate was promoted to TESTED or RUNTIME_VERIFIED. Current HEAD was independently confirmed through GitHub as `d4f699e2f43d1b3a021f0cdd3ba253ab15cb1efb`.

MongoDB remains PARTIAL for an implementation reason: the repository has a Docker Compose MongoDB healthcheck and DR tooling, but no MongoDB-specific Prometheus exporter/scrape target or MongoDB-specific service-health alert was found in the inspected monitoring configuration. Runtime verification is also unavailable.


### MongoDB monitoring remediation — source/configuration verification
MongoDB monitoring implementation is **VERIFIED BY SOURCE/CONFIGURATION**. A dedicated Percona MongoDB exporter, Prometheus scrape target, separate exporter-down and MongoDB-unavailable alerts, deploy-time secret injection, and Kubernetes network-policy paths are now wired into the existing observability architecture. MongoDB runtime monitoring remains **RUNTIME EVIDENCE REQUIRED**. Alertmanager fire → route → resolve remains **RUNTIME EVIDENCE REQUIRED**.


### 2026-09-25 — Workstream 1 Jaeger/OTel retention enforcement
The Jaeger retention gap was corrected without changing the 7-day policy. The deployed architecture is Jaeger 1.60 all-in-one with Badger local storage. The previous `retention.max_age: 168h` YAML was documentation/config intent only; it was not consumed by the actual Jaeger Deployment. The Deployment now explicitly passes `--badger.span-store-ttl=168h0m0s`, which is the native Jaeger/Badger retention mechanism for this architecture. The prior documentation claim of a `jaeger-badger-purge` CronJob was removed because no such job exists.

Jaeger retention implementation is **VERIFIED BY SOURCE/CONFIGURATION**. Automated execution is **NOT EXECUTED — ENVIRONMENT BLOCKED**. Runtime retention behavior is **RUNTIME EVIDENCE REQUIRED**; YAML inspection is not treated as runtime evidence. Workstream 2 remains untouched.


## Workstream 2 — Real Telephony Production Layer
**Status: PARTIALLY IMPLEMENTED — REMAINING IMPLEMENTATION WORK**

Actual path: tenant/campaign/lead → Redis tenant queue → dialer_worker.js → Twilio REST → Python /voice → signed admission token → Twilio Media Streams WSS → CallOrchestrator → audio/VAD/STT/ConversationEngine/TTS → Twilio media. Status callbacks reach BFF /dialer/callback. Durable call_attempts are written before provider initiation.

Phone-number tenant routing is now implemented through migration 037, TelephonyNumberResolver, signed /voice routing, WSS ticket tenant binding, and tenant-scoped outbound caller-ID selection. SIP remains adapter-level and is not wired into the live listener. Webhook ordering/state, recording lifecycle, provider rate limiting, callback timezone enforcement, and canonical billing/CRM event contracts remain partial.

Workstream 1 remains frozen. Workstreams 3/4/5 and Level-3 remain untouched.

## Workstream 2 — latest implementation slice
**Status: PARTIALLY IMPLEMENTED — REMAINING IMPLEMENTATION WORK**

Implemented since the previous W2 baseline: canonical provider callback normalization/transition guard; callback correlation against the durable `call_attempts` row with row locking and tenant/lead/pipeline mismatch rejection; terminal event deduplication remains on the existing idempotency table; call-attempt lifecycle migration now permits RINGING/CANCELLED/VOICEMAIL; outbound Twilio creation now has a bounded tenant-scoped CPS guard backed by Redis.

Automated execution remains blocked in this environment. Runtime and production evidence remain required.


## W2 continuation status — 2026-09-25

| Capability | Implementation | Tested | Integration | Runtime | Production |
|---|---|---|---|---|---|
| Recording production lifecycle | IMPLEMENTED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Callback timezone boundary | IMPLEMENTED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Provider failure contract | IMPLEMENTED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Webhook timeout/retry hardening | IMPLEMENTED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Telephony observability | IMPLEMENTED/PARTIAL | BLOCKED | BLOCKED | BLOCKED | BLOCKED |
| Canonical Billing/CRM event boundary | IMPLEMENTED | BLOCKED | BLOCKED | BLOCKED | BLOCKED |

W2 remains PARTIALLY IMPLEMENTED — REMAINING IMPLEMENTATION WORK because recording access is not yet wired through an authenticated public endpoint, and runtime/test execution remains unavailable.


### W2 continuation update — 2026-09-25
Current branch HEAD: `6963d0411cd4569cfa1e7087d554ac6af14e59f3`.

W2 implementation slices now include durable recording metadata/object-storage boundary and authenticated-by-capability recording access, callback timezone/DST policy, canonical provider failure classification with persisted retryability, bounded webhook execution, canonical durable telephony events, and bounded telephony/media/recording/callback telemetry. These are **IMPLEMENTED** by source inspection. **TESTED / INTEGRATION VERIFIED / RUNTIME VERIFIED / PRODUCTION VERIFIED: BLOCKED** because no executable repository/runtime is available. W2 remains **PARTIALLY IMPLEMENTED — REMAINING IMPLEMENTATION WORK**.


## W2 continuation — canonical event boundary implementation — 2026-09-25

**Status: PARTIALLY IMPLEMENTED — REMAINING IMPLEMENTATION WORK**

The canonical telephony event boundary is now implemented as a transactional PostgreSQL event + outbox contract, with tenant-scoped Redis delivery and bounded retry/DLQ handling.

### Added
- `telephony_event_boundary.js`
  - schema validation
  - fixed schema version `1.0`
  - fixed lifecycle event-type allowlist
  - deterministic event identity
  - safe downstream serialization
  - transactional event + outbox persistence
  - duplicate suppression
- migration 042 / Alembic 0042
  - `provider` field on canonical event record
  - `telephony_event_outbox`
  - `telephony_event_dlq`
  - retry/claim indexes and constraints
- `scripts/telephony/telephony_event_relay.js`
  - PostgreSQL row claiming with `SKIP LOCKED`
  - stale-claim recovery
  - tenant-scoped Redis delivery
  - bounded exponential retry
  - terminal DLQ transition
- `/dialer/callback` now persists canonical events through the outbox boundary.
- Focused tests for schema, serialization, identity, persistence, duplicate suppression, retry, DLQ and migration inventory.

### Boundary semantics
- Canonical event identity is deterministic and tenant/call/lifecycle scoped.
- One canonical event is persisted per event identity.
- One outbox row exists per canonical event.
- Downstream delivery is at-least-once; `event_id` is the deduplication identity.
- Redis delivery is tenant-isolated by queue key.
- Billing logic and CRM business logic remain out of scope.

### Verification state
- IMPLEMENTED: source-level implementation complete for this event-boundary slice.
- TESTED: **NOT EXECUTED — ENVIRONMENT BLOCKED**.
- INTEGRATION VERIFIED: **NOT EXECUTED**.
- RUNTIME VERIFIED: **RUNTIME EVIDENCE REQUIRED**.
- PRODUCTION VERIFIED: **RUNTIME EVIDENCE REQUIRED**.

Current execution-environment probe on 2026-09-25:
- repository checkout: unavailable
- Python 3.13.5: available
- Node 22.16.0: available
- npm 10.9.2: available
- pytest 9.0.2: available
- ruff: unavailable
- mypy: unavailable
- PostgreSQL client: unavailable
- Redis CLI: unavailable
- Mongo shell: unavailable
- Docker / Compose: unavailable
- kubectl: unavailable
- `git status`: failed because no repository checkout exists

Therefore no repository-local test, migration, Redis, PostgreSQL, Docker, Prometheus, S3 or Twilio runtime command was executed in this session.


## W2 implementation gate — 2026-09-25

The previously identified remaining W2 implementation gap was the canonical telephony event delivery boundary. That boundary is now implemented using PostgreSQL transactional event/outbox persistence, tenant-scoped Redis delivery, bounded retry, stale-claim recovery and durable DLQ handling. Focused tests and a migration inventory were added.

**W2 implementation gate: IMPLEMENTED.**

This does **not** mean W2 is runtime verified. Automated execution remains blocked because the coding environment has no repository checkout and lacks PostgreSQL/Redis/Docker tooling. Twilio, Media Streams, S3 and Prometheus runtime evidence also remains unavailable.

Therefore the current final W2 status is:

**WORKSTREAM 2 IMPLEMENTATION COMPLETE — RUNTIME VERIFICATION REQUIRED**

This status must not be promoted to VERIFIED until the applicable test, integration, runtime and production evidence is actually executed.


## W2 continuation — 2026-09-25 canonical event boundary
**Implementation:** COMPLETE for the canonical event boundary slice. PostgreSQL canonical event persistence is coupled to a durable outbox row; a bounded relay publishes to tenant-scoped Redis queues with retry and DLQ states. Billing and CRM business logic remain outside this boundary.

**Verification:** NOT EXECUTED. No local repository checkout/runtime was available, and no authorized PostgreSQL/Redis/Twilio/S3/Prometheus runtime was attached. Therefore no implementation-only evidence is promoted to TESTED, INTEGRATION VERIFIED, RUNTIME VERIFIED, or PRODUCTION VERIFIED.

Overall W2 status remains **PARTIALLY IMPLEMENTED — REMAINING IMPLEMENTATION WORK** until all remaining W2 implementation gaps are closed. This event-boundary slice itself is implementation-complete.


## W2 FINAL STATIC GAP AUDIT — 2026-09-25

**Inspected branch/HEAD:** `claude/ssh-gpu-cpu-servers-y99fib` @ `6e42dce528b84728613c06e082b6983baaed2a52`.

This is a source/tracking audit only. No repository-local test or runtime command was executed.

### Acceptance classification

| # | W2 item | Classification | Static finding |
|---|---|---|---|
| 1 | Phone-number lifecycle | IMPLEMENTED — awaiting verification | Tenant-scoped number table, ACTIVE/SUSPENDED/RELEASED state, inbound/outbound flags and resolver exist. Carrier provisioning itself remains external. |
| 2 | Tenant inbound/outbound routing | IMPLEMENTED — awaiting verification | /voice resolves To for inbound and From for outbound; WSS receives resolved tenant. |
| 3 | Twilio outbound creation | IMPLEMENTED — awaiting verification | Production dialer creates Twilio Calls with status callbacks and /voice URL. |
| 4 | Caller ID selection | IMPLEMENTED — awaiting verification | Tenant-owned active Twilio number, campaign-specific first; global fallback requires explicit flag. |
| 5 | /voice | IMPLEMENTED — awaiting verification | Signed Twilio request, AccountSid check, tenant resolution and admission-token issuance are wired. |
| 6 | Media Streams WSS boundary | IMPLEMENTED — awaiting verification | Real Starlette WebSocketRoute and CPU serve path exist. |
| 7 | Admission/authentication | IMPLEMENTED — awaiting verification | Single-use token is bound to CallSid/AccountSid/tenant and consumed before call resources are allocated. |
| 8 | Webhook authentication | IMPLEMENTED — awaiting verification | /dialer/callback validates Twilio signature; production fails closed when auth token is absent. |
| 9 | CallSID correlation | IMPLEMENTED — awaiting verification | Callback locks and resolves the durable call_attempts row by CallSid. |
| 10 | Canonical call state machine | IMPLEMENTED — awaiting verification | Provider normalization plus explicit transition table/terminal states exist. |
| 11 | Idempotency | IMPLEMENTED — awaiting verification | Existing idempotency_keys plus canonical event identity suppress duplicate effects. |
| 12 | Duplicate/out-of-order callbacks | IMPLEMENTED — awaiting verification | Terminal duplicates are suppressed and backward transitions return 200 without mutation. |
| 13 | Callback lifecycle | IMPLEMENTED — awaiting verification | Callback transaction updates call_attempts/active_calls and completion queue. |
| 14 | Recording lifecycle | IMPLEMENTED — awaiting verification | CREATE/PROCESSING/AVAILABLE/RETAINED/FAILED/DELETED lifecycle is wired into call teardown. |
| 15 | Recording storage abstraction | IMPLEMENTED — awaiting verification | Filesystem local/test and S3 production backends share RecordingStorage; production requires S3. |
| 16 | Retention/deletion | IMPLEMENTED — awaiting verification | retention_until, cleanup worker, deletion attempts and durable failure state exist. |
| 17 | Callback timezone policy | IMPLEMENTED — awaiting verification | IANA validation, explicit offsets, local wall time, DST nonexistent-time rejection and calling-window checks exist. |
| 18 | Provider failure classification | IMPLEMENTED — awaiting verification | Bounded failure classes and retryability contract are persisted on call_attempts. |
| 19 | Bounded provider retries | IMPLEMENTED — awaiting verification | MAX_RETRY_ATTEMPTS=3 with 60/300/900s scheduling plus classifier-driven retry on placement errors. |
| 20 | Webhook timeout/retry behavior | IMPLEMENTED — awaiting verification | DB statement timeout and bounded Redis completion/expiry operations return non-2xx on failure so provider retry behavior can occur. |
| 21 | CPS limiting | IMPLEMENTED — awaiting verification | Tenant-scoped Redis per-second limiter is invoked immediately before Twilio call creation. |
| 22 | Carrier/provider failure handling | IMPLEMENTED — awaiting verification | Busy/no-answer/voicemail/timeout/provider/auth/destination classes have explicit actions. |
| 23 | Telephony metrics | PARTIALLY IMPLEMENTED — code work remains | Core BFF/lifecycle/recording metrics exist, but media-failure, retry-attempt, callback-event and CPS wrapper functions in media_gateway/metrics.py have no live call sites found. |
| 24 | Structured logging/tracing | PARTIALLY IMPLEMENTED — code work remains | BFF trace IDs/structured logs exist, but SharedCallDependencies exposes an optional OTel tracer and the CPU composition root does not wire a tracer into the live telephony path. |
| 25 | Tenant isolation | IMPLEMENTED — awaiting verification | Phone resolution, caller ID selection, callback identity checks, active-call updates, recording access and event relay are tenant-scoped. |
| 26 | Canonical telephony events | IMPLEMENTED — awaiting verification | Fixed v1.0 lifecycle schema and deterministic SHA-256 identity are implemented. |
| 27 | PostgreSQL outbox | IMPLEMENTED — awaiting verification | Canonical event insert and outbox insert are performed through the callback transaction boundary. |
| 28 | Event relay | IMPLEMENTED — awaiting verification | PostgreSQL claim/recovery plus tenant-scoped Redis delivery exists. |
| 29 | Retry/backoff | IMPLEMENTED — awaiting verification | Bounded exponential retry with stale PROCESSING recovery exists. |
| 30 | DLQ | IMPLEMENTED — awaiting verification | Exhausted events are durably written to telephony_event_dlq and outbox state becomes DLQ. |
| 31 | Billing event boundary | IMPLEMENTED — awaiting verification | Canonical telephony event/outbox boundary is the W2 downstream contract; billing business logic/consumer remains outside W2. |
| 32 | CRM event boundary | IMPLEMENTED — awaiting verification | Same canonical downstream contract carries tenant/campaign/lead/call identity; CRM business logic remains outside W2. |
| 33 | Dialer interface boundary | IMPLEMENTED — awaiting verification | Live JS dialer/provider boundary plus durable call_attempt/callback/Redis completion contract are present. |
| 34 | Configuration/secrets | IMPLEMENTED — awaiting verification | Twilio credentials/secrets are environment-backed; production webhook/auth paths fail closed when required secrets are absent. |
| 35 | W2 documentation | IMPLEMENTED — awaiting verification | Six Level-2 tracking files and W2-specific architecture/verification notes exist. |
| 36 | W2 tests | PARTIALLY IMPLEMENTED — code work remains | Focused tests exist, but the test matrix explicitly records the Redis-backed CPS concurrency/integration test as not yet added. |

### Concrete implementation gaps

1. **Alembic revision graph collision:** the branch contains both `0037_compliance_violations.py` and `0037_telephony_phone_numbers.py`, each declaring revision `0037`; it also contains both `0038_require_crm_match.py` and `0038_call_attempt_lifecycle.py`, each declaring revision `0038`. W2 revisions `0039`→`0042` point through the ambiguous `0038` revision. The raw SQL 037–042 sequence is present, but the Alembic graph is not unambiguous. This is an implementation/migration-graph gap, not runtime evidence.
2. **Telephony tracing wiring:** `SharedCallDependencies.tracer` is optional, but `deployment/cpu/app.py::build_shared_call_dependencies()` does not construct/inject an OTel tracer. The W2 media path therefore has no source-level proof of active OTel spans.
3. **Telemetry call-site coverage:** `record_media_failure`, `record_retry_attempt`, `record_callback_event`, and `record_cps_limit_event` are defined but no live call sites were found in the inspected W2 path. Core metrics are present, but the declared telemetry surface is incomplete.
4. **CPS integration test:** the W2 test matrix explicitly says the Redis-backed provider-CPS integration/concurrency test is **NOT YET ADDED**. Source implementation exists, but the W2 test implementation is incomplete.

These are the only concrete W2 implementation gaps found in this static pass. No new event bus, queue abstraction, state machine, storage abstraction, retry framework, or telemetry framework is proposed.

### Verification-only blockers

The following are **not implementation gaps**:
- no repository checkout / no executable repo-local test environment
- no PostgreSQL runtime
- no Redis runtime
- no S3/object-storage runtime
- no Prometheus runtime
- no Twilio/carrier runtime
- no Media Streams runtime
- no authorized downstream consumer runtime

**Tests executed: 0.** Source/test-file existence is not execution evidence.

### Exact W2 SQL migrations present

- `scripts/db/migrations/037_telephony_phone_numbers.sql`
- `scripts/db/migrations/038_call_attempt_lifecycle.sql`
- `scripts/db/migrations/039_telephony_recordings.sql`
- `scripts/db/migrations/040_provider_failure_contract.sql`
- `scripts/db/migrations/041_telephony_call_events.sql`
- `scripts/db/migrations/042_telephony_event_outbox.sql`

W2 Alembic files present:
- `0037_telephony_phone_numbers.py`
- `0038_call_attempt_lifecycle.py`
- `0039_telephony_recordings.py`
- `0040_provider_failure_contract.py`
- `0041_telephony_call_events.py`
- `0042_telephony_event_outbox.py`

Also present, but unrelated W2 Alembic files with colliding numeric revision IDs:
- `0037_compliance_violations.py`
- `0038_require_crm_match.py`

### Tests present

Relevant W2 tests include:
- `tests/unit/services/test_telephony_phone_numbers.py`
- `tests/integration/services/test_twilio_tenant_routing.py`
- `tests/unit/services/test_twilio_admission.py`
- `tests/integration/services/test_twilio_ws_entrypoint_integration.py`
- `tests/unit/services/test_dialer.py`
- `tests/jest/bff/dialer.test.js`
- `tests/jest/bff/telephony_call_state.test.js`
- `tests/jest/bff/telephony_provider_boundary.test.js`
- `tests/jest/bff/telephony_callback_policy.test.js`
- `tests/jest/bff/telephony_provider_failure.test.js`
- `tests/jest/bff/telephony_call_event.test.js`
- `tests/jest/bff/telephony_event_boundary.test.js`
- `tests/jest/bff/telephony_event_relay.test.js`
- `tests/unit/services/test_recording_lifecycle.py`
- `tests/unit/services/test_telephony_metrics.py`
- `tests/unit/services/test_telephony_migrations.py`

### Final static-audit decision

**WORKSTREAM 2 PARTIALLY IMPLEMENTED — REMAINING IMPLEMENTATION WORK**

The canonical event boundary itself is implementation-complete. The overall W2 implementation gate cannot be marked complete because the four concrete gaps above remain. Runtime/test infrastructure gaps are tracked separately and do not substitute for these implementation findings.

W3/W4/W5 and Level-3 remain frozen.


## 2026-09-25 — W2 four-gap implementation closure

Fresh implementation pass limited to the four genuine gaps from the final static audit. No W3/W4/W5/Level-3 work was started.

### Implementation status
- Alembic revision collisions — FIXED. Pre-existing 0037 compliance and 0038 CRM revisions are preserved. W2 Alembic revisions now use 0043 through 0048, chained from 0038 through all six W2 migrations. No duplicate revision ID remains and no unrelated migration functionality was deleted.
- OTel live W2 composition — FIXED. The CPU composition root now builds the existing OTelTracer from the configured OTLP endpoint and injects it into SharedCallDependencies.tracer. Existing W2 media spans are retained; a call-termination span was added.
- Telemetry call sites — FIXED. Actual media pipeline failures, actual STT/TTS retry attempts, callback processing, CPS-limit rejection, and outbound retry execution now have bounded metric increments. No CallSID, phone number, raw error text, or other unbounded identifier is used as a metric label.
- CPS Redis integration test — FIXED. A real-ioredis integration test now covers N/N+1 allowance, concurrent acquisition, tenant isolation, epoch-second rollover, and Redis failure/fail-closed behavior. The test requires a real Redis service.

### Verification status
- Static/source checks: IMPLEMENTATION EVIDENCE PRESENT; AUTOMATED EXECUTION NOT EXECUTED.
- Migration application/rollback: NOT EXECUTED — POSTGRESQL RUNTIME UNAVAILABLE.
- OTel runtime export/scrape: NOT EXECUTED — OTEL/PROMETHEUS RUNTIME UNAVAILABLE.
- Telephony/Twilio/Media Streams: NOT EXECUTED — REAL TELEPHONY RUNTIME UNAVAILABLE.
- CPS Redis integration: NOT EXECUTED — REDIS RUNTIME UNAVAILABLE.
- S3 recording runtime: NOT EXECUTED — S3 RUNTIME UNAVAILABLE.

CURRENT W2 STATUS: WORKSTREAM 2 IMPLEMENTATION COMPLETE — RUNTIME VERIFICATION REQUIRED.
