# VoiceOS Level-2 Acceptance Criteria

Level-2 = reliable, scalable, secure, commercially deployable platform around the existing AI. Level-3 conversation intelligence is out of scope.

Every workstream must satisfy five layers where applicable: implementation, automated tests, integration tests, runtime tests, production acceptance.

| # | Workstream | Implementation gate | Automated/integration gate | Runtime gate | Production gate |
|---|---|---|---|---|---|
| 1 | Production stabilization | Health, SLOs, logs, traces, alerts, backup, recovery | Unit/integration/failure tests pass | Real restart/recovery/alert/restore drills pass | Agreed SLO/error-budget observation passes |
| 2 | Telephony | In/out calls, callbacks, idempotency, scheduling, recording lifecycle | Contract/webhook/regression tests pass | Real staging calls and media WS pass | Carrier reliability/compliance approved |
| 3 | Dialer | Scheduling, pacing, concurrency, retries, callbacks, DNC/time windows | Unit/integration/load/failure tests pass | Concurrent dialing and crash recovery pass | Capacity target met without duplicate/lost effects |
| 4 | CRM | Canonical records, connectors, sync, retries, DLQ, reconciliation | Connector contract/integration tests pass | Real CRM test tenant sync passes | Consistency/recovery SLA met |
| 5 | Billing | Plans, entitlements, metering, invoices, payment state, reconciliation | Metering/idempotency/billing tests pass | Payment/webhook lifecycle passes | Provider and ledger reconcile |
| 6 | Tenants | Lifecycle, users, RBAC, credentials, limits, offboarding | RBAC/isolation/lifecycle tests pass | Real tenant A/B isolation passes | No cross-tenant access; repeatable lifecycle |
| 7 | Dashboard | Customer call/campaign/lead/billing/user journeys | Build/component/API/E2E pass | Rendered production-like flows pass | Customer operates agreed workflows without engineering |
| 8 | Analytics | Canonical metrics, aggregation, dashboards, exports | Metric/reconciliation tests pass | Realistic event stream produces correct metrics | Metrics reconcile to source records |
| 9 | Compliance | DNC/DND, consent, time windows, retention, PII, audit, deletion | Negative/security/compliance tests pass | Live blocked-call/retention/deletion/audit scenarios pass | Technical controls reviewed and enforced |
| 10 | Security | Authz, secrets, encryption, rate limits, scanning, audit | Static/security/fuzz tests pass | Deployed attack/control scenarios pass | No unresolved critical/high findings under approved gate |
| 11 | Scalability | Horizontal workers, backpressure, pools, autoscaling | Load/stress/failure gates pass | Target concurrency and sustained load pass | Capacity + headroom demonstrated |
| 12 | Multi-GPU | Registration, health, routing, drain, failover, versioning | Scheduler/failover tests pass | Actual GPU loss/drain/failover passes | No single GPU SPOF for agreed target |
| 13 | Deployment | CI/CD, staging, migration, health gates, rollback | CI/release gates pass | Staging deploy + rollback passes | Repeatable production promotion/rollback |
| 14 | Data platform | Events, durable records, retention, ETL/reporting, reconciliation | Schema/migration/data-quality tests pass | Backup/restore/failure tests pass | RPO/RTO/data-integrity requirements met |
| 15 | Model/voice infrastructure | Serving, health, warmup, versioning, capacity, rollback | Adapter/contract/latency/failure tests pass | Live STT/LLM/TTS health/latency/failure pass | Availability/latency/rollback gates met |
| 16 | Onboarding | Signup to tenant, credentials, test call and launch | API/UI/E2E pass | Test tenant completes journey | No manual server edits for agreed flow |
| 17 | Support | Cases, incidents, audited support access, SLA state | Authorization/audit/workflow tests pass | Real support workflow passes | Auditable support operation meets SLA |
| 18 | Cost optimization | Per-call/tenant/GPU/telephony cost attribution | Accounting/reconciliation tests pass | Realistic usage reconciles | Economics/margin controls measurable and enforceable |

