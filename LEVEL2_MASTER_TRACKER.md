# VoiceOS Level-2 Master Tracker

Baseline branch: claude/ssh-gpu-cpu-servers-y99fib
Baseline HEAD: c33571a10b8882b1f3da5b0e0d25ebae7ce3c417
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

### Scope assessment at implementation start

| Area | Initial state | Evidence | Level-2 action |
|---|---|---|---|
| Liveness/readiness | PARTIAL | CPU media gateway already exposes /health, /health/live, /health/ready; Web API had /system/health only; BFF had /system/health only. | Added explicit BFF and Web API liveness/readiness endpoints. |
| Systemd supervision | PARTIAL | Core units used Restart=always with no StartLimit. | Changed core CPU services to Restart=on-failure with StartLimitIntervalSec=300 and StartLimitBurst=5. |
| CPU/RAM/disk/network monitoring | PARTIAL | node-exporter and disk alerts existed; CPU/memory/network alert coverage was incomplete. | Added CPU, memory, network error alerts. |
| GPU monitoring | IMPLEMENTED/PARTIAL | GPU healthcheck, GPU-node Prometheus target and GPU alerts already exist. | Preserved existing implementation; runtime verification remains required. |
| Redis/PostgreSQL | IMPLEMENTED/PARTIAL | CPU healthcheck and Web API health checks exist. | Added readiness semantics and retained existing health checks. |
| MongoDB | PARTIAL | CPU healthcheck pings MongoDB and validates indexes. | Backup artifact verification now includes MongoDB; runtime verification required. |
| Vault | PARTIAL | Vault snapshot timer/service and DR runbook exist. | Backup artifact verification includes Vault snapshots; runtime restore remains required. |
| Prometheus/alerts | PARTIAL | Extensive rules existed but voiceos_bff_dialer.yml was not loaded by prometheus.yml. | Loaded missing rule group and added service/infrastructure health alerts. |
| Logging | IMPLEMENTED | StructuredLogger, Fluent Bit, Loki and logrotate already exist. | No duplicate logger introduced; retention remains deployment/runtime evidence. |
| Tracing | PARTIAL | OTel collector and Jaeger exist; intended 7-day retention was documented but no purge mechanism was found in repository. | Recorded as remaining deployment/runtime gap; no unsupported purge command invented. |
| Backups | PARTIAL | MongoDB/Vault timers and PostgreSQL WAL/Redis persistence scripts exist. | Added read-only scheduled backup artifact verification. Restore verification remains runtime evidence required. |
| Runbooks | PARTIAL | Existing DB/DR/deployment/GPU runbooks existed. | Added 15 Workstream-1 operational runbooks for the requested failure classes. |

### Workstream 1 implementation status

Status: IN_PROGRESS

Implemented:
- BFF /health/live and /health/ready.
- Web API /health/live and /health/ready.
- BFF health no longer reports Twilio/SIP as healthy without an actual probe; Event Bus status follows its Redis dependency instead of being unconditionally healthy.
- Bounded systemd crash recovery for BFF, Web API, voice runtime, dialer worker and frontend.
- Prometheus now loads the existing BFF/dialer alert rule file.
- Added service-target-down, GPU-target-down, CPU, memory, network-error and container-restart-storm alerts.
- Added scheduled backup-artifact verification for MongoDB, Vault, Redis AOF and PostgreSQL WAL archiving.
- Added 15 production-stabilization runbooks.
- Added tests for BFF health semantics, Web API health semantics and bounded systemd restart policy.

### Verification state

- Automated repository tests: NOT EXECUTED because GitHub DNS/network access is unavailable to the execution container.
- Executed isolated check: bash -n /tmp/backup_verify.sh — PASS. This validates generated backup-verifier shell syntax, not runtime behavior.
- GitHub Actions cannot currently provide evidence for this branch from this environment.
- Runtime verification: RUNTIME EVIDENCE REQUIRED.
- Production verification: RUNTIME EVIDENCE REQUIRED.

### Remaining Workstream-1 acceptance gaps

1. Execute Python/Jest/Ruff/mypy/coverage on the actual branch checkout.
2. Execute service restart/readiness transition tests on the CPU node.
3. Verify Redis/PostgreSQL/MongoDB/Vault health against live services.
4. Execute GPU health and failure/recovery scenarios on the GPU node.
5. Verify Prometheus alert firing/resolution with real metrics.
6. Verify Alertmanager routing with real receivers.
7. Verify backup timers, successful artifact creation and restore procedures.
8. Establish actual trace-retention enforcement; repository currently documents 7-day intent but no purge job was found.
9. Verify log rotation and disk-growth behavior on the deployed host.
10. Verify continuous recovery/restart behavior without causing retry amplification.
