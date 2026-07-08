# VoiceOS v2 — Project Milestones

**Generated:** 2026-06-29 · **Updated:** 2026-06-29 (3 milestones added for Sprints 032/033/034)
**Total Milestones:** 13
**Completed:** 1 (planning complete)
**Pending:** 12  

---

## Milestone Overview

| # | Name | Sprint | Status |
|---|---|---|---|
| M-0 | Implementation Roadmap Generated | Planning Session | ✅ Complete |
| M-1 | Foundation Complete | End Sprint-003 | ⬜ Pending |
| M-2 | Core Runtime Complete | End Sprint-008 | ⬜ Pending |
| M-3 | Walking Skeleton (First Full Call) | End Sprint-012 | ⬜ Pending |
| M-4 | Reliability Complete | End Sprint-016 | ✅ **Reached** (2026-07-04) |
| M-5 | Compliance & Security Complete | End Sprint-020 | ⬜ Pending |
| M-6 | SaaS Platform Complete | End Sprint-025 | ✅ **Reached** (2026-07-06) |
| M-7 | Production Alpha | End Sprint-028 | ⬜ Pending |
| M-8 | Founder Validation | End Sprint-029 | ⬜ Pending |
| M-9 | Pilot Deployment | End Sprint-030 | ⬜ Pending |
| M-11 | Enterprise Platform Complete | End Sprint-032 | ⬜ Pending |
| M-12 | Workflow & Customer Success Complete | End Sprint-033 | ⬜ Pending |
| M-13 | Conversation Learning Layer Complete | End Sprint-034 | ⬜ Pending |
| M-10 | Production Release | End Sprint-031 (after M-9+M-11+M-12+M-13) | ⬜ Pending |

---

## M-0 — Implementation Roadmap Generated

**Status:** ✅ Complete  
**Date:** 2026-06-29

**What it means:** All seven architecture volumes and twelve documentation files were read. A dependency-ordered 31-sprint implementation plan was generated and recorded in `implementation/`. `PROJECT_STATUS.md` was updated to reflect planning complete.

**Acceptance Criteria:**
- [x] All 7 architecture volumes read and understood
- [x] All 12 documentation suite files confirmed
- [x] 31 sprint files created (Sprint-001 through Sprint-031)
- [x] ROADMAP.md, EPICS.md, BACKLOG.md, DONE.md, CHANGELOG.md, BUILD_ORDER.md, DEPENDENCY_GRAPH.md, SPRINT_GRAPH.md, MILESTONES.md, CURRENT_SPRINT.md all created
- [x] PROJECT_STATUS.md updated: planning complete, implementation not started

---

## M-1 — Foundation Complete

**Status:** ⬜ Pending  
**Target:** End of Sprint-003

**What it means:** The engineering foundation is in place. Every subsequent sprint can build on a stable, type-safe, tested base. The CI pipeline enforces architecture rules automatically.

**Acceptance Criteria:**
- [ ] `src/libs/contracts/` fully typed, all exported symbols pass `mypy --strict`
- [ ] `src/libs/invariants/` implements RI-1 through RI-8 as callable assertions; each has ≥1 test (passing + failing case)
- [ ] All Postgres schemas committed as Alembic migrations; MongoDB collection schemas documented
- [ ] CI pipeline: ruff → mypy --strict → pytest with coverage gates (≥90% core, 100% invariants) runs green end-to-end
- [ ] Docker Compose `up` starts all services cleanly in local dev
- [ ] Module boundary enforcement: cross-package imports outside defined interfaces fail in CI

---

## M-2 — Core Runtime Complete

**Status:** ⬜ Pending  
**Target:** End of Sprint-008

**What it means:** The real-time media plane is fully operational. Audio can flow from telephony ingress through all preprocessing stages to the point where it is ready for STT. The GPU Scheduler is in place and enforces OOM-by-construction.

