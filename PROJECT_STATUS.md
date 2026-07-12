# VoiceOS v2 — PROJECT_STATUS.md

## Purpose

This document is the single source of truth for the current implementation status of the VoiceOS project. Unlike the architecture documents, this file is updated continuously throughout development. Every Claude Code session must read this document before beginning work.

---

## Project Overview

**Project Name:** VoiceOS v2
**Project Status:** 🔴 Sprint-028 INCOMPLETE / BLOCKED — real-infrastructure gates (latency p95 ≤ 1.5s, load test @ 500 users, chaos GPU failover, canary) cannot pass without GPU fleet and server availability. Sprint-029 Phase 1 (offline tooling) in progress. Milestones M-3 through M-6 complete; M-7 (Production Alpha) and M-8 (Founder Validation) NOT yet achieved.

**Architecture Status:** ✅ **Complete** (Volumes 1–7 + Documentation Suite frozen)

- ☑ Volume 1 Complete (Core Voice Architecture — incl. Ch.26 Runtime Deployment Topology)
- ☑ Volume 2 Complete (Conversation Intelligence)
- ☑ Volume 3 Complete (Reliability & Distributed Systems)
- ☑ Volume 4 Complete (Compliance, Security & Governance)
- ☑ Volume 5 Complete (SaaS Platform & Business Systems)
- ☑ Volume 6 Complete (Engineering Standards & Developer Handbook)
- ☑ Volume 7 Complete (Operations, Deployment & Hyper-Scale)
- ☑ Documentation Suite Complete (Documents 1–12)

**Planning Status:** ✅ **Complete** (generated 2026-06-29, traceability audit completed 2026-06-29)

- ☑ Implementation Roadmap generated (`implementation/ROADMAP.md`)
- ☑ All 8 epics defined (`implementation/EPICS.md`) + 2 extended epics
- ☑ All 34 sprints specified (`implementation/sprints/Sprint-001.md` through `Sprint-034.md`)
- ☑ Traceability audit complete — 100% of V1–V7 chapters mapped to sprints
- ☑ Backlog initialized (`implementation/BACKLOG.md`)
- ☑ Milestones defined (`implementation/MILESTONES.md`) — 13 milestones
- ☑ Build order documented (`implementation/BUILD_ORDER.md`)
- ☑ Dependency graph documented (`implementation/DEPENDENCY_GRAPH.md`)
- ☑ Sprint sequencing graph documented (`implementation/SPRINT_GRAPH.md`)
- ☑ CURRENT_SPRINT.md advanced to Sprint-002

---

## Current Development Phase