If a criterion cannot currently be executed: DEFERRED — RUNTIME EVIDENCE REQUIRED.
Never lower a requirement because execution is difficult.

## Workstream 1 — Production Stabilization: objective gates

### Implementation criteria
1. Every critical HTTP service exposes separate liveness and readiness semantics. Liveness must not require dependency I/O; readiness must fail when hard dependencies required for serving traffic are unhealthy.
2. Core systemd services use bounded crash recovery with Restart=on-failure and explicit start-limit protection. Restart policy must not mask persistent configuration/dependency failures.
3. Prometheus loads all production alert rule groups used by the platform.
4. Monitoring covers service availability, CPU, memory, disk, network errors, queue/restart symptoms and GPU service availability where metrics exist.
5. Existing structured logging, log rotation, Loki and OpenTelemetry infrastructure is retained and not duplicated.
6. Existing backup jobs remain authoritative; a scheduled verifier checks actual backup artifacts and datastore persistence indicators without performing destructive restores.
7. Operational runbooks exist for BFF, Web API, voice runtime, dialer, Redis, PostgreSQL, MongoDB, Vault, GPU services, disk, memory, queue buildup, repeated crashes, degraded dependencies and recovery/rollback.

### Automated-test criteria
1. BFF liveness returns HTTP 200 without touching Postgres or Redis.
2. BFF readiness returns HTTP 200 only when both Postgres and Redis checks succeed; it returns HTTP 503 when a hard dependency fails.
3. Web API liveness returns HTTP 200 independent of dependency state.
4. Web API readiness returns HTTP 200 only when its health aggregator is healthy and HTTP 503 otherwise.
5. Systemd unit tests verify bounded Restart=on-failure and StartLimitIntervalSec=300/StartLimitBurst=5 for the core services.
6. Backup verifier shell syntax passes bash -n.
7. Existing regression suites remain required and must be executed before TESTED is awarded.

### Integration-test criteria
1. BFF readiness must interact with the real Postgres and Redis instances.
2. Web API readiness must interact with its configured health checks.
3. Prometheus must successfully load the complete rule set and scrape the configured service targets.
4. Backup verification must execute against actual MongoDB, Vault snapshot, Redis persistence and PostgreSQL WAL-archiver state.
5. Alertmanager must receive and route a real firing/resolved alert.

### Runtime-verification criteria
1. Kill/restart each supervised CPU service and observe liveness/readiness transitions.
2. Stop a hard dependency and verify readiness becomes unhealthy without causing an uncontrolled restart loop.
3. Restore the dependency and verify readiness recovers.
4. Verify Prometheus records target-down and infrastructure alerts from real metrics.
5. Verify GPU STT/LLM/TTS readiness and failure detection on the actual GPU node.
6. Verify scheduled backup timers execute and report success/failure.
7. Verify logrotate prevents uncontrolled log growth.
8. Verify trace retention is actually enforced in the deployed Jaeger storage.
9. Verify graceful shutdown drains active voice runtime work before process exit.

### Production-verification criteria
1. Core services remain stable over the agreed observation window without manual babysitting.
2. Error-rate and latency alerts fire only on meaningful sustained conditions and resolve after recovery.
3. Service restart storms are bounded and diagnosable rather than hidden.
4. Backup jobs produce recent artifacts and restore drills meet the approved RPO/RTO.
5. Operational dashboards expose service, dependency and resource health for the production environment.
6. No critical/high stabilization defect remains unresolved without an explicit accepted risk.

### Current result
Implementation: PARTIAL / IN_PROGRESS.
Automated tests: RUNTIME/EXECUTION EVIDENCE REQUIRED.
Integration tests: RUNTIME EVIDENCE REQUIRED.
Runtime verification: RUNTIME EVIDENCE REQUIRED.
Production verification: RUNTIME EVIDENCE REQUIRED.

Criteria are intentionally not lowered because the current coding environment cannot execute the live infrastructure gates.