**Acceptance Criteria:**
- [ ] Audio flows: fake Twilio WebSocket → session manager → preprocessed frames → VAD-segmented frames
- [ ] Barge-in signal propagates correctly to downstream playback flush
- [ ] AEC3 suppresses echo (measured by ERLE ≥ 20dB on test fixture)
- [ ] Silero VAD detects speech/silence accurately (≥95% precision/recall on test set)
- [ ] GPU Scheduler rejects requests exceeding VRAM limit (zero OOM errors in any test)
- [ ] Per-component latency within allocated budget (see V1 Ch23)

---

## M-3 — Walking Skeleton (First Full Call)

**Status:** ⬜ Pending  
**Target:** End of Sprint-012

**What it means:** The first complete end-to-end call path works. A spoken utterance flows through VAD → STT → Conversation Intelligence (all engines) → LLM → TTS → playback, and the system responds with synthesized speech within the latency budget. This is the most important technical milestone: it proves the architecture works.

**Acceptance Criteria:**
- [ ] End-to-end integration test: inject fake audio → full CIL → synthesized speech out (passes)
- [ ] First-audio p95 ≤ 1.5s on integration fixture (real AI models, not mocks)
- [ ] OutputValidator rejects LLM output containing facts not in ResponsePlan.facts (Law of Authority)
- [ ] NegotiationEngine never produces an offer outside the configured floor/ceiling
- [ ] Intent classification ≥90% accuracy on held-out labeled test set
- [ ] ResponsePlan schema valid on every call (no missing required fields)
- [ ] DecisionEnvelope created for every consequential decision

---

## M-4 — Reliability Complete

**Status:** ✅ **Reached** (2026-07-04)  
**Target:** End of Sprint-016

**What it means:** The system is durable. No committed event is ever lost. Recovery from any single failure class is deterministic and produces no duplicate effects. The system is fully observable.