**Current Phase:** Sprint-028 BLOCKED (servers temporarily unavailable); Sprint-029 Phase 1 (offline work) in progress
**Current Epic:** E7 — Production Alpha (Sprint-028 incomplete) / E8 — Founder Validation (Sprint-029 Phase 1)
**Previous Epic:** E6 — SaaS Platform (closed 2026-07-06 — Milestone M-6 reached)
**Current Sprint:** Sprint-028 (BLOCKED) + Sprint-029 Phase 1 (in progress)
**Sprint Status:** 🔴 Sprint-028 INCOMPLETE — latency gate, load test, chaos GPU, canary, compliance audit gaps, security sign-off all remain FAIL/UNVALIDATED. Sprint-029 Phase 1 (offline evaluation suite) in progress.
**Previous Sprint:** Sprint-027 ✅ **Completed** 2026-07-08 — Monitoring, Alerting, Logging, Tracing & Disaster Recovery. — Monitoring, Alerting, Logging, Tracing & Disaster Recovery. Full production observability stack (Prometheus/Grafana/Alertmanager/Loki/FluentBit/OTel Collector/Jaeger) deployed for real into the `voiceos-ops` namespace: 26/26 real VoiceOS Kubernetes targets `up` in Prometheus; 15 Grafana dashboards (6 SLO/ops + 5 governance + 4 security) provisioned and verified via Grafana's own API; Loki log search by `call_id` verified against a real `StructuredLogger` JSON line delivered through the real FluentBit DaemonSet; a real OTLP trace verified end-to-end in Jaeger (OTel Collector → Jaeger, including the collector's own resource-attribute processor); Alertmanager routing verified with both a synthetic burn-rate alert and independently-observed real `NodeDown`/`GPUUnavailable` alerts, all routed correctly to `pagerduty-critical`. `monitoring/gpu_fleet/` (`GPUFleetHealthMonitor`/`ModelWarmupOrchestrator`/`FleetVRAMBudget`) and `src/services/cost_optimizer/`/`src/services/ops_analytics/` implemented and unit-tested (35 new tests, 95.24% coverage on the new code). A real Postgres failover DR drill (`infra/dr/scripts/trigger-db-failover.sh --mode pitr-restore`) completed in **4 seconds** (target ≤60s) with zero data loss — full regression suite unchanged (2091 passed/1 skipped) before and after; see `infra/dr/DR_DRILL_REPORT_2026-07-08.md`. 3 real infrastructure bugs found and fixed: this cluster's `containerd` CRI log format is plain-text, not the Docker json-file format FluentBit's default `docker` parser assumed (fixed with a `cri` parser); the same apiserver-NAT limitation TT-015 documented for the GPU node also blocks in-cluster pods from reaching `kubernetes.default.svc`, and FluentBit's constant retry against it was starving its own Loki delivery (fixed by removing the `kubernetes` filter — filed as **TT-015-addendum**); a stale hardcoded Alembic-head assertion in `healthcheck.sh` (`"0025"`→`"0026"`, same recurring bug class as every prior sprint). New **TT-017** filed (GPU node's STT/LLM/TTS never bound `/metrics`, same class as TT-006 — not fixed, GPU node access needs per-instance approval). `ruff`/`mypy --strict` (698 files)/`check_boundaries.py` all clean; local suite 2020 passed/72 skipped (90.50%), CPU node suite 2091 passed/1 skipped (90.87%). Full detail in CHANGELOG.md's Sprint-027 entry and `implementation/DONE.md`. Previous: Sprint-026 ✅ **Completed on Phase 1 AND Phase 2** 2026-07-07 — **Phase 2 update (same day):** the original CPU node (`216.48.191.142`, TT-014) was proven incapable of running Kubernetes and was migrated to a genuinely unrestricted VM (`101.53.141.75`, new canonical CPU node — see `deployment/CPU_NODE_STATE.md`) per explicit user direction. Full data-layer migration (Postgres 54 tables + migration `0026` applied for real, MongoDB, Redis — zero customer-data loss) plus fresh Vault/mTLS/datastore-auth provisioning. A real `kubeadm` v1.36.2 + Calico v3.28.0 cluster now runs the full `voiceos-platform` Helm umbrella chart for real: **26/26 pods Running/Ready** (`gpu-scheduler` correctly `Pending` — no GPU node joined, proving K8S-2 config is exactly right). Guaranteed QoS/PDB/NetworkPolicy all validated with real evidence. Full suite: 2056 passed/1 skipped/0 failed. GPU node join attempted (succeeded) then cleanly reverted after hitting a genuine cross-provider NAT limitation (TT-015) — GPU inference services unaffected throughout. 8 real infra bugs found and fixed (see CHANGELOG.md's Sprint-026 Phase 2 entry). TT-014 **RESOLVED**. Old node left running, untouched, pending a separate decommission decision. Original Phase 1 completion, same day: (Infrastructure as Code & Kubernetes Architecture — complete Terraform module tree [`infra/terraform/`: network/kms/kubernetes/database/redis/mongodb/object-storage/registry modules + dev/staging/production environments, AWS target], complete Helm umbrella chart + 25 service sub-charts [`infra/helm/voiceos-platform/`, every chart Guaranteed-QoS + PDB'd + NetworkPolicy'd, GPU taint/toleration only on `gpu-scheduler`], `infra/k8s/` cluster config [namespaces/priority-classes/resource-quotas/cluster-policies], `src/services/saas_ops/` [`FeatureFlagService`/`FleetRolloutManager`/`TenantDataMigration`/`EntitlementOpsService`, migration `0026`]. Phase 1 fully validated: `terraform validate`/`plan` (dev) 0 errors, `tfsec` 0 HIGH/CRITICAL (3 real findings found and fixed: public EKS endpoint, public-IP subnets, missing IMDSv2), `helm lint --with-subcharts` 0 warnings/26 charts, `helm template | kubeconform -strict` 180/180 valid, ruff/mypy --strict/boundaries clean, local suite 1985 passed/72 skipped (90.42%). **Phase 2 (real deployment) formally blocked, not attempted as a live change:** direct SSH investigation proved the CPU node cannot run any Kubernetes distribution — it is a capability-restricted notebook-server pod on a shared ML platform (`unshare` denied, `cap_sys_admin`/`cap_net_admin` absent), the same blocker for both kubeadm and k3s, unrelated to disk/RAM/CPU (all abundant). Filed as `implementation/BACKLOG.md`'s **TT-014**. Per explicit user decision, sprint closed on its fully-validated Phase 1 IaC deliverables. Full detail in CHANGELOG.md's Sprint-026 entry. **Epic E7 (Production Alpha) opened.** Previous: Sprint-025 ✅ Completed 2026-07-06, gap-filled + schema-extended 2026-07-07 (Part-3: migration `0025` additive on top of `0024` — `webhook_delivery_attempts`/`webhook_dead_letter_queue`/`api_key_usage`/`api_rate_limits`/`admin_audit_views` view/`api_keys.expires_at`+`plan_tier`; AI Config wired into `ConversationEngine.resolve_runtime_config()`; PromptVersioning wired into `CampaignService.activate()`'s pin gate; EventBus wired for exactly-once webhook dispatch; persisted rate limits + dual-window burst handling; full `APIKeyLifecycleService`/`APIKeyAdminController`; `admin_audit_views` query route — CPU node re-validated 1992 passed/31 skipped, infra-validation 27/27 PASS; full detail in CHANGELOG.md's "Part-3" entry and `implementation/adrs/ADR-002-sprint025-scope-expansion.md`). Original scope (Admin Portal, AI Configuration & Integration Platform — `src/services/admin_portal/` [`AdminAPI`/`create_admin_api()`, `TenantAdminController`, `UserAdminController`, `CampaignAdminController`, `BillingAdminController`, `AuditAdminController`, `AIConfigAdminController`] + `src/services/ai_config/` [`AIConfigService`, `PromptVersioningService`, `ModelConfigService`, `EvaluationRunService`] + `src/services/integration_platform/` [`IntegrationPlatformService`, `WebhookService`, `WebhookDeliveryEngine`, `WebhookSigner`] + `src/services/api_platform/` [`PublicAPI`/`create_public_api()`, spec-first OpenAPI 3.1, `sdk_stubs/` placeholders]; new repositories `PromptVersionRepository`/`ModelConfigRepository`/`WebhookRegistrationRepository`/`WebhookDeliveryRepository`/`APIKeyRepository`; `APIKeyValidator` gained an optional Postgres-backed `repository` fallback (additive); migration `0024` (additive: `prompt_versions`/`campaign_prompt_pins`/`model_configs`/`webhook_registrations`/`webhook_deliveries`/`api_keys`, 49 public tables total); new domain events `PTPBroken`/`CampaignCompleted`; new dependency `pyyaml`; new `api-specs/voiceos-public-v1.yaml` (OpenAPI 3.1) + `scripts/validate_openapi.py` (structural validator substituting for the unavailable `openapi-generator` CLI); AdminAPI/PublicAPI built as Starlette ASGI apps, not literal FastAPI (`fastapi` is not a project dependency, same Sprint-016 precedent); ruff ✓ mypy --strict ✓ (659 source files) check_boundaries ✓; 1889 passed/0 failed locally, 1960 passed/1 skipped on the CPU node against real Postgres/Redis/Vault/MongoDB (migration 0024 applied, head confirmed 0024, 49 public tables — the 1 skip is the pre-existing unrelated Devanagari-pipeline deferral, not a regression); `scripts/sprint025_infra_validation.py` 8/8 PASS (prompt version hash=sha256(template), publish→edit raises PromptImmutableError, model config campaign override wins over tenant default, real signed webhook HTTP round trip, 3-retry→DLQ, Postgres-backed API key resolution + rejection); `healthcheck.sh`'s new Admin Portal/AI Config/Integration Platform/API Platform section OK; 2 real-infra-only issues found and fixed [`ModelConfigRepository.upsert()`'s `ON CONFLICT ON CONSTRAINT` targeted a partial index name rather than a real constraint, fixed to target `(columns) WHERE <predicate>` directly; infra-validation script's own synthetic non-UUID `campaign_id` against a real UUID FK, fixed by creating a real `Campaign` row first — same recurring bug class as prior sprints], plus 1 pre-existing unrelated bug found and fixed [stale hardcoded migration-head assertions in both `test_migration_upgrade_downgrade.py` and `healthcheck.sh`, the latter live since Sprint-024] — see CHANGELOG.md. **Milestone M-6 SaaS Platform Complete reached.** Previous: Sprint-024 ✅ Completed 2026-07-06 (Billing Platform, Usage Metering & Analytics — `src/services/billing/` [`BillingService`, `SubscriptionManager`, `EntitlementEngine`, `InvoiceGenerator`, `PaymentProcessor`/`StripeGateway`/`RazorpayGateway`, `RateCard`] + `src/services/metering/` [`MeteringService`, `UsageCollector`, `UsageAggregator`, `UsageLimitEnforcer`] + `src/services/analytics/` [`AnalyticsService`, `CallAnalytics`, `CampaignAnalytics`, `DailyAggregationJob`, `RealtimeAnalytics`] + `src/services/reporting/` [`ReportingService`, `ReportScheduler`, `ExportService`, report `templates/`] + `src/services/bi_platform/` [`BIPlatformService`, `BIWarehouse`, `ForecastingEngine`, `CrossTenantBenchmarking`, `ExecutiveDashboard`]; new repositories `InvoiceRepository`/`CallDispositionRepository`/`AnalyticsDailyRepository`/`BIRepository`; new `BillingPolicyPack` (sixth built-in Policy Engine pack, domain="billing") + `PolicyEngineService.check_entitlement()`; migrations `0021`/`0022`/`0023` (additive tier/usage-type enum values + `invoices.line_items` JSONB; new `analytics_daily` table; new dedicated `bi_facts` Postgres schema); new domain events `STTTranscribed`/`LLMGenerated`/`GPUAllocated`; new dependencies `openpyxl`/`reportlab`; ruff ✓ mypy --strict ✓ (629 source files) check_boundaries ✓; 1862 passed/0 failed locally, 1927 passed/7 skipped on the CPU node against real Postgres/Redis/Vault/MongoDB (92.20% coverage, migrations 0021→0023 applied, head confirmed 0023, 43 public tables + `bi_facts` schema — the 7 skips are the pre-existing MongoDB-credential gap [6] + Devanagari-pipeline deferral [1], not regressions); `scripts/sprint024_infra_validation.py` 11/11 PASS (GROWTH subscription creation, usage event capture via real EventBus → Postgres, idempotency, GROWTH call-minute limit enforcement, invoice generation matching usage aggregate, campaign ptp_rate=0.30, scheduled DailyAggregationJob, BIWarehouse.refresh() into bi_facts, non-zero forecast, anonymized benchmark with no tenant IDs exposed, executive summary all-KPIs-present); `scripts/validate/usage_metering.py`/`usage_limit.py` both PASS; 2 real-infra-only issues found and fixed [non-UUID tenant_id values with no backing tenants row in new integration tests/validation script; a hand-written Redis Protocol rejecting the real redis.Redis client's wider .set() return type, retyped to Any] — see CHANGELOG.md. Previous: Sprint-023 ✅ Completed 2026-07-06 (Campaign Management & Contact Center Platform — `src/services/campaign_management/` [`CampaignService`, `CampaignLifecycle`, `AudienceSelector`, `ScheduleEngine`, `RetryPolicyEngine`, `ABTestingFramework`, `CallDispatcher`] + `src/services/contact_center/` [`SkillsBasedRouter`, `LiveTransferService`, `SupervisorService`, `AgentScreenContext`, `ContactCenterService`] + `src/services/hitl/` [`HITLQueue`, `SLAEnforcer`, `HumanReviewAPI`/`create_review_api`, `OverrideLogger`, `HITLDashboard`]; new repositories `CampaignAudienceRepository`/`CampaignResultRepository`/`HITLQueueRepository`/`HITLDecisionRepository`; `CampaignRepository` extended with `ab_test_variants` CRUD + `update_counts()`; `LoanAccountRepository` gained `select_cohort()`; migration `0020` reworks `campaigns.status` to the real 7-state lifecycle + adds `campaign_audiences`/`campaign_results`/`hitl_queue`/`hitl_decisions`; new domain events `CallTransferred`, `HITLItemEnqueued`/`SLABreached`/`DecisionRecorded`; `ConversationEngine` gains optional `contact_center_service` + `escalate_call()`/`campaign_id_for_call()`, `start_call()` gained an optional `campaign_id` param; `GovernanceLayer`/`AIGovernanceService`'s `human_oversight_router` retyped to a new `HumanOversightRouterPort` Protocol so the durable `HITLQueue` can be wired in wherever the Sprint-020 in-memory router was accepted; ruff ✓ mypy --strict ✓ (404 source files) check_boundaries ✓; 1769 passed/67 skipped locally (91.38% coverage), 1835 passed/1 skipped on the CPU node against real Postgres/Redis/Vault/MongoDB (91.84% coverage, migration 0020 applied, head confirmed 0020, 42 tables — the 1 skip is the pre-existing, unrelated Devanagari-pipeline deferral, not a regression); `scripts/sprint023_infra_validation.py` 10/10 PASS (campaign lifecycle DRAFT→REVIEW→APPROVED→ACTIVE via real Postgres + invalid-transition raises, RBI 21:00/3-calls-today both return `None` from the real Policy Engine, 100-dispatch A/B split 45/55, HITL enqueue→resolve durable across a fresh repository instance, HITL SLA breach detection, live-transfer AgentScreenContext includes transcript+summary, supervisor monitor read-only + barge-in mutes AI); 2 real-infra-only issues found and fixed [`hitl_queue.call_id` was typed UUID but `ScheduleEngine` builds synthetic non-UUID call identifiers, retyped to TEXT via a clean downgrade/upgrade cycle; stale hardcoded migration-head test assertion (`"0019"`→`"0020"`)] — see CHANGELOG.md. Previous: Sprint-022 ✅ Completed 2026-07-06 (CRM & Loan/Collections Management — `src/services/crm/` [`CustomerService`, `PartyService`, `CustomerContextAssembler`, `CustomerImporter`] + `src/services/collections/` [`LoanAccountService`, `EMIScheduleService`, `PromiseToPayService`, `SettlementService`, `CallbackScheduler`, `EscalationWorkflow`]; new repositories `EMIScheduleRepository`/`SettlementRepository`/`CallbackRepository`/`EscalationRepository`/`PartyRepository`; migration `0019` adds `settlements.approved_by`/`authorized_at` (additive — `settlements`/`callback_requests`/`escalation_records` tables already existed from migration 0007); new domain events `SettlementOffered`/`Accepted`/`Authorized`/`Disbursed`, `CallbackScheduled`, `EscalationTriggered`; `ConversationEngine` gains optional `context_assembler` + `start_call()`/`end_call()` (CustomerContext assembled exactly once per call, RI-5, backward-compatible default `None`); ruff ✓ mypy --strict ✓ (377 source files) check_boundaries ✓; 1696 passed/63 skipped locally (92.02% coverage), 1752 passed/7 skipped on the CPU node against real Postgres/Redis (92.40% coverage, migration 0019 applied, head confirmed 0019, 38 tables — 7 skips are the pre-existing TT-008 MongoDB-credential gap, not a Sprint-022 regression); `scripts/sprint022_infra_validation.py` 8/8 PASS (CustomerContext assembly correct amounts/DPD=30 from real Postgres, immutability, RI-5 blocks unauthorized source + permits crm_collections, PTP 10-concurrent-connections → 1 row, PTP audit event persisted, settlement offer→accept→authorize→disburse, context-assembly latency 0.60ms); 2 real-infra-only issues found and fixed [stale hardcoded migration-head test assertion (`"0018"`→`"0019"`); validation script's `audit_log` cleanup attempt correctly rejected by the Sprint-020 immutability trigger, fixed to skip it] — see CHANGELOG.md. Previous: Sprint-021 ✅ Completed 2026-07-06 (Multi-Tenancy, Tenant Lifecycle & User Management — `src/services/tenant_management/` [`TenantService`, `TenantLifecycle`, `TenantProvisioner`, `IsolationProfileManager`, `TenantSuspender`, `TenantDeleter`] + `src/services/org_management/` [`OrgHierarchy`, `OrgService`] + `src/services/user_management/` [`InvitationService`, `UserService`, `SSOIntegration` stub]; migration `0018` reworks `tenants.status` to the real 7-state lifecycle + adds `invitations`/`sso_config` tables; new `SaaSPolicyPack` (`domain="tenant"`) + `PolicyEngineService.check_tenant_active()`; new repositories `TenantRepository`/`OrganizationRepository`/`UserRepository`/`InvitationRepository`; additive `KMSClientProtocol.ensure_kek()` + `AuditLogger` tenant/user lifecycle wrappers; ruff ✓ mypy --strict ✓ (357 source files) check_boundaries ✓; 1661 passed/60 skipped locally (92.35% coverage), 1720 passed/1 skipped on the CPU node against real Postgres/Redis/MongoDB (92.70% coverage, migration 0018 applied, head confirmed 0018, 38 tables); `scripts/sprint021_infra_validation.py` 7/7 PASS; walking-skeleton e2e 6/6; 2 real-infra-only issues found and fixed [`RoleAssignment.assigned_by` UUID-column bug in `TenantProvisioner`'s default-admin assignment, fixed via an all-zero-UUID `SYSTEM_ACTOR_ID` sentinel; a `RedisClient`-wrapper-vs-raw-client mismatch in the validation script] — see CHANGELOG.md. **First sprint of Epic E6 (SaaS Platform).** Previous: Sprint-020 ✅ Completed 2026-07-05 (PII Protection, Audit, API Security & AI Safety — `src/libs/pii/` [`PIIDetector`, `PIIRedactor`, `PIITokenizer`, `PIIEntity`] + `src/libs/audit/` [`AuditLogger`, `AuditVerifier`, `AuditSearch`, `AuditEvent`] + `src/libs/api_security/` [`APIRateLimiter`, `InputValidator`, `SecurityHeaders`, `CORSPolicy`] + `src/libs/runtime_security/` [`ContainerSecurityPolicy`, `NetworkPolicy`] + `src/libs/ai_safety/` [`ContentModerator`, `PromptInjectionDetector`, `AIOutputValidator`, `HumanOversightRouter`] + `src/services/compliance_monitoring/` [`ComplianceMonitoring`, `SignalCorrelator`, `ComplianceAlerter`] + `src/services/incident_response/` [`IncidentResponse`, `PlaybookRegistry`, `RegulatoryNotifier`]; migration `0017` (`audit_log.seq`/`prev_hash`/`hash` hash-chain columns + `pii_tokens` table, additive); hash-chaining computed in `AuditRepository.append()` itself (single INSERT path, Sprint-014 extended not replaced); `StructuredLogger` now unconditionally redacts PII on every log path; `ContentModerator`/`HumanOversightRouter` wired into `GovernanceLayer`/`AIGovernanceService`, `PromptInjectionDetector` wired into `ConversationEngine`; `scripts/check_pii_logs.py` wired into CI as a new blocking gate; ruff ✓ mypy --strict ✓ (508 source files) check_boundaries ✓ check_pii_logs.py ✓; 1627 passed/57 skipped locally (93.34% coverage), 1679 passed/1 skipped/4 failed on the CPU node against real Postgres/Redis (93.38% coverage — the 4 failures are a documented pre-existing MongoDB-credential gap, TT-008, not a Sprint-020 regression); `scripts/sprint020_infra_validation.py` 12/12 PASS [PII redaction via real StructuredLogger, 100-event real hash chain valid, immutability trigger rejects UPDATE, PIITokenizer round-trip via real Redis, ContentModerator/PromptInjectionDetector, 5-event compliance-alert threshold + status flip, DATA_BREACH 72h timer + full incident lifecycle to CLOSED, SecurityHeaders, real-Redis rate limiting]; `scripts/validate/audit_chain.py` and `consent_gate.py` both PASS; walking-skeleton e2e 6/6; regression 1679/1683 passed (excl. the 4 documented Mongo-gap failures); 2 real-infra-only issues found and fixed during Phase 2 [stale migration-head test assertion — same recurring class as Sprint-015/016; a `healthcheck.sh` `REDIS_URL`-clobbering bug live since before Sprint-019's Redis-auth rollout, plus a credential-in-print leak discovered in a new script and 5 pre-existing ones, all fixed — see TT-007] — see CHANGELOG.md. **Milestone M-5 Compliance & Security Complete reached.** Previous: Sprint-019 ✅ Completed 2026-07-05 (Secrets Management, Encryption & Privacy Architecture — `src/libs/secrets/` [`SecretsManager`, `VaultProvider`/`HVACVaultClient`, `AWSSecretsProvider`, `SecretRotator`, `EmergencyRevocation`] + `src/libs/encryption/` [`EncryptionService`, `EnvelopeEncryption`, `AESGCMEncryptor`, `VaultTransitKMSClient`/`AWSKMSAdapter`, `CryptoShredder`, `TLSConfig`, `InMemoryDEKStore`/`PostgresDEKStore`] + `src/libs/privacy/` [`PrivacyEngine`, `PurposeRegistry`, `DataMinimizer`, `DataErasureJob`, `RetentionScheduler`, `LocalDiskObjectStore`]; migration `0016` (`data_encryption_keys`/`data_erasure_certificates` tables + `*_encrypted` PII columns, additive); `CustomerRepository` optionally encrypts/decrypts transparently; `scripts/check_secrets.py` wired into CI as a new blocking gate; self-hosted HashiCorp Vault (KV v2 + Transit) provisioned from zero on the CPU node — no cloud Vault/KMS account exists for this project; Redis/MongoDB auth enforced (net-new scope, separate from the already-resolved TT-002); ruff ✓ mypy --strict ✓ (455 source files) check_boundaries ✓ check_secrets.py ✓; 1570 passed/57 skipped locally (93.31% coverage), 1626 passed/1 skipped on the CPU node against real Postgres/Redis/MongoDB/Vault (93.35% coverage); `scripts/sprint019_infra_validation.py` 9/9 PASS [SecretsManager+real Vault KV, EnvelopeEncryption+real Vault Transit round-trip, CryptoShredder→KeyNotFoundError, CustomerRepository verified ciphertext+transparent decrypt, Redis/MongoDB auth rejection, DataMinimizer, full DataErasureJob run]; regression 1626/1627 passed; 3 real-infra-only issues found and fixed during Phase 2 [container `cap_ipc_lock` blocking Vault's exec — fixed per HashiCorp's documented container guidance; a base64-generated password breaking URL construction — fixed via proper URL-encoding; 3 pre-existing tests stale against the new migration/schema] — see CHANGELOG.md.)

---

## Repository Status

**Repository Version:** v2.0.28 (Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy — EXECUTED / NO-GO)
**Current Branch:** claude/ssh-gpu-cpu-servers-y99fib
**Last Commit:** Sprint-028 — 6 evaluation reports committed; 6 bugs fixed (4 TTS + STT CUDA OOM + httpx keepalive); GPU deployment docs updated; TT-024 filed. See CHANGELOG.md's Sprint-028 entry.
**Last Successful Build (CPU node, Phase 1 performance library):** 2026-07-11 (38 tests pass, 100% module coverage on `src/libs/performance_engineering/`)
**Phase 2 (GPU node 217.18.55.78 + CPU node 101.53.137.131):** All evaluation gates executed. Latency gate FAIL (p95=1950ms intra-DC, limit 1500ms; GPU thermal throttling). Load test FAIL (p95=16,524ms @ 10 users). Chaos 2/5 PASS. Security CONDITIONAL (3 HIGH findings, API gateway required). Compliance CONDITIONAL (15/15 enforcement tests pass; audit durability gap). Production Alpha NOT DEPLOYED (canary mechanism absent). Full detail in CHANGELOG.md's Sprint-028 entry and `evaluation/production-alpha-report.md`.

---

## Implementation Progress

**Total Epics:** 5 / 10 Completed (E1 closed 2026-06-30; E2 closed 2026-06-30 — Milestone M-2 reached; E3 substantially complete with Sprint-012 walking skeleton; E4 closed 2026-07-04 — Milestone M-4 reached; E5 closed 2026-07-05 — Milestone M-5 reached; E6 closed 2026-07-06 — Milestone M-6 reached; E7 opened 2026-07-07 with Sprint-026, Sprint-027 complete 2026-07-08, Sprint-028 executed 2026-07-11 / NO-GO)
**Total Sprints:** 27 / 34 Completed (Sprint-028 in progress / blocked — does not count as complete until latency gate passes)
**Overall Progress:** ~79% complete (27/34 done); Sprint-028 gates blocked on GPU fleet availability

---

## Next Actions

**Sprint-028 (BLOCKED):** When CPU/GPU servers (217.18.55.78 / 101.53.137.131) become available again:
1. Verify GPU services healthy: STT/LLM/TTS all responding on ports 8100/8000/8200
2. Run latency validation Run D (100 calls from CPU node): `python3 scripts/validate/latency_validation_phase2.py --gpu-host 217.18.55.78 --calls 100`
3. Gate: first-audio p95 ≤ 1500ms sustained across all 100 calls
4. If gate fails: escalate to GPU fleet (V7 Ch6) — single L4 thermal throttling is confirmed root cause
5. Complete remaining AC items per `CURRENT_SPRINT.md` blocked AC list

**Sprint-029 Phase 1 (in progress):** Offline founder validation tooling — `tests/ai_eval/founder_validation_suite.py`, synthetic fixtures, evaluation report template. Does not require servers.

---

## Sprint-by-Sprint Progress

| Sprint | Title | Status |
|---|---|---|
| Sprint-001 | Repository Scaffolding, Contracts & Invariants | ✅ **DONE** (2026-06-30) |
| Sprint-002 | Event Contracts & Data Models | ✅ **DONE** (2026-06-30) |
| Sprint-003 | Testing Infrastructure, CI/CD Pipeline & Developer Tooling | ✅ **DONE** (2026-06-30) |
| Sprint-004 | Media Gateway | ✅ **DONE** (2026-06-30) |
| Sprint-005 | Audio Session Manager | ✅ **DONE** (2026-06-30) |
| Sprint-006 | Audio Preprocessing Pipeline | ✅ **DONE** (2026-06-30) |
| Sprint-007 | VAD & Endpointing | ✅ **DONE** (2026-06-30) |
| Sprint-008 | GPU Scheduler | ✅ **DONE** (2026-06-30) |
| Sprint-009 | STT, LLM & TTS Adapter Services + Devanagari Enhancement | ✅ **DONE** (2026-07-03) |
| Sprint-010 | Intelligence Engines — Perception Layer | ✅ **DONE** (2026-07-03) |
| Sprint-011 | Intelligence Engines — Decision Layer | ✅ **DONE** (2026-07-03) |
| Sprint-012 | Conversation Orchestration — Walking Skeleton | ✅ **DONE** (2026-07-03) |
| Sprint-013 | Event Bus & Redis Architecture | ✅ **DONE** (2026-07-04) |
| Sprint-014 | Persistent Storage — Schemas & Migrations | ✅ **DONE** (2026-07-04) |
| Sprint-015 | State Persistence, Crash Recovery & Idempotency | ✅ **DONE** (2026-07-04) |
| Sprint-016 | Concurrency, Circuit Breakers, Health & Observability | ✅ **DONE** (2026-07-04) |
| Sprint-017 | Policy Engine & Regulatory Compliance | ✅ **DONE** (2026-07-04) |
| Sprint-018 | Authentication, Authorization/RBAC & AI Governance | ✅ **DONE** (2026-07-04) |
| Sprint-019 | Secrets Management, Encryption & Privacy Architecture | ✅ **DONE** (2026-07-05) |
| Sprint-020 | PII Protection, Audit, API Security & AI Safety | ✅ **DONE** (2026-07-05) |
| Sprint-021 | Multi-Tenancy, Tenant Lifecycle & User Management | ✅ **DONE** (2026-07-06) |
| Sprint-022 | CRM & Loan/Collections Management | ✅ **DONE** (2026-07-06) |
| Sprint-023 | Campaign Management & Contact Center Platform | ✅ **DONE** (2026-07-06) |
| Sprint-024 | Billing Platform, Usage Metering & Analytics | ✅ **DONE** (2026-07-06) |
| Sprint-025 | Admin Portal, AI Configuration & Integration Platform | ✅ **DONE** (2026-07-06) |
| Sprint-026 | Infrastructure as Code & Kubernetes Architecture | ✅ **DONE — Phase 1 AND Phase 2** (2026-07-07; real K8s on migrated VM, TT-014 resolved) |
| Sprint-027 | Monitoring, Alerting, Logging, Tracing & Disaster Recovery | ✅ **DONE — Phase 1 AND Phase 2** (2026-07-08; full observability stack deployed for real, DR drill 4s RTO) |
| Sprint-028 | Performance Validation, Load Testing, Pen Test & Production Alpha Deploy | 🟡 **CURRENT** |
| Sprint-029 | Founder Validation | ⬜ Pending |
| Sprint-030 | Pilot Deployment | ⬜ Pending |
| Sprint-031 | Production Release (final — depends on 032/033/034) | ⬜ Pending |
| Sprint-032 | Enterprise Platform: SSO, SCIM & Multi-Region | ⬜ Pending |
| Sprint-033 | Workflow Automation & Customer Success Platform | ⬜ Pending |
| Sprint-034 | Conversation Learning Layer | ⬜ Pending |

---

## Active Architecture References (for Sprint-016)

**Volumes:** Volume 3, Volume 7
**Relevant Chapters:**
- V3 Ch9–Ch17 (Concurrency, Queues, Circuit Breakers, Health, Service Discovery, Observability)
- V7 Ch7 (Production Operations)

---

## Current Components

**Completed (Sprint-001):**
- `src/libs/contracts/` — all 8 modules, 60 public types (primitives, audio, turn, response_plan, decision, context, streaming, events/envelope)
- `src/libs/invariants/` — `InvariantViolationError` + 8 guards (RI-1 through RI-8)
- `pyproject.toml`, `ruff.toml`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
- `tests/unit/contracts/` (5 modules, 113 tests), `tests/unit/invariants/` (8 modules, 71 tests), `tests/invariants/test_invariant_suite.py` (6 tests)

**Completed (Sprint-002):**
- `src/libs/contracts/events/` — 39 domain event subtypes across 6 files (audio, dialogue, intelligence, reliability, compliance, saas)
- `src/libs/contracts/models/` — 43 persistent model types across 8 files (customer, loan, collections, consent, campaign, tenant, user, billing)
- `scripts/db/migrations/` — 10 PostgreSQL DDL files (001–010), all idempotent
- `scripts/db/mongodb/` — 4 MongoDB index JSON files
- `tests/unit/contracts/test_domain_events.py` (80 tests), `tests/unit/contracts/test_models.py` (60 tests), `tests/integration/test_migrations.py` (14 tests)

**Completed (Sprint-003):**
- `tests/fixtures/` — FakeRTPStream, make_wav_bytes, TestPostgres, FakeRedisClient, TestRedis
- `tests/conftest.py` — 6 root shared fixtures
- `tests/integration/` — 3 connectivity test files (22 tests) + conftest skip markers
- `tests/unit/test_audio_harness.py` (38 tests), `tests/unit/test_boundary_checker.py`, `tests/unit/test_conftest.py`
- `scripts/check_boundaries.py` — AST-based boundary enforcer (4 rules)
- `docker-compose.yml` + docker/ configs (postgres, redis, mongo rs0, prometheus, grafana)
- Developer scripts (setup.sh, dev-up.sh, dev-down.sh, seed-db.sh, run-tests.sh, lint.sh + dev/ helpers)
- `.github/workflows/ci.yml` (6 stages) + `.github/workflows/release.yml` (4 stages)

**Completed (Sprint-005):**
- `src/services/audio_session_manager/` — `AudioSessionManagerService`, `AudioSession` (state machine), `AdaptiveJitterBuffer`, `PacketLossConcealer`, `SessionClock`, `metrics.py`
- `src/libs/contracts/audio.py` — `AudioFrame.is_plc: bool` added
- `tests/unit/services/test_audio_session_manager.py` (41 tests, 6 required named tests)
- `tests/integration/services/test_asm_integration.py` (5 MG↔ASM integration tests)

**Completed (Sprint-009):**
- `src/services/stt/` — `STTAdapter` Protocol, `WhisperAdapter` (faster-whisper, GPU-gated), `STTService`, metrics
- `src/services/llm_runtime/` — `LLMAdapter` Protocol, `vLLMAdapter` (SSE streaming), `PromptContract` (RI-7), `LLMService`, metrics
- `src/services/tts/` — `TTSAdapter` Protocol, `VeenaAdapter` (clause streaming), `ClauseSplitter`, `TTSService`, metrics
- `src/engines/prosody/` — `AdaptiveProsodyEngine` (EmpathyConfig → VoiceConfig)
- `deployment/gpu/services/stt/server.py`, `tts/server.py` — GPU inference servers (Whisper 8100, Veena+SNAC 8200)
- `tests/unit/services/test_stt.py`, `test_llm_runtime.py`, `test_tts.py`, `tests/unit/engines/test_prosody.py` (45 tests)
- New dependency: `httpx>=0.27`

**Completed (Sprint-013):**
- `src/libs/event_bus/` — `EventBus`, `Publisher`, `Consumer`, `EventDeduplicator`, `DLQHandler`, `EventRouter`, metrics
- `src/libs/redis_client/` — `RedisClient`, `DistributedLock`/`LockToken`, `RateLimiter`, `TTLGuard`/`MissingTTLError`
- `src/services/conversation_engine/event_bus_adapter.py` — `RedisEventBusAdapter` (implements `EventBusPort`)
- `src/engines/memory/working/store.py` — migrated to `TTLGuard.set()`
- `scripts/check_boundaries.py` — added TTLGuard-enforcement AST check
- `tests/unit/libs/`, `tests/integration/libs/` (event_bus + redis_client, 100% coverage), `tests/unit/services/test_conversation_engine_event_bus_adapter.py`, `tests/integration/services/test_conversation_engine_event_bus_integration.py`
- `scripts/sprint013_infra_validation.py` — operational smoke-test script (publish/consume, dedup, DLQ, fencing, rate limit, TTLGuard against real Redis)

**Completed (Sprint-014):**
- `alembic.ini` + `scripts/db/migrations/alembic/` — 13 revisions (`0001_tenants` … `0013_usage_events`) formalizing the Sprint-002 raw-SQL Postgres schema; `_ddl_helpers.py` (idempotent CHECK/UNIQUE constraint helpers)
- `scripts/db/mongodb/create_indexes.py` — applies the 4 Sprint-002 index specs (never previously run); 22 indexes across `response_plans`/`decision_envelopes`/`call_transcripts`/`call_lineage`
- `src/libs/repositories/` — `BaseRepository`, `CustomerRepository`, `LoanAccountRepository`, `PromiseToPayRepository`, `ConsentRepository`, `IdempotencyRepository`, `AuditRepository`, `CampaignRepository`, `BillingRepository`/`UsageRepository` — all mechanically tenant-scoped (AR-8), raw-SQL/psycopg2
- `audit_log` immutability trigger (Postgres, migration 0010) — defense-in-depth alongside the repository-level guard
- `tests/unit/libs/repositories/` (56 tests via `tests/fixtures/fake_pg.py`), `tests/integration/repositories/` (10 tests against real Postgres)

**Completed (Sprint-015):**
- `src/libs/state/` — `Recoverable` protocol + `StateSnapshot`, `Snapshot` (Postgres `snapshots` table), `EventTailReplay` (Redis Streams resume), `RecoveryLog` (Postgres `recovery_log` table)
- `src/libs/idempotency/` — `IdempotencyGuard.execute_once()` (atomic claim/complete over `idempotency_keys`), `IdempotencyKeyBuilder`, `FencingToken`/`FencingTokenTracker`
- `src/libs/recovery/` — `RecoveryManager` + `CPURestartStrategy`/`GPUFailureStrategy`/`RedisOutageStrategy`/`DBOutageStrategy`/`TwilioDisconnectStrategy`/`NetworkPartitionStrategy`
- `scripts/db/migrations/alembic/versions/0014_snapshots_and_recovery_log.py` — additive migration, head `0013` → `0014`
- `src/services/conversation_engine/session_state.py` (`ConversationSessionState`, the engine's first real `Recoverable`) + `engine.py`/`event_bus_adapter.py`/`src/libs/event_bus/publisher.py` updates (IdempotencyGuard wiring; `Publisher.publish_with_entry_id()`; `EventBusPort.publish()` now returns the real Redis entry ID)
- `scripts/validate/idempotency_test.py`, `scripts/sprint015_recovery_drill.py` — operational validation scripts (real Postgres/Redis)
- `tests/unit/libs/test_idempotency.py`, `test_state_persistence.py`, `test_crash_recovery.py`; `tests/unit/services/test_conversation_session_state.py`, `test_conversation_engine_recovery.py`; `tests/integration/libs/test_recovery_integration.py`

**Completed (Sprint-017):**
- `src/services/policy_engine/` — `PolicyEngine` (PDP: Redis cache → Postgres fallback → deny-override → audit), `PolicyEngineService` (façade), `PolicyDecision`/`PolicyOutcome`/`PolicyRule`/`PolicyCondition`/`PolicyEffect`, `PolicySet`, `PolicyInheritance`, `BreakGlassPolicy`/`BreakGlassDirective`, `metrics.py`
- `src/services/policy_engine/packs/` — `RBIPolicyPack` (6 rules), `DPDPPolicyPack` (4 rules), `AuthorizationPolicyPack` (2 rules), `AIGovernancePolicyPack` (3 rules), `ConversationalPolicyPack` (3 rules) — 18 built-in rules total
- `src/libs/repositories/policy.py` — `PolicyRepository` (Postgres fallback tier)
- `scripts/db/migrations/alembic/versions/0015_policies.py` — additive migration, head `0014` → `0015`; **applied to the production `voiceos` Postgres on the CPU node**
- `src/services/conversation_engine/engine.py` — optional `policy_engine_service` param + `check_call_admission()`; `src/engines/dialogue_policy/engine.py` — optional `PolicyLookupPort` hook (boundary-safe, no `src.services` import)
- `scripts/seed_policies.py`, `scripts/validate/rbi_calling_hours.py`, `scripts/sprint017_infra_validation.py` — deployment/validation scripts, **run successfully against the real CPU node**
- `tests/unit/services/test_policy_engine.py`, `tests/unit/libs/repositories/test_policy_repository.py`, `tests/integration/services/test_policy_engine_integration.py` + additions to `tests/unit/engines/test_dialogue_policy.py`

**Completed (Sprint-018):**
- `src/services/auth/` — `AuthContext`/`AuthMethod`, `JWTValidator` (RS256) + `issue_test_token()`, `OIDCProvider`, `MTLSEnforcer`, `APIKeyValidator`, `AuthMiddleware` (pure ASGI), `AuthService`
- `src/services/authz/` — `Role`/`ROLE_PERMISSIONS`, `RBACEngine`, `ABACEvaluator`, `JITPrivilege`/`JITGrant`, `TenantIsolationGuard`/`TenantIsolationViolationError`, `AuthzService`
- `src/services/ai_governance/` — `GovernanceLayer`, `LawOfAuthorityChecker`, `ExplainabilityEngine`, `AIGovernanceService`, `metrics.py`
- `scripts/pki/generate_mtls_certs.py` — self-managed internal PKI provisioning (CA + per-service leaf certs)
- `ConversationEngine` — `ai_governance_service` is now a **mandatory** constructor parameter; `TrueStreamingPipeline` runs the gate before every TTS synthesis call
- `tests/unit/services/test_auth.py`, `test_authz.py`, `test_ai_governance.py`, `tests/integration/services/test_auth_integration.py`

**Completed (Sprint-019):**
- `src/libs/secrets/` — `SecretsManager`, `VaultProvider`/`HVACVaultClient`, `AWSSecretsProvider`, `SecretRotator`, `EmergencyRevocation`, `metrics.py`
- `src/libs/encryption/` — `EncryptionService`, `EnvelopeEncryption`/`EncryptedPayload`, `AESGCMEncryptor`, `VaultTransitKMSClient`/`AWSKMSAdapter`, `InMemoryDEKStore`/`PostgresDEKStore`, `CryptoShredder`, `TLSConfig`, `metrics.py`
- `src/libs/privacy/` — `PrivacyEngine`, `PurposeRegistry`, `DataMinimizer`, `DataErasureJob`, `RetentionScheduler`, `LocalDiskObjectStore`, `metrics.py`
- `scripts/check_secrets.py` — regex + optional trufflehog secrets scanner, wired into CI as a blocking gate
- `scripts/db/migrations/alembic/versions/0016_encryption_privacy.py` — `data_encryption_keys`/`data_erasure_certificates` tables + `*_encrypted` PII columns (additive); `scripts/db/encrypt_pii_backfill.py`
- `src/libs/repositories/customer.py` — optional `encryption_service` param (transparent encrypt-on-write/decrypt-on-read)
- Self-hosted HashiCorp Vault (KV v2 + Transit) provisioned on the CPU node; Redis/MongoDB auth enforced
- `tests/unit/libs/test_secrets.py`, `test_encryption.py`, `test_privacy.py`, `tests/unit/test_check_secrets.py`, `tests/integration/libs/test_encryption_integration.py`

**Completed (Sprint-020):**
- `src/libs/pii/` — `PIIDetector` (layered regex — Aadhaar/PAN/phone/account/UPI/amount/name), `PIIRedactor` (`.redact()`/`.mask()`), `PIITokenizer` (in-memory + optional Redis/Postgres, AUDITOR-gated `detokenize()`), `PIIEntity`/`Sensitivity`
- `src/libs/audit/` — `AuditLogger` (PII-redacting façade + named convenience wrappers for every mandatory-coverage event type), `AuditVerifier` (independent hash-chain recomputation), `AuditSearch`, `AuditEvent`/`AuditEventType`; `AuditRepository.append()` (Sprint-014) extended with SHA-256 hash-chaining (`GENESIS_HASH`/`compute_audit_hash`, `SELECT ... FOR UPDATE` serialization)
- `src/libs/api_security/` — `APIRateLimiter`, `InputValidator`, `SecurityHeaders`, `CORSPolicy`
- `src/libs/runtime_security/` — `ContainerSecurityPolicy`, `NetworkPolicy`
- `src/libs/ai_safety/` — `ContentModerator`, `PromptInjectionDetector`, `AIOutputValidator`, `HumanOversightRouter`
- `src/services/compliance_monitoring/` — `ComplianceMonitoring`, `ComplianceRuleSet`, `SignalCorrelator`, `ComplianceAlerter`
- `src/services/incident_response/` — `IncidentResponse`, `PlaybookRegistry` (6 mandatory playbook classes), `RegulatoryNotifier` (DPDP 72h timer)
- `scripts/db/migrations/alembic/versions/0017_pii_audit_hash_chain.py` — additive migration (`audit_log.seq`/`prev_hash`/`hash` + `pii_tokens` table)
- `src/libs/observability/logger.py` — `StructuredLogger` now unconditionally redacts PII via `PIIRedactor` on every log path
- `src/services/ai_governance/governance_layer.py` — `ContentModerator`/`HumanOversightRouter` wired in (replacing the prior inline keyword check); `src/services/conversation_engine/engine.py` — optional `PromptInjectionDetector` screening on `turn.transcript`
- `scripts/check_pii_logs.py` (new CI gate), `scripts/sprint020_infra_validation.py`, `scripts/validate/audit_chain.py`, `scripts/validate/consent_gate.py`
- `tests/unit/libs/test_pii.py`, `test_audit.py`, `test_ai_safety.py`, `test_api_security.py`, `test_runtime_security.py`, `tests/unit/services/test_compliance_monitoring.py`, `test_incident_response.py` (55+ new tests)

**Completed (Sprint-021):**
- `src/services/tenant_management/` — `TenantService` (CRUD + lifecycle façade), `TenantLifecycle` (7-state machine), `TenantProvisioner` (KEK/Redis-namespace/default-admin/event on PRODUCTION activation), `IsolationProfileManager` (SHARED/DEDICATED_SCHEMA/DEDICATED_CLUSTER DDL), `TenantSuspender`, `TenantDeleter` (crypto-shreds the tenant KEK), `ports.py` (structural Protocols)
- `src/services/org_management/` — `OrgHierarchy.resolve_scope()`, `OrgService`, re-exports of the canonical `Organization`/`BusinessUnit`/`Branch`/`OrgScope` contracts
- `src/services/user_management/` — `InvitationService` (hashed-token invite→activate), `UserService`, `SSOIntegration` (explicit Sprint-025 stub)
- `src/libs/repositories/` — `TenantRepository`, `OrganizationRepository`, `UserRepository`, `InvitationRepository`
- `src/services/policy_engine/packs/saas.py` — `SaaSPolicyPack` (new `domain="tenant"` rule); `PolicyEngineService.check_tenant_active()`
- Migration `0018` — reworks `tenants.status` to the real 7-state lifecycle; adds `invitations`/`sso_config` tables
- Additive: `KMSClientProtocol.ensure_kek()`, `AuditLogger` tenant/user lifecycle wrappers, `contracts/models/tenant.py::TenantStatus` reworked
- `scripts/sprint021_infra_validation.py`
- `tests/unit/services/test_tenant_management.py`, `test_org_management.py`, `test_user_management.py`, `tests/integration/services/test_tenant_isolation.py` (60+ new tests)

**Completed (Sprint-022):**
- `src/services/crm/` — `CustomerService` (CRUD + search), `CRMRepositories` (repository-wrapper dataclass), `PartyService`, `CustomerContextAssembler` (RI-5: assembles sealed `CustomerContext` from CRM+Collections), `CustomerImporter` (CSV bulk import, dedup by `crm_id`)
- `src/services/collections/` — `LoanAccountService` (CRUD + real-time DPD), `EMIScheduleService` (schedule + DPD/next-EMI/overdue calc), `PromiseToPayService` (idempotent PTP), `SettlementService` (offer→accept→authorize→disburse), `CallbackScheduler`, `EscalationWorkflow`
- `src/libs/repositories/` — `EMIScheduleRepository`, `SettlementRepository`, `CallbackRepository`, `EscalationRepository`, `PartyRepository`
- Migration `0019` — additive `settlements.approved_by`/`authorized_at` (settlement-authorization gate)
- New domain events: `SettlementOffered`/`Accepted`/`Authorized`/`Disbursed`, `CallbackScheduled`, `EscalationTriggered`
- `src/services/conversation_engine/engine.py` — optional `context_assembler` + `start_call()`/`end_call()` (CustomerContext assembled once per call, cached, RI-5)
- `scripts/sprint022_infra_validation.py`
- `tests/unit/services/test_crm.py`, `test_collections.py`, `test_ptp_idempotency.py`, `tests/integration/services/test_customer_context_assembly.py` (38 new tests)

**Completed (Sprint-023):**
- `src/services/campaign_management/` — `CampaignService` (CRUD + lifecycle façade), `CampaignLifecycle` (7-state machine), `AudienceSelector` (DPD/outstanding/product cohort + DND/consent/dedup), `ScheduleEngine` (RBI-compliant, delegates to `PolicyEngineService`), `RetryPolicyEngine`, `ABTestingFramework` (deterministic hash variant assignment), `CallDispatcher`
- `src/services/contact_center/` — `SkillsBasedRouter`, `LiveTransferService` (AgentScreenContext assembly + audio bridge/mute), `SupervisorService` (monitor/barge-in/override), `AgentScreenContext`, `ContactCenterService`
- `src/services/hitl/` — `HITLQueue` (durable Postgres-backed), `SLAEnforcer` (CRITICAL/HIGH/MEDIUM tiers), `HumanReviewAPI`/`create_review_api`, `OverrideLogger`, `HITLDashboard`
- `src/libs/repositories/` — `CampaignAudienceRepository`, `CampaignResultRepository`, `HITLQueueRepository`, `HITLDecisionRepository`; `CampaignRepository`/`LoanAccountRepository` extended
- Migration `0020` — reworks `campaigns.status` to the real 7-state lifecycle + adds `campaign_audiences`/`campaign_results`/`hitl_queue`/`hitl_decisions`
- New domain events: `CallTransferred`, `HITLItemEnqueued`, `HITLSLABreached`, `HITLDecisionRecorded`
- `src/services/conversation_engine/engine.py` — optional `contact_center_service` + `escalate_call()`/`campaign_id_for_call()`; `start_call()` gained optional `campaign_id`
- `src/services/ai_governance/` — `human_oversight_router` retyped to a new `HumanOversightRouterPort` Protocol so `HITLQueue` can be wired in
- `scripts/sprint023_infra_validation.py`, `scripts/validate/rbi_scheduling.py`, `scripts/validate/hitl_queue.py`
- `tests/unit/services/test_campaign_management.py`, `test_contact_center.py`, `test_hitl.py`, `tests/integration/services/test_rbi_scheduling.py`, `test_hitl_sla.py` (73 new tests)

**Completed (Sprint-024):**
- `src/services/billing/` — `BillingService` (façade), `SubscriptionManager` (TRIAL/GROWTH/ENTERPRISE lifecycle), `EntitlementEngine` (routes every check through `PolicyEngineService.check_entitlement()`), `InvoiceGenerator` (aggregates uninvoiced `usage_events` into `InvoiceLineItem`s), `PaymentProcessor`/`StripeGateway`/`RazorpayGateway` (stubs), `rate_card.py` (`RateCard`, `TIER_USAGE_LIMITS`, `BASE_FEE_MINOR`), `metrics.py`
- `src/services/metering/` — `MeteringService` (façade), `UsageCollector` (idempotent capture from `saas.call.dispositioned`/`saas.stt.transcribed`/`saas.llm.generated`/`saas.gpu.allocated`), `UsageAggregator`, `UsageLimitEnforcer` (Redis fast-path + `EntitlementEngine`), `metrics.py`
- `src/services/analytics/` — `AnalyticsService` (façade), `CallAnalytics`, `CampaignAnalytics`, `DailyAggregationJob`, `RealtimeAnalytics`
- `src/services/reporting/` — `ReportingService` (façade), `ExportService` (CSV/XLSX/PDF), `ReportScheduler`, `templates/` (Campaign Summary, Collections Performance, Compliance Audit)
- `src/services/bi_platform/` — `BIPlatformService` (façade), `BIWarehouse` (daily refresh into `bi_facts.fact_daily`), `ForecastingEngine` (exponential smoothing / documented linear-trend proxy), `CrossTenantBenchmarking` (anonymized, surrogate-key-only), `ExecutiveDashboard`, `models.py`
- `src/libs/repositories/` — new `InvoiceRepository`, `CallDispositionRepository`, `AnalyticsDailyRepository`, `BIRepository`; `BillingRepository`/`UsageRepository` gained idempotent `record_usage()`/`find_all_between()`/`mark_invoiced()`; `CampaignResultRepository` gained `find_between()`
- Migrations `0021` (additive `TRIAL`/`STT_TOKEN`/`LLM_TOKEN`/`GPU_SECOND` enum values + `invoices.line_items` JSONB), `0022` (new `analytics_daily` table), `0023` (new dedicated `bi_facts` Postgres schema)
- New domain events (additive): `STTTranscribed`, `LLMGenerated`, `GPUAllocated`
- New `BillingPolicyPack` (sixth built-in Policy Engine pack, `domain="billing"`) + `PolicyEngineService.check_entitlement()`
- New dependencies: `openpyxl`, `reportlab`
- `scripts/sprint024_infra_validation.py`, `scripts/validate/usage_metering.py`, `scripts/validate/usage_limit.py`

**Completed (Sprint-025):**
- `src/services/admin_portal/` — `AdminAPI`/`create_admin_api()` (Starlette, base path `/admin/v1`, `AdminRoleGateMiddleware` ADMIN/SUPERVISOR-only, `AuditMiddleware` mechanical mutation auditing), `TenantAdminController`, `UserAdminController`, `CampaignAdminController`, `BillingAdminController`, `AuditAdminController`, `AIConfigAdminController`
- `src/services/ai_config/` — `AIConfigService` (façade), `PromptVersioningService` (immutable-once-published, SHA-256 hash), `ModelConfigService` (global→tenant→campaign inheritance, Redis-cached), `EvaluationRunService` (deterministic keyword-match scorer)
- `src/services/integration_platform/` — `IntegrationPlatformService` (façade), `WebhookService` (registration + EventBus fanout), `WebhookDeliveryEngine` (HMAC-signed, 3-retry→DLQ), `WebhookSigner`
- `src/services/api_platform/` — `PublicAPI`/`create_public_api()` (Starlette, base path `/v1`, `X-API-Key` auth + per-tier rate limiting), `openapi.py` (spec-first, loads `api-specs/voiceos-public-v1.yaml`), `rate_limits.py`, `sdk_stubs/` placeholders
- `src/libs/repositories/` — new `PromptVersionRepository`, `ModelConfigRepository`, `WebhookRegistrationRepository`, `WebhookDeliveryRepository`, `APIKeyRepository`; `CallDispositionRepository` gained `get_by_call_id()`
- Migration `0024` (additive: `prompt_versions`, `campaign_prompt_pins`, `model_configs`, `webhook_registrations`, `webhook_deliveries`, `api_keys`)
- New domain events (additive): `PTPBroken`, `CampaignCompleted`
- `APIKeyValidator` gained an optional Postgres-backed `repository` fallback (additive)
- New dependency: `pyyaml`
- `api-specs/voiceos-public-v1.yaml`, `scripts/validate_openapi.py`, `scripts/sprint025_infra_validation.py`

**Completed (Sprint-026, Phase 1):**
- `infra/terraform/` — network/kms/kubernetes/database/redis/mongodb/object-storage/registry modules + dev/staging/production environments
- `infra/helm/voiceos-platform/` — umbrella chart + 25 service sub-charts (Deployment/Service/ConfigMap/HPA/PDB/NetworkPolicy/ServiceAccount each)
- `infra/k8s/` — namespaces, priority classes, resource quotas, cluster policies
- `src/services/saas_ops/` — `FeatureFlagService`, `FleetRolloutManager`, `TenantDataMigration`, `EntitlementOpsService`; migration `0026`
- Phase 2 (real deployment) blocked — see `implementation/BACKLOG.md` TT-014

**Pending:** Full system per roadmap E7–E8 (Sprint-027 next)

---

## Testing Status

| Suite | Status |
|---|---|
| Unit Tests (contracts) | ✅ 193 passing (113 Sprint-001 + 80 domain events Sprint-002) |
| Unit Tests (models) | ✅ 60 passing (Sprint-002) |
| Unit Tests (invariants) | ✅ 71 + 5 = 76 passing |
| Unit Tests (Sprint-003 harness) | ✅ 57 tests (audio harness 38 + boundary 7 + conftest 12) |
| Unit Tests (Sprint-004 media gateway) | ✅ 57 tests |
| Unit Tests (Sprint-005 audio session manager) | ✅ 41 tests (6 required named tests) |
| Unit Tests (Sprint-006 audio preprocessing) | ✅ 62 tests |
| Unit Tests (Sprint-007 VAD + endpointing + bargein) | ✅ 54 tests (22 + 15 + 17) |
| Unit Tests (Sprint-008 GPU Scheduler) | ✅ 35 tests |
| Unit Tests (Sprint-009 STT/LLM/TTS/Prosody) | ✅ 47 tests (STT 8 + LLM 13 + TTS 15 + Prosody 11) — 2 new TTS streaming tests added Sprint-012 Phase 3 |
| Unit Tests (Sprint-011 Decision Layer engines) | ✅ 116 tests (Risk 18 + DialoguePolicy 13 + Strategy 20 + GoalPlanner 16 + Negotiation 28 + Empathy 21) |
| Unit Tests (Sprint-012 Orchestration) | ✅ 89 tests — OutputValidator(14) + PlaybackScheduler(11) + KnowledgeRetrieval(8) + ConversationQuality(12) + DialogueManager(10) + ConversationEngine(12) + PromptBuilder(8) + OutputEvaluation(7) + AdaptiveConversation(7) |
| E2E Tests (Sprint-012 Walking Skeleton) | ✅ test_walking_skeleton.py — full pipeline mock pass; 6/6 still passing after Sprint-013 EventBus wiring |
| Unit + Integration Tests (Sprint-013 EventBus + Redis reliability) | ✅ 60+ tests — event_bus(EventBus/Publisher/Consumer/Dedup/DLQ/Router) + redis_client(RedisClient/DistributedLock/RateLimiter/TTLGuard) + ConversationEngine adapter; 8/8 required-named tests; 100% coverage on both new libs |
| Integration Tests (MG↔ASM, Sprint-005) | ✅ 5 tests passing |
| Integration Tests (MG→ASM→Preprocessing, Sprint-006) | ✅ 6 tests |
| Integration Tests (VAD pipeline, Sprint-007) | ✅ 10 tests |
| Integration Tests (GPU Scheduler concurrent, Sprint-008) | ✅ 6 tests |
| Integration Tests (migrations) | ✅ 11 static passing; 3 live skipped (no POSTGRES_DSN) |
| Integration Tests (DB connectivity) | ✅ 22 tests (skip if no live DB) |
| Unit + Integration Tests (Sprint-014 Repositories + Alembic migrations) | ✅ 66 tests — 56 unit (mocked-cursor, `tests/fixtures/fake_pg.py`) + 10 integration (real Postgres, incl. all 5 required-named tests); scratch-database upgrade→downgrade→upgrade verified |
| Unit + Integration Tests (Sprint-015 State/Idempotency/Recovery) | ✅ ~53 tests — `test_idempotency.py`(10) + `test_state_persistence.py`(11) + `test_crash_recovery.py`(16) + `test_conversation_session_state.py`(6) + `test_conversation_engine_recovery.py`(4) + `test_recovery_integration.py`(2, real Postgres/Redis); all 6 named required tests + both integration-required named tests pass |
| Unit + Integration Tests (Sprint-016 Concurrency/CircuitBreaker/Health/ServiceDiscovery/Observability) | ✅ ~100+ tests — `test_bounded_queue.py`/`test_circuit_breaker.py`/`test_worker_pool.py`/`test_load_shedder.py`/`test_backpressure.py`/`test_health.py`/`test_service_discovery.py`/`test_observability.py` + `test_circuit_breaker_integration.py` (incl. required `test_otel_trace_end_to_end`) + circuit-breaker/WorkerPool/tracer wiring tests added to STT/LLM/TTS/RedisClient/BaseRepository/ConversationEngine test files; all required named tests pass |
| Unit + Integration Tests (Sprint-017 Policy Engine) | ✅ 84 new tests — `test_policy_engine.py` (all 7 required-named tests + broad pack/engine/service/wiring coverage) + `test_policy_repository.py` + `test_policy_engine_integration.py` (both required-named integration tests + a p99<10ms latency test) + `PolicyLookupPort` additions to `test_dialogue_policy.py`; all pass locally (`FakeRedisClient`/real `EventBus`+`Publisher`) and on the CPU node against real Postgres/Redis |
| Unit + Integration Tests (Sprint-018 Auth/Authz/AI Governance) | ✅ ~61 new tests — `test_auth.py` (JWT valid/expired/invalid-signature/issuer-mismatch, API key, mTLS valid/expired/untrusted-CA, OIDC, AuthMiddleware via `TestClient`) + `test_authz.py` (RBAC AUDITOR/ADMIN required tests + ABAC/JIT/TenantIsolationGuard/AuthzService) + `test_ai_governance.py` (Law-of-Authority BLOCK/APPROVE + REQUIRE_HUMAN required tests + Prometheus counter assertions) + `test_auth_integration.py` (AI Governance gate blocking a hallucination end-to-end through `TrueStreamingPipeline`, JWT→AuthzService chain); all 8 required-named tests pass locally (in-process RSA keys/CA, `FakeRedisClient`) and on the CPU node against real Postgres/Redis/MongoDB + the real mTLS PKI |
| Unit + Integration Tests (Sprint-019 Secrets/Encryption/Privacy) | ✅ ~50 new tests — `test_secrets.py` (SecretsManager cache/rotation/revocation, no-env-var required test) + `test_encryption.py` (envelope encrypt/decrypt roundtrip + crypto-shred required tests, AES-GCM, TLSConfig) + `test_privacy.py` (DataMinimizer required test, PrivacyEngine, RetentionScheduler, DataErasureJob) + `test_check_secrets.py` (secrets-scan-catches-hardcoded required test) + `test_encryption_integration.py` (required `test_erasure_workflow_end_to_end`) + `TestEncryptionWiring` additions to `test_customer_repository.py`; all required-named tests pass locally (`FakeVaultClient`/`FakeKMSClient`/`FakeObjectStore`) and on the CPU node against real self-hosted Vault (KV+Transit)/Postgres/Redis/MongoDB |
| Unit Tests (Sprint-020 PII/Audit/AI Safety/Compliance Monitoring/Incident Response) | ✅ 55+ new tests — `test_pii.py` (phone/aadhaar/PAN/UPI detection, redaction, AUDITOR-gated tokenizer required tests) + `test_audit.py` (hash-chain-valid/hash-chain-tamper required tests against a stateful in-memory `AuditRepository` fake, all mandatory-coverage event types, no-raw-PII-in-payload) + `test_ai_safety.py` (content-moderator-blocks-abuse/prompt-injection-detected required tests + output validator + human oversight router) + `test_api_security.py` + `test_runtime_security.py` + `tests/unit/services/test_compliance_monitoring.py` (5-CONSENT_DENIED-events-alert required test) + `test_incident_response.py` (lifecycle + DPDP-timer required tests); 2 new PII-redaction tests added to `test_observability.py`; `test_audit_repository.py` extended with hash-chain assertions; all required-named tests pass locally and on the CPU node against real Postgres/Redis |
| Unit + Integration Tests (Sprint-021 Tenant/Org/User Management) | ✅ 60+ new tests — `test_tenant_management.py` (both required-named lifecycle tests + `IsolationProfileManager`/`TenantProvisioner`/`TenantSuspender`/`TenantDeleter`/`TenantService` coverage) + `test_org_management.py` (both required-named `OrgHierarchy.resolve_scope` tests) + `test_user_management.py` (invitation workflow + SSO stub) + `tests/integration/services/test_tenant_isolation.py` (both required-named integration tests, real Postgres); all required-named tests pass locally (in-memory fakes) and on the CPU node against real Postgres/Redis/Vault |
| Unit + Integration Tests (Sprint-022 CRM/Collections) | ✅ 38 new tests — `test_crm.py` (CustomerService/PartyService/CustomerImporter + `test_customer_context_immutable` required test) + `test_collections.py` (LoanAccountService/EMIScheduleService `test_dpd_calculation` required test + SettlementService offer→accept→authorize→disburse + CallbackScheduler + EscalationWorkflow) + `test_ptp_idempotency.py` (`test_ptp_idempotency_key_deterministic` required test + validation/policy checks) + `tests/integration/services/test_customer_context_assembly.py` (`test_customer_context_assembly`/`test_ptp_create_idempotent`/`test_ptp_policy_check_called` required-named integration tests, real Postgres, 10-concurrent-connection PTP idempotency); all required-named tests pass locally (in-memory fakes) and on the CPU node against real Postgres |
| Unit + Integration Tests (Sprint-023 Campaign Management/Contact Center/HITL) | ✅ 72 new tests — `test_campaign_management.py` (`CampaignLifecycle`, `ScheduleEngine` `test_schedule_engine_rbi_frequency_limit`/`test_schedule_engine_rbi_calling_hours` required tests, `ABTestingFramework` `test_ab_variant_deterministic` required test, `AudienceSelector`, `CallDispatcher`, `CampaignService`) + `test_contact_center.py` (`SkillsBasedRouter`, `LiveTransferService` `test_live_transfer_context_includes_transcript` required test, `SupervisorService` monitor-read-only/barge-in-mutes-AI) + `test_hitl.py` (`HITLQueue` `test_hitl_queue_enqueue_and_dequeue` required test, `SLAEnforcer` `test_hitl_sla_enforcer_breach` required test, `OverrideLogger` `test_override_logger_audit_event` required test, `HumanReviewAPI` `test_human_review_requires_rationale` required test via `TestClient`, `HITLDashboard`) + `tests/integration/services/test_rbi_scheduling.py` (`test_rbi_scheduling_compliance_suite` required test — 20 real-Policy-Engine scenarios, 100% pass) + `test_hitl_sla.py` (`test_hitl_sla_integration` required test, real Postgres); all required-named tests pass locally (in-memory fakes/`FakePolicyEngineService`/`FakeAudioBridge`) and on the CPU node against real Postgres |
| Unit + Integration Tests (Sprint-024 Billing/Metering/Analytics/Reporting/BI Platform) | ✅ ~85 new tests — `test_billing.py` (`test_invoice_calculation`/`test_entitlement_blocks_on_limit` required tests + SubscriptionManager/PaymentProcessor/BillingService coverage) + `test_metering.py` (`test_usage_event_idempotent` required test + UsageCollector/UsageAggregator/UsageLimitEnforcer/MeteringService) + `test_analytics.py` (`test_campaign_ptp_rate_calculation` required test + CallAnalytics/DailyAggregationJob/RealtimeAnalytics/AnalyticsService) + `test_bi_platform.py` (BIWarehouse/ForecastingEngine/CrossTenantBenchmarking/ExecutiveDashboard/BIPlatformService) + `test_reporting.py` (ExportService CSV/XLSX/PDF, ReportScheduler, templates) + new `test_billing_repository.py`/`test_call_disposition_repository.py`/`test_analytics_repository.py`/`test_bi_repository.py` (SQL-generation unit tests) + `test_policy_engine.py` gained `TestBillingPolicyPack` + `check_entitlement` tests + `tests/integration/services/test_metering_integration.py` (`test_metering_event_consumption`/`test_usage_limit_enforcer` required tests, real Postgres/Redis) + `test_bi_warehouse.py` (`BIWarehouse.refresh()` required test, real Postgres); all required-named tests pass locally (in-memory fakes/`FakeRedisClient`/real in-process `PolicyEngine`) and on the CPU node against real Postgres/Redis |
| Total | ✅ 1862 passed, 0 failed locally; coverage well above 85% — **1927 passed, 7 skipped on CPU node** (real Postgres/Redis/Vault/MongoDB; no Sprint-024 regressions — the 7 skips are the pre-existing MongoDB-credential gap [6] + unrelated Devanagari-pipeline deferral [1]); coverage 92.20% |
| GPU Inference (Sprint-009 Phase 2) | ✅ STT/LLM/TTS deployed & validated on L4 node — health checks pass, VRAM 20,359/23,034 MB, LLM smoke test OK |
| Real AI Inference (Sprint-012 Phase 2) | ✅ 20/20 walking skeleton calls — CIL → vLLM Qwen2.5-7B → Veena TTS → PlaybackScheduler; LLM TTFT 200ms ✓; TTS RTF 2.4× [TT-001] |
| TTS Streaming Inference (Sprint-012 Phase 3) | ✅ ADR-001 deployed to GPU node — vLLM AsyncLLMEngine + SNAC sliding-window decode + StreamingResponse; server TTFA p95=873ms ✅; Transfer-Encoding: chunked ✓; true streaming ✓; pipeline p50=2355ms p95=6048ms |
| EventBus Infrastructure (Sprint-013 Phase 2) | ✅ Deployed to CPU node against real Redis 6.0.16 — publish→consume 0.39ms ✅ (target <50ms); dedup, DLQ routing, DistributedLock fencing, RateLimiter, TTLGuard all confirmed via `scripts/sprint013_infra_validation.py` |
| Persistent Storage Infrastructure (Sprint-014 Phase 2) | ✅ `alembic upgrade head` applied to production `voiceos` Postgres — 30 tables, head 0013, zero errors against already-populated DB; MongoDB indexes 0→22 across 4 collections; `audit_log` immutability trigger verified live (direct SQL UPDATE/DELETE both rejected) |
| State Persistence / Crash Recovery / Idempotency (Sprint-015 Phase 2) | ✅ `alembic upgrade head` applied to production `voiceos` Postgres — 32 tables, head 0014 (FK-constraint bug found and fixed same session, see CHANGELOG.md); concurrent idempotency (`scripts/validate/idempotency_test.py --concurrent 10`) → effect_fn executed exactly once; full crash-recovery drill (`scripts/sprint015_recovery_drill.py`) → OVERALL PASS (snapshot/crash/restart/replay/4th-turn/fencing all green); CPU↔GPU regression confirmed unaffected |
| Concurrency, Circuit Breakers, Health & Observability (Sprint-016 Phase 2) | ✅ `scripts/sprint016_infra_validation.py` → 8/8 PASS on CPU node (real Redis + Postgres): `CircuitBreaker` opens on real sustained Postgres failure, fails fast <1ms while OPEN, closes again on real recovery; `RedisHealthCheck`/`HealthAggregator`/`ReadinessProbe` HEALTHY against real Redis+Postgres; `BoundedQueue`/`StructuredLogger`/`OTelTracer` all validated. `healthcheck.sh`'s new circuit-breaker-state check OK (2 pre-existing script bugs found and fixed — missing `POSTGRES_DSN` for Alembic, stale `"0013"` head check — see CHANGELOG.md). No standalone HTTP server exists yet for any service, so `/health/*` and `/metrics` remain unbound (tracked TT-006, not blocking). |
| Authentication, Authorization/RBAC & AI Governance (Sprint-018 Phase 2) | ✅ `scripts/sprint018_infra_validation.py` → 11/11 PASS on CPU node (real Redis + self-managed mTLS PKI): JWT valid/invalid signature, no-credentials rejection, RBAC AUDITOR+DELETE→DENY, tenant-isolation cross-tenant→`TenantIsolationViolationError`, mTLS real-CA-signed cert→AuthContext, mTLS non-CA-signed cert rejected, AI Governance hallucinated-fact→BLOCK (+ `law_of_authority_violations` counter increment), AI Governance grounded-fact→APPROVE, REQUIRE_HUMAN→supervisor queue event confirmed via real `EventBus.replay_from()`. `healthcheck.sh`'s new Auth/Authz/AI Governance section OK (mTLS PKI CA+5 leaf certs present, JWT/RBAC/AIGovernanceService smoke test, violation counter=0 at steady state). |
| Encryption / Privacy Infrastructure (Sprint-019 Phase 2) | ✅ `scripts/sprint019_infra_validation.py` → 9/9 PASS on CPU node (real self-hosted Vault KV+Transit, Postgres, Redis, MongoDB, local disk): SecretsManager fetches real Vault KV secret; EnvelopeEncryption round-trips through real Vault Transit; CryptoShredder→`KeyNotFoundError`; `CustomerRepository` writes verified ciphertext to `customers.name_encrypted` + transparently decrypts name/phone/address; Redis/MongoDB both reject unauthenticated access and accept real Vault-stored credentials; `DataMinimizer` strips unconsented fields; full `DataErasureJob` run persists a real `DataErasureCertificate`, deletes the audio file, leaves the DEK unrecoverable. `healthcheck.sh`'s new Vault/Encryption/Privacy section OK. |
| PII / Audit / AI Safety / Compliance Monitoring / Incident Response Infrastructure (Sprint-020 Phase 2) | ✅ `scripts/sprint020_infra_validation.py` → 12/12 PASS on CPU node (real Postgres 14 + Redis 6.0.16): `StructuredLogger` redacts PII via real logger; 100-event real hash chain valid (`AuditVerifier.verify_chain()`); `audit_log` immutability trigger rejects direct-SQL UPDATE (defense-in-depth); `PIITokenizer` round-trips through real Redis (AUDITOR-gated); `ContentModerator`/`PromptInjectionDetector` both correct; 5 CONSENT_DENIED events → `ComplianceViolationAlert` emitted + `status()` flips to VIOLATION; DATA_BREACH incident → 72h `notification_deadline_at` set + full lifecycle reaches CLOSED; `SecurityHeaders` includes HSTS/CSP; `APIRateLimiter` enforces its limit against real Redis. `scripts/validate/audit_chain.py`/`consent_gate.py` both PASS. `healthcheck.sh`'s new PII/Audit/AI Safety/Compliance Monitoring/Incident Response section OK (2 pre-existing bugs found and fixed — stale migration-head assertion, `REDIS_URL`-clobbering bug — see TT-007). |
| Tenant / Org / User Management Infrastructure (Sprint-021 Phase 2) | ✅ `scripts/sprint021_infra_validation.py` → 7/7 PASS on CPU node (real Postgres 14 + Redis 6.0.16 + self-hosted Vault Transit): tenant lifecycle TRIAL→SANDBOX→PRODUCTION; invalid transition raises; `TenantProvisioner` creates a real KEK in Vault Transit; `TenantProvisioned` event delivered via real EventBus; cross-tenant isolation returns 0 results; `OrgHierarchy.resolve_scope()` BU-sees-branch/branch-cannot-see-sibling both correct; `TenantSuspender` blocks new call admission. `healthcheck.sh`'s new Tenant/Org/User Management section OK (2 real-infra-only bugs found and fixed — `RoleAssignment.assigned_by` UUID-column mismatch, `RedisClient`-vs-raw-client mismatch in the validation script). |
| CRM / Collections Infrastructure (Sprint-022 Phase 2) | ✅ `scripts/sprint022_infra_validation.py` → 8/8 PASS on CPU node (real Postgres 14.23): `CustomerContextAssembler.assemble()` correct outstanding balance + DPD=30 from real EMI schedule data; `CustomerContext` immutability (mutation raises `ValidationError`); RI-5 blocks an unauthorized source and permits `crm_collections`; PTP creation via 10 concurrent Postgres connections with identical `call_id`+`loan_account_id`+`promise_date` → exactly 1 row; PTP-creation audit event persisted; Settlement `offer→accept→authorize→disburse` transitions correctly; context-assembly latency 0.60ms (well under both the 50ms architecture target and the 100ms AC bar). `healthcheck.sh`'s new CRM/Collections section OK (2 real-infra-only bugs found and fixed — stale hardcoded migration-head test assertion, validation script's `audit_log` cleanup correctly rejected by the immutability trigger). |
| Campaign Management / Contact Center / HITL Infrastructure (Sprint-023 Phase 2) | ✅ `scripts/sprint023_infra_validation.py` → 10/10 PASS on CPU node (real Postgres 14.23): campaign lifecycle `DRAFT→REVIEW→APPROVED→ACTIVE` via real Postgres, `DRAFT→ACTIVE` (skip `APPROVED`) raises `CampaignLifecycleError`; RBI 21:00 request and 3-calls-today both correctly return `None` from the real `PolicyEngine`+`RBIPolicyPack`; 100 A/B dispatches split 45/55; HITL enqueue→list→supervisor-approve→resolve round-trips through real Postgres (durability confirmed via a fresh repository instance); a REQUIRE_HUMAN verdict from a real `AIGovernanceService` (with `HITLQueue` substituted as its `human_oversight_router`) actually reaches the durable queue and survives a fresh instance; HITL SLA breach detection fires correctly with a mocked "now"; live-transfer `AgentScreenContext` includes transcript+AI summary; supervisor `monitor()` read-only while `barge_in()` mutes AI (both via `FakeAudioBridge`). `healthcheck.sh`'s new Campaign Management/Contact Center/HITL section OK; new DR scripts `scripts/validate/rbi_scheduling.py`/`hitl_queue.py` both PASS. 2 real-infra-only bugs found and fixed — `hitl_queue.call_id` UUID→TEXT retype, stale hardcoded migration-head test assertion. |
| Billing / Metering / Analytics / Reporting / BI Platform Infrastructure (Sprint-024 Phase 2) | ✅ `scripts/sprint024_infra_validation.py` → 11/11 PASS on CPU node (real Postgres 14.23 + Redis 6.0.16): GROWTH-tier subscription created; `saas.call.dispositioned` event → real EventBus/Consumer pipeline → 1 `usage_event` in Postgres; duplicate `event_id` → 1 record (idempotent); GROWTH call-minute limit exceeded → `UsageLimitEnforcer` returns `False` (429-equivalent); generated invoice persists with line items matching the usage aggregate; 10-call/3-PTP campaign → `ptp_rate == 0.30`; `DailyAggregationJob` → row in `analytics_daily`; `BIWarehouse.refresh()` → populated `bi_facts.fact_daily`; non-zero `ForecastingEngine` result; anonymized `CrossTenantBenchmarking` percentile with no other tenant IDs exposed; `ExecutiveDashboard` returns all required KPI fields. `scripts/validate/usage_metering.py`/`usage_limit.py` (both directions) PASS. `healthcheck.sh`'s new Billing/Metering/Analytics/Reporting/BI Platform section OK (4/4). 2 real-infra-only bugs found and fixed — non-UUID `tenant_id` FK violations in new integration tests/validation script (fixed via a real-tenant-creating fixture), `UsageLimitEnforcer`'s Redis Protocol return-type mismatch (retyped to `Any`). No leftover validation test data (0 rows confirmed across `usage_events`/`analytics_daily`/`bi_facts.dim_tenant`). |
| Regression Tests | ✅ Started Sprint-013 — walking skeleton e2e regression run against every Phase 2 deployment (Sprint-024: 1927/1934 passed [7 pre-existing skips — MongoDB-credential gap + Devanagari deferral] — full suite against real Postgres/Redis/Vault/MongoDB) |
| Performance Tests | Not Started |
| Founder Validation | Not Started (planned Sprint-029) |
| Pilot Validation | Not Started (planned Sprint-030) |

---

## Production Readiness

| Area | Status |
|---|---|
| Infrastructure | 🟡 Event Bus + Redis reliability layer live (Sprint-013); persistent storage/migrations live (Sprint-014); state persistence/crash recovery/idempotency live (Sprint-015); circuit breakers/health/observability libraries live (Sprint-016); complete Terraform/Helm/K8s IaC tree written and Phase-1-validated (Sprint-026) — real deployment blocked pending non-multi-tenant-restricted compute, see TT-014 |
| Runtime | ⬜ |
| Intelligence | ⬜ |
| Reliability | ✅ 4/4 sprints (E4) — Sprint-013 Event Bus/locks/rate-limiting + Sprint-014 persistent storage/repositories + Sprint-015 state persistence/crash recovery/idempotency + Sprint-016 concurrency/circuit breakers/health/observability all done. Milestone M-4 reached. |
| Compliance | ✅ Policy Engine (PDP) + RBI/DPDP/AI-governance/conversational policy packs implemented and deployed/validated against real Postgres/Redis on the CPU node (Sprint-017 ✅); Authentication (JWT/OIDC/mTLS/API-key), RBAC+ABAC authorization, and the mandatory AI Governance gate implemented and validated (Sprint-018 ✅); Secrets Management/Encryption/Privacy Architecture (SecretsManager/EnvelopeEncryption/PrivacyEngine, PII field-level encryption) implemented and deployed/validated against real self-hosted Vault + Postgres on the CPU node (Sprint-019 ✅); PII detection/redaction/tokenization, hash-chained immutable audit trail, ComplianceMonitoring (real-time signal correlation + alerting), and IncidentResponse (DPDP 72h breach-notification timer) implemented and validated against real Postgres/Redis on the CPU node (Sprint-020 ✅). **Milestone M-5 Compliance & Security Complete reached.** |
| Security | ✅ AuthService/AuthzService/mTLS PKI live and validated against real infrastructure (Sprint-018 ✅); self-hosted Vault (KV+Transit) secrets management, envelope encryption with crypto-shredding, Redis/MongoDB auth all live and validated against real infrastructure (Sprint-019 ✅); PII protection (detection/redaction/tokenization), tamper-evident audit hash chain, API security (rate limiting/input validation/security headers/CORS), runtime security policy definitions, and AI safety (content moderation/prompt-injection detection/human oversight) all implemented and validated against real infrastructure (Sprint-020 ✅) |
| Observability | ⬜ |
| SaaS Platform | 🟡 Multi-tenancy foundation live — tenant lifecycle state machine, isolation profiles (SHARED/DEDICATED_SCHEMA/DEDICATED_CLUSTER), tenant provisioning (KEK/Redis-namespace/default-admin), org hierarchy + RBAC scope resolution, and token-based user invitation all implemented and validated against real Postgres/Redis/Vault on the CPU node (Sprint-021 ✅). CRM (CustomerService/CustomerContextAssembler) and Collections (LoanAccountService/PromiseToPayService/SettlementService/CallbackScheduler/EscalationWorkflow) implemented and validated against real Postgres on the CPU node (Sprint-022 ✅) — `ConversationEngine` now assembles the authoritative `CustomerContext` once per call (RI-5). Campaign Management (RBI-compliant `ScheduleEngine`, `AudienceSelector`, `ABTestingFramework`) and Contact Center/HITL (`LiveTransferService`, `SupervisorService`, durable `HITLQueue`+`SLAEnforcer`) implemented and validated against real Postgres on the CPU node (Sprint-023 ✅) — campaigns are now the authoritative dispatcher hook for outbound AI calls (`ConversationEngine.escalate_call()`/`campaign_id_for_call()`), and every REQUIRE_HUMAN verdict can be durably queued (`HITLQueue` satisfies `AIGovernanceService`'s `human_oversight_router` port). Billing (`BillingService`/`SubscriptionManager`/`EntitlementEngine`/`InvoiceGenerator`), Usage Metering (`MeteringService`/`UsageCollector`/`UsageLimitEnforcer`), Analytics (`AnalyticsService`/`CallAnalytics`/`CampaignAnalytics`/`DailyAggregationJob`), Reporting (`ReportingService`/`ExportService`), and Business Intelligence (`BIPlatformService`/`BIWarehouse`/`ForecastingEngine`/`CrossTenantBenchmarking`/`ExecutiveDashboard`) implemented and validated against real Postgres/Redis on the CPU node (Sprint-024 ✅) — every billable feature access now routes through `PolicyEngineService.check_entitlement()` (`BillingPolicyPack`, the sixth built-in pack). Admin Portal (Sprint-025) still pending — **Milestone M-6 SaaS Platform Complete** requires it. |
| Operations | ⬜ |
| Production Deployment | ⬜ |

---

## Known Issues

- **TT-001 — Veena TTS Serving Layer (Batch→Streaming) — RESOLVED (server-level) in Sprint-012 Phase 3 (2026-07-04):** Phase 2 first-audio p95=8,730ms. Root cause (reclassified 2026-07-03): **implementation limitation, not a model limitation.** HF `model.generate()` + buffered `Response` blocked streaming. Fix deployed: `_SNACTokenStreamer` + 28-token sliding-window SNAC decode + `FastAPI StreamingResponse` + pipeline `VeenaAdapter`. TTS server-level TTFA p95=**873ms** (target 1,500ms ✅). Walking skeleton pipeline p95=6,048ms (pipeline-limited: LLM+VeenaAdapter sequential synthesis). Veena AI FP16 retained. ADR-001 approved. **Residual gap:** pipeline p95 exceeds 1,500ms due to VeenaAdapter sequential clause synthesis — concurrent LLM+TTS task execution needed (TT-001-residual, BACKLOG.md).

---

## Technical Debt

- `requires-python = ">=3.11"` in pyproject.toml: architecture specifies Python ≥3.12 but only 3.11.9 is installed on the dev machine. No 3.12-only APIs used. Update when 3.12 is installed.
- **TT-002 (Sprint-013) — RESOLVED 2026-07-04 (dedicated hardening task):** Root cause proven via live CPU-node investigation: the running Redis process had been started bypassing `/etc/redis/redis.conf` entirely (bare `redis-server 127.0.0.1:6379`, no config file), so `appendonly no`/`noeviction` were compiled-in defaults; combined with no long-running VoiceOS consumer process to self-heal via the already-idempotent `ensure_consumer_group()`, recovery depended entirely on manual intervention. **Fixed:** `/etc/redis/redis.conf` hardened (`appendonly yes`, `appendfsync everysec`, `maxmemory-policy volatile-ttl`) and Redis restarted via the proper init script; new `scripts/eventbus_recovery.py` provides automatic, idempotent, race-safe recovery, wired into `restore.sh`/`healthcheck.sh`/`bootstrap.sh`. **Validated:** publish→consume→graceful-restart round trip proved 0 data loss (persistence alone, no self-heal even needed); Postgres/Mongo restarts also clean; full regression 1304 passed/1 skipped after all infra restarts. See BACKLOG.md TT-002 row and CPU_NODE_STATE.md §7.1/§18 for full detail.
- **TT-003 (Sprint-013) — RESOLVED 2026-07-04:** Pre-existing documentation gaps (Sprint-012 missing from CHANGELOG.md; CPU_NODE_STATE.md/GPU_NODE_STATE.md lagging behind the sprints that actually touched those nodes; ROADMAP.md status line stale; BACKLOG.md summary counts stale) were fixed in a dedicated documentation-cleanup pass. See CHANGELOG.md and BACKLOG.md for the full list of corrections.
- **TT-005 (Sprint-015) — OPEN:** `deployment/cpu/healthcheck.sh`/`restore.sh` default `REDIS_HOST`/`POSTGRES_HOST` to Docker/K8s-Service-style hostnames (matching `.env.example`'s Sprint-026-era intended values), which don't resolve on this bare-metal-process CPU node — a bare `bash healthcheck.sh` silently reports Redis/Postgres as down even when healthy. Discovered running the script standalone during Sprint-015 Phase 2. Workaround: export `REDIS_HOST=localhost POSTGRES_HOST=localhost` before invoking. Not blocking; natural fit for Sprint-026 IaC work. See BACKLOG.md TT-005.
- **TT-007 (Sprint-020) — RESOLVED same session:** `healthcheck.sh` line 60 unconditionally rebuilt `REDIS_URL` with no password and no `${REDIS_URL:-...}` fallback guard, clobbering any already-exported, auth-bearing value — harmless before Sprint-019's Redis-auth rollout, a live failure afterward (EventBus self-heal, Policy Engine cache-warm both hit `AuthenticationError`). Separately, a real Redis password briefly appeared in `scripts/validate/consent_gate.py`'s own stdout during this sprint's Phase 2 validation, and the same unsafe `print(f"...{REDIS_URL}...")` pattern was found in 5 pre-existing scripts. **Fixed:** `healthcheck.sh` now respects a pre-set `REDIS_URL` and embeds `REDIS_PASSWORD` in its fallback; all 6 scripts' prints redacted. See BACKLOG.md TT-007.
- **TT-008 (Sprint-020) — OPEN:** MongoDB integration tests (4) and `healthcheck.sh`'s MongoDB-auth/index checks fail on the CPU node — not a Sprint-020 regression, this session had Postgres/Redis credentials but not MongoDB's (auth has required `--auth` since Sprint-019). Not blocking Sprint-020's scope. See BACKLOG.md TT-008.
- **TT-009 (post-Sprint-020 audit) — RESOLVED same session:** `deployment/cpu/restore.sh` required a nonexistent Kubernetes/Helm deployment; `deployment/gpu/restore.sh` referenced a nonexistent `validate_latency.py`. Both fixed. See BACKLOG.md TT-009.
- **TT-010 (post-Sprint-020 audit) — OPEN:** TTS `/synthesize` time-to-first-chunk measured 914–2974ms on the GPU node, above the documented 873ms Sprint-012 Phase 3 p95, GPU otherwise idle. Not root-caused. See BACKLOG.md TT-010.
- **TT-011 (Sprint-021) — OPEN, non-blocking:** One orphaned Vault Transit KEK left behind by `scripts/sprint021_infra_validation.py` (its destruction is a separate irreversible secret-deletion action outside this session's standing authorization). Harmless — no real customer data was ever encrypted with it. See BACKLOG.md TT-011.
- **TT-012 (Sprint-022) — RESOLVED same session:** `tests/integration/repositories/test_migration_upgrade_downgrade.py` hardcoded the expected Alembic head as `"0018"` (same recurring stale-head class as Sprint-015/016/017/020) — bumped to `"0019"`. Separately, `scripts/sprint022_infra_validation.py`'s first draft tried to `DELETE FROM audit_log` during cleanup, correctly rejected by the Sprint-020 immutability trigger (`audit_log is append-only`) — fixed to skip audit-row cleanup, same as every other validation script that writes audit events. See CHANGELOG.md.
- **TT-013 (Sprint-023) — RESOLVED same session (migration bug); OPEN, non-blocking (test residue):** `hitl_queue.call_id` was originally typed `UUID` — `ScheduleEngine` and other pre-dial admission checks build synthetic, non-UUID call identifiers before a real call session exists, caught by the first real-Postgres integration test run and fixed by retyping to `TEXT`. Also: `tests/integration/repositories/test_migration_upgrade_downgrade.py` hardcoded the expected Alembic head as `"0019"` — bumped to `"0020"`. Separately, ~16 `hitl_queue` rows were left behind by this session's own `test_hitl_sla.py`/`sprint023_infra_validation.py` runs against the real CPU node Postgres — harmless (no real HITL usage exists yet), a broad cleanup `DELETE` was correctly declined by the auto-mode safety classifier for lacking a narrow tenant-scoped predicate on shared infrastructure. See CHANGELOG.md and BACKLOG.md TT-013.

---

## Blockers

**Current Blockers:** None.

---

## Risks

| Risk | Prob | Impact | Mitigation Sprint |
|---|---|---|---|
| R-1 | GPU concurrency / cost-per-call exceeds budget | High | High | Sprint-008, Sprint-028 |
| R-2 | First-audio latency > 1.5s under load | Med | High | Sprint-012, Sprint-028 |
| R-3 | RBI/DPDP compliance gap discovered post-launch | Med | Critical | Sprint-017, Sprint-028 |
| R-4 | Tenant isolation breach | Low | Critical | Sprint-020, Sprint-021 (validated against real Postgres — `scripts/sprint021_infra_validation.py`), Sprint-028 |
| R-5 | Scope overrun for lean team | Med | Med | Walking-skeleton-first (Sprint-012) |
| R-6 | LLM hallucination violating Law of Authority | Med | Critical | Sprint-012, Sprint-018, Sprint-029 |

---

## Architecture Decision Records

**Latest ADR:** ADR-001 — Replace HF Batch TTS Inference with vLLM Streaming (2026-07-03). See `implementation/adrs/ADR-001-vllm-tts-streaming.md`.

---

## Performance Snapshot

| Metric | Latest | Target |
|---|---|---|
| End-to-End first-audio | p50=2,355ms p95=6,048ms (Sprint-012 Phase 3, pipeline-limited) | p95 ≤ 1.5 s |
| STT Latency | N/A (bypassed in validation) | within budget line |
| LLM TTFT | ~200-300 ms ✓ | ≤ 350 ms |
| LLM Throughput | ~40 tok/s (vLLM) | TBD |
| TTS Server TTFA | p95=873ms ✅ (Sprint-012 Phase 3 streaming, ADR-001) | ≤ 1,500 ms |
| TTS Latency (per clause) | RTF 2.4× batch superseded by streaming — 85.33ms/chunk chunked delivery | ≤ 250 ms per clause |
| GPU Utilization | N/A | ~0.80 target |
| CPU Utilization | N/A | — |
| Memory Usage | N/A | bounded (RI-3) |
| Error Rate | N/A | within SLO |
| EventBus publish→consume (Sprint-013) | 0.39ms ✅ (real Redis, CPU node) | append <5ms p99, dispatch <50ms p99 (V3 Ch3 §3.14) |
| DistributedLock acquire (Sprint-013) | sub-ms (SET NX + INCR, same node) | <2ms p99 (V3 Ch4 §4.14) |
| Postgres simple SELECT (Sprint-014, `customers`) | 0.649ms ✅ (CPU node, same-host) | < 20ms |
| Crash recovery (Sprint-015, `CPURestartStrategy`) | Well within SLA (drill measured completion in low single-digit seconds against real Postgres+Redis) | < 10s (V3 Ch7) |
| Concurrent idempotency (Sprint-015, 10 threads/connections) | 1 effect execution, 1 Postgres record ✅ | exactly 1 |
| Policy evaluation latency (Sprint-017, cached rules) | p99 = 0.066–0.150ms on the CPU node against real Redis + Postgres (`scripts/sprint017_infra_validation.py`) ✅ | p99 < 10ms cached (V4 Ch4 §4.14) |
| JWT validation latency (Sprint-018, RS256, cached key verification) | p99 = 0.030ms on the CPU node (500-iteration sample) ✅ | p99 < 5ms (V4 Ch5 §5.9) |
| Envelope encryption round trip (Sprint-019, real Vault Transit) | Functionally validated (encrypt→decrypt→crypto-shred all correct against real Vault) — `EncryptionService`'s `voiceos_encryption_latency_ms` Prometheus histogram instruments every call, but no formal p50/p99 benchmark was run this sprint | < 1ms typical (AES-NI, excl. KMS round trip; V4 Ch8 §8.14) |
| SecretsManager fetch (Sprint-019, real Vault KV, cached) | Functionally validated; no formal latency benchmark run this sprint | < 10ms cached (V4 Ch7 §7.14) |
| PII detection/redaction (Sprint-020, regex-based, real StructuredLogger) | Functionally validated (redaction confirmed on every log path); no formal latency benchmark run this sprint | < 20ms detect / < 1ms redact (V4 Ch10 §10.14) |
| Audit hash chain append (Sprint-020, real Postgres, `SELECT...FOR UPDATE` + SHA-256) | Functionally validated (100-event chain verified clean on the CPU node); no formal latency benchmark run this sprint | < 5ms enqueue (V4 Ch11 §11.14) |

---

## Upcoming Milestones

| # | Milestone | Reached at | Status |
|---|---|---|---|
| M-0 | Implementation Roadmap Generated | Planning Session 2026-06-29 | ✅ **Complete** |
| M-1 | Foundation Complete | end Sprint-003 | ✅ **Complete** (2026-06-30) |
| M-2 | Core Runtime Complete | end Sprint-008 | ✅ **Complete** (2026-06-30) |
| M-3 | Walking Skeleton (First Full Call) | end Sprint-012 | ✅ **Reached** (2026-07-03) — pipeline proven; Phase 3 streaming fix (2026-07-04): TTS server TTFA p95=873ms ✅; pipeline p50=2355ms p95=6048ms (sequential synthesis — TT-001-residual) |
| M-4 | Reliability Complete | end Sprint-016 | ✅ **Reached** (2026-07-04) — Event Bus/EventBus (Sprint-013), persistent storage (Sprint-014), state persistence/crash recovery/idempotency (Sprint-015), and concurrency/circuit breakers/health/observability (Sprint-016) all validated against real Postgres/Redis on the CPU node |
| M-5 | Compliance & Security Complete | end Sprint-020 | ✅ **Reached** (2026-07-05) — PII protection, hash-chained audit trail, API/runtime security, AI safety, ComplianceMonitoring, IncidentResponse all validated against real Postgres/Redis on the CPU node |
| M-6 | SaaS Platform Complete | end Sprint-025 | ⬜ Pending |
| M-7 | Production Alpha | end Sprint-028 | ⬜ Pending |
| M-8 | Founder Validation | end Sprint-029 | ⬜ Pending |
| M-9 | Pilot Deployment | end Sprint-030 | ⬜ Pending |
| M-10 | Production Release | end Sprint-031 (after M-9+M-11+M-12+M-13) | ⬜ Pending |
| M-11 | Enterprise Platform Complete | end Sprint-032 | ⬜ Pending |
| M-12 | Workflow & Customer Success Complete | end Sprint-033 | ⬜ Pending |
| M-13 | Learning Layer Complete | end Sprint-034 | ⬜ Pending |

---

## Session Rules

At the beginning of every Claude Code session:
1. Read this file (`PROJECT_STATUS.md`).
2. Read `CLAUDE.md`.
3. Read `implementation/CURRENT_SPRINT.md`.
4. Read any architecture chapters referenced by the current sprint.
5. Confirm understanding before writing code.

---

## End-of-Session Checklist

Before ending every implementation session, update: ✓ PROJECT_STATUS.md · ✓ CURRENT_SPRINT.md · ✓ BACKLOG.md · ✓ DONE.md · ✓ CHANGELOG.md · ✓ Sprint documentation · ✓ Tests (if modified) · ✓ API documentation (if modified).
