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

## Workstream 1 — objective acceptance gates

| Gate | Implementation | Automated test | Integration test | Runtime verification | Production verification |
|---|---|---|---|---|---|
| Liveness/readiness | Separate live and dependency-aware ready endpoints | Route tests prove correct 200/503 behavior | Start BFF/API with real Redis/Postgres | Stop/recover dependency and observe ready transition | Controlled staging incident shows correct traffic gating |
| Error-rate tracking | BFF/Python counters exposed to Prometheus | Metrics/counter tests pass | Prometheus scrapes targets | Query error ratio after controlled 5xx | Alert threshold observed without noisy paging |
| Supervision | Bounded restart/backoff/start-limit policies | Unit-file tests pass | Units installed on CPU node | One safe crash recovers; repeated crash hits guard | Crash-loop drill confirms protection |
| Infra monitoring | CPU/RAM/disk/network/GPU rules | Config/rule validation passes | Exporters scrape | Controlled threshold/target failure fires alert | Observation confirms useful signal |
| Dependencies | Redis/Postgres/Mongo/Vault/GPU checks where supported | Failure-path tests pass | Real dependency connectivity | Dependency loss causes correct degradation/recovery | Staging incident detected/recovered |
| Backups | Existing jobs + artifact verification + correct Vault backend | Script/config tests pass | Verification against staging artifacts | Non-destructive restore succeeds | RPO/RTO/integrity demonstrated |
| Logs/traces | Structured logs, rotation, OTel pipeline | Logger/config tests pass | Collectors receive events | Rotation + trace query verified | Retention supports incident reconstruction |
| Alerts | Service/error/crash/resource/queue/backup rules | Rule validation passes | Alertmanager receives synthetic alert | Alert fires/routes/resolves | 24h observation shows acceptable noise |
| Recovery/rollback | Existing scripts + W1 runbooks | Script/config tests pass | Staging rollback/restore | Actual recovery executed | Agreed RTO/RPO met |

**W1 completion rule:** implementation alone never promotes a gate to RUNTIME VERIFIED or PRODUCTION VERIFIED. Missing runtime execution remains **RUNTIME EVIDENCE REQUIRED**.