**Acceptance Criteria:**
- [x] Zero uncommitted-event loss: publish → consumer dedup → process is atomic (integration test) — Sprint-013 EventBus/`EventDeduplicator`, validated against real Redis (`sprint013_infra_validation.py`)
- [x] Idempotency: submit an effect twice → exactly one record (integration test) — Sprint-015 `IdempotencyGuard.execute_once()`, validated with 10 concurrent real OS threads/connections against real Postgres (`scripts/validate/idempotency_test.py --concurrent 10` → 1 effect execution); PTP creation itself doesn't exist until Sprint-022, so the mechanism is exercised via `ConversationEngine`'s DecisionEnvelope publish, its one authoritative effect today
- [x] Recovery: simulate CPU crash → assert system recovers to correct state from snapshot+replay — Sprint-015 `CPURestartStrategy`, full drill against real Postgres+Redis (`scripts/sprint015_recovery_drill.py` → OVERALL PASS)
- [x] Recovery: simulate GPU failure → assert graceful failover, zero dropped calls in test — Sprint-015 `GPUFailureStrategy` (unit-tested against a `GPUFailoverPort`/`TTSHaltPort` double; no live GPU failure induced on the shared GPU node, out of scope per Sprint-015.md's own note)
- [x] Recovery: simulate Redis outage → assert degraded-mode operation (no data loss) — Sprint-015 `RedisOutageStrategy`
- [x] No unbounded queue anywhere (every queue has max_depth enforced) — Sprint-016 `BoundedQueue`/`QueueFullError` (100% coverage of queue instantiation) + `PlaybackScheduler`'s pre-existing RI-3 bounded deque, both now Prometheus-gauge-instrumented
- [x] Full trace: end-to-end OpenTelemetry trace visible per call in test environment — Sprint-016 `OTelTracer`, validated by `test_otel_trace_end_to_end` (3 services/3 spans, one shared `trace_id`) and `ConversationEngine.handle_turn()`'s per-turn span (`tests/unit/services/test_conversation_engine_recovery.py`)
- [x] Zero Redis keys without TTL (enforced by test) — Sprint-013 `TTLGuard`, mechanically enforced by `scripts/check_boundaries.py`'s AST scan for raw `.set()` calls (0 violations)

---

## M-5 — Compliance & Security Complete

**Status:** ⬜ Pending  
**Target:** End of Sprint-020

**What it means:** The system is compliant with RBI fair-practice codes and DPDP, and is hardened against the full security threat model (multi-tenant isolation, encryption, audit, AI governance).

**Acceptance Criteria:**
- [ ] Policy Engine: RBI calling hours test — zero calls outside permitted window
- [ ] Policy Engine: DPDP consent gate — call blocked if consent not recorded
- [ ] Cross-tenant data access: 100% of isolation breach attempts blocked in security tests
- [ ] AI Governance: zero LLM outputs with invented facts reach TTS (red-team test)
- [ ] All Postgres PII columns encrypted at field level (verified by column list test)
- [ ] Audit trail: every auth event, policy decision, PTP creation, consent change is logged (completeness test)
- [ ] PII: zero PII strings found in application log output (log scan test)
- [ ] Secrets: zero hardcoded secrets in codebase (CI gate via trufflehog/gitleaks)

---

## M-6 — SaaS Platform Complete

**Status:** ✅ **Reached** (2026-07-06, Sprint-025)  
**Target:** End of Sprint-025

**What it means:** The complete SaaS platform is operational. A new tenant can be onboarded, configured, and run a campaign — end to end — through the platform.

**Acceptance Criteria:**
- [x] Tenant onboarding flow: TRIAL → PRODUCTION lifecycle transitions work (Sprint-021 `TenantLifecycle`)
- [x] CustomerContext assembly: correct DPD, outstanding amount, contact info from CRM+Collections (Sprint-022 `CustomerContextAssembler`, integration test)
- [x] PTP idempotency: double submission → single record (Sprint-022 `PromiseToPayService`, 10-concurrent-connection integration test)
- [x] Campaign scheduling: zero calls outside RBI-permitted hours (Sprint-023 `ScheduleEngine`, `test_rbi_scheduling_compliance_suite`)
- [x] Billing: usage event → meter → invoice amount correct for test scenarios (Sprint-024 `UsageCollector`→`InvoiceGenerator`)
- [x] Entitlement enforcement: call blocked when usage limit exceeded (Sprint-024 `UsageLimitEnforcer`/`EntitlementEngine`)
- [x] Tenant isolation: cross-tenant API request returns 403 for all tested endpoints (Sprint-018 `TenantIsolationGuard`, AR-8 mechanically enforced in `BaseRepository`)
- [x] Public API: all endpoints present in OpenAPI spec and return correct schemas (Sprint-025 `api-specs/voiceos-public-v1.yaml`, `scripts/validate_openapi.py`, `test_openapi_spec_validates`)

---

## M-7 — Production Alpha

**Status:** ⬜ Pending  
**Target:** End of Sprint-028

**What it means:** The system is deployed to production infrastructure with full monitoring, has passed load testing and security penetration testing, and has completed a canary rollout to 100% of traffic.

**Acceptance Criteria:**
- [ ] First-audio p95 ≤ 1.5s at target concurrent call load (load test)
- [ ] GPU utilization ≤ 0.80 at target load (load test)
- [ ] Zero CRITICAL security pen test findings; all HIGH findings resolved
- [ ] Disaster recovery drill: RTO ≤ 30 min; RPO ≤ 5 min
- [ ] Chaos engineering: GPU node kill → graceful failover, no dropped calls
- [ ] Compliance validation: all RBI + DPDP automated test scenarios pass
- [ ] Canary rollout: 5%→25%→50%→100% completed with no auto-rollback triggers
- [ ] SLO dashboards live; error-budget burn alerts firing correctly in test scenario

---

## M-8 — Founder Validation

**Status:** ⬜ Pending  
**Target:** End of Sprint-029

**What it means:** Domain experts (founders + collections specialists) have reviewed real conversations and confirmed the system is appropriate for live use.

**Acceptance Criteria:**
- [ ] ≥50 calls reviewed using structured rubric (DocSuite-10)
- [ ] Zero Law-of-Authority violations (LLM never stated a fact not from authoritative sources)
- [ ] Zero RBI compliance failures (no prohibited language, no out-of-hours contact)
- [ ] Negotiation within envelope: 100% of offers within configured floor/ceiling
- [ ] MOS audio quality ≥ 3.5 on rated sample
- [ ] First-audio p95 ≤ 1.5s confirmed in real environment
- [ ] Signed founder sign-off document on file (`evaluation/founder-validation-report.md`)

---

## M-9 — Pilot Deployment

**Status:** ⬜ Pending  
**Target:** End of Sprint-030

**What it means:** The system has handled live production calls with real borrowers and demonstrated reliable, compliant behavior.

**Acceptance Criteria:**
- [ ] ≥95% of pilot calls completed without system intervention
- [ ] Availability ≥ 99.95% during pilot period
- [ ] First-audio p95 ≤ 1.5s maintained during pilot
- [ ] Zero regulatory violations during pilot period
- [ ] Zero data breaches or tenant isolation failures
- [ ] All CRITICAL issues resolved within 2h SLA; all HIGH within 24h
- [ ] Pilot report on file (`evaluation/pilot-report.md`)

---

---

## M-11 — Enterprise Platform Complete

**Status:** ⬜ Pending
**Target:** End of Sprint-032

**What it means:** Enterprise customers can federate their identity via SAML/OIDC, their users are auto-provisioned via SCIM, their audit data is exportable in tamper-evident format, and their data is isolated by region.

**Acceptance Criteria:**
- [ ] SAML SP-initiated SSO: full flow with mock IdP passes
- [ ] OIDC PKCE flow: authorization code exchange resolves user identity
- [ ] SCIM provisioning: user created/deactivated; group sync updates RBAC roles
- [ ] Audit export: NDJSON exported to test S3, signature verified
- [ ] Data residency: cross-region write rejected

---

## M-12 — Workflow & Customer Success Complete

**Status:** ⬜ Pending
**Target:** End of Sprint-033

**What it means:** Customers can automate collections workflows without code. The CS team can monitor tenant health and predict churn before it happens.

**Acceptance Criteria:**
- [ ] WorkflowEngine: event trigger → action executed (integration test)
- [ ] Workflow execution survives process restart (idempotent resume)
- [ ] Approval flow connects to HITL queue
- [ ] TenantHealthScorer: daily scores for all tenants, no errors
- [ ] ChurnPredictor: risk probability returned for all active tenants

---

## M-13 — Conversation Learning Layer Complete

**Status:** ⬜ Pending
**Target:** End of Sprint-034

**What it means:** The system can improve itself. Failed turns are mined, labeled, used to fine-tune IntentEngine, and the improved model is safely promoted using the same quality gate as Founder Validation.

**Acceptance Criteria:**
- [ ] FailedTurnMiner mines turns from MongoDB quality scores
- [ ] WeakLabelGenerator generates labels stored in Postgres
- [ ] ModelDriftTracker computes accuracy weekly on holdout set
- [ ] OfflineTrainingPipeline completes without error
- [ ] PromotionGate blocks substandard model; promotes qualifying model

---

## M-10 — Production Release

**Status:** ⬜ Pending
**Target:** End of Sprint-031 (requires M-9 + M-11 + M-12 + M-13 complete)

**What it means:** VoiceOS v2 is live in full production with unrestricted tenant onboarding, all SLOs active, all operational processes running, enterprise features live, and the learning loop closed.

**Acceptance Criteria:**
- [ ] Production deployment complete (canary 100%)
- [ ] SLO dashboards and error-budget alerting live
- [ ] Customer-facing API documentation published
- [ ] Support runbooks finalized and distributed
- [ ] Governance runbooks present: data-breach, privacy-incident, AI misbehavior, credential-compromise, compliance-violation
- [ ] Enterprise operations guides present: dedicated-cluster-ops, premium-sla-delivery, air-gapped-deployment
- [ ] CHANGELOG.md v1.0.0 entry committed
- [ ] PROJECT_STATUS.md: 34/34 sprints complete, phase → Production
- [ ] No outstanding CRITICAL or HIGH issues
