# VoiceOS v2 — Epics

**Generated:** 2026-06-29  
**Total Epics:** 8  
**Status:** All epics pending implementation start.

---

## Overview

The 31 implementation sprints are organized into 8 epics that follow a strict dependency order. Each epic delivers a named milestone. The system is built using a **walking-skeleton-first** strategy: a complete end-to-end call path (thin, unpolished) is operational by the end of Epic E3, and every subsequent epic hardens, secures, scales, and productizes that skeleton.

---

## Epic E1 — Foundation & Engineering Infrastructure

**Sprints:** 001–003  
**Milestone:** Foundation Complete  
**Milestone Reached:** End of Sprint-003

### Purpose
Establish the engineering foundation that every subsequent sprint depends on. No production feature is started until this foundation is verified complete: the repository structure is correct, all shared typed contracts exist, runtime invariant guards are in place, and the CI pipeline enforces the architecture rules.

### Scope
- Monorepo layout per Volume 6 Ch2
- `src/libs/contracts/` — all shared typed data models (ResponsePlan, DecisionEnvelope, EventEnvelope, TurnInput, CustomerContext, AudioFrame, DomainEvent, all domain entities)
- `src/libs/invariants/` — RI-1 through RI-8 as runtime-callable, CI-tested assertions
- All Postgres and MongoDB schemas (DDL + Alembic migrations)
- CI/CD pipeline: ruff → mypy --strict → pytest (with coverage gates: ≥90% core, 100% invariants)
- Docker Compose dev environment
- Developer scripts and pre-commit hooks

### Exit Criteria
- All contracts fully typed and passing mypy --strict
- All invariant guards testable (each RI-1–RI-8 has at least one failing + one passing test)
- CI pipeline runs green end-to-end
- Docker Compose `up` starts all services cleanly

### Architecture References
V6 Ch2, Ch3, Ch4, Ch9, Ch11, Ch12; V1 Appendix A, Appendix E; DocSuite-02, DocSuite-03, DocSuite-12

---

## Epic E2 — Core Voice Runtime

**Sprints:** 004–008  
**Milestone:** Core Runtime Complete  
**Milestone Reached:** End of Sprint-008

### Purpose
Build the real-time media plane: everything from telephony ingress to GPU scheduling. These are latency-critical infrastructure components; no AI service can operate until the media plane delivers clean audio in real-time.

### Scope
- **Media Gateway** — TransportAdapter protocol, Twilio WebSocket adapter, SIP/RTP adapter, auth-before-allocation
- **Audio Session Manager** — adaptive jitter buffer, PLC, session lifecycle state machine
- **Audio Preprocessing** — AEC3, noise suppression, AGC, 8kHz→16kHz resampling
- **VAD & Endpointing** — Silero VAD, adaptive pause detection, barge-in detection, backchannel discrimination
- **GPU Scheduler** — VRAM ledger (OOM-by-construction), per-model pools, warm-before-admit, graceful failover

### Exit Criteria
- Audio can flow from a fake Twilio WebSocket → session manager → preprocessed and VAD-segmented audio frames → ready for STT
- Barge-in signal correctly flushes the downstream playback pipeline
- GPU Scheduler correctly rejects requests that would exceed VRAM (no OOM errors in any test)
- All per-component latency budgets measured and within allocation

### Architecture References
V1 Ch3, Ch4, Ch5, Ch6, Ch7; V7 Ch6; DocSuite-02

---

## Epic E3 — Conversation Intelligence

**Sprints:** 009–012  
**Milestone:** Conversation Intelligence Complete (Walking Skeleton)  
**Milestone Reached:** End of Sprint-012

### Purpose
Build all AI service adapters and all conversation intelligence engines, then assemble them into the first complete, end-to-end call path. This is the **walking skeleton milestone**: a real spoken conversation — VAD → STT → intent → strategy → negotiation → LLM → TTS → playback — completes in a test.

### Scope
- **STT Adapter** — Whisper streaming (word hypotheses)
- **LLM Runtime Adapter** — Qwen/vLLM, token streaming, deterministic prompt contract
- **TTS Adapter** — Veena streaming, Hindi/Hinglish/English, clause-level audio
- **Perception engines** — Intent Engine (13 labels, <30ms), Entity Extraction, Emotion Intelligence, Working Memory, Relationship Memory, Conversation State Intelligence
- **Decision engines** — Risk Engine, Dialogue Policy Engine, Strategy Engine, Goal Planner, Negotiation Engine (non-bypassable boundary clamp), Empathy Planner
- **Orchestration** — Response Planning Engine, Dialogue Manager, Conversation Engine (CIL), Prompt Builder, Output Validator
- **Delivery** — True Streaming Pipeline, Playback Scheduler, Audio Output
- **Quality** — Output Evaluation Engine
- **Integration test** — inject fake audio → full call path → playback assertion → latency ≤ 1.5s

### Exit Criteria
- End-to-end integration test passes (fake audio in → synthesized speech out)
- First-audio p95 ≤ 1.5s on integration fixture (not yet full load test)
- Law-of-Authority check: OutputValidator rejects LLM output containing facts not in ResponsePlan.facts
- Negotiation envelope: NegotiationEngine never produces an offer outside the configured floor/ceiling
- Intent classification: ≥90% accuracy on held-out test set

### Architecture References
V1 Ch8–10, Ch12–18, Ch21–22; V2 Ch3–15, Ch17; DocSuite-02, DocSuite-06, DocSuite-08

---

## Epic E4 — Reliability Infrastructure

**Sprints:** 013–016  
**Milestone:** Reliability Complete  
**Milestone Reached:** End of Sprint-016

### Purpose
Add the durable reliability layer beneath the walking skeleton. After this epic, the system guarantees: zero committed-event loss, deterministic crash recovery, exactly-once authoritative effects, bounded memory and queue growth, and end-to-end observability.

### Scope
- **Event Bus** — immutable ordered events, DomainEvent schema, at-least-once + consumer dedup, DLQ
- **Redis layer** — hot state, distributed locks with fencing tokens, TTL discipline, rate limiting
- **Persistent Storage** — Postgres (authoritative), MongoDB (lineage/documents), migrations
- **State Persistence** — Snapshot + event-tail replay, Recoverable protocol
- **Crash Recovery** — deterministic recovery per failure class (CPU/GPU/Redis/DB/Twilio)
- **Idempotency** — IdempotencyGuard.execute_once(), atomic check+execute+record
- **Concurrency** — single-writer, per-call media thread, fair-share, backpressure, load shedding
- **Queue Management** — bounded queues, priority lanes, exponential retry, DLQ
- **Circuit Breakers & Failover** — CLOSED→OPEN→HALF_OPEN, per-service
- **Observability** — Prometheus metrics, structured JSON logs, OpenTelemetry traces

### Exit Criteria
- Recovery integration tests: simulate each failure class → assert deterministic recovery + zero duplicate effects
- No unbounded queues anywhere in the system
- Idempotency test: submit same PTP creation twice → exactly one record created
- Trace propagation: full call trace visible end-to-end in test environment
- Redis: no Redis key exists without a TTL

### Architecture References
V3 (all chapters); DocSuite-03, DocSuite-08

---

## Epic E5 — Compliance & Security

**Sprints:** 017–020  
**Milestone:** Compliance & Security Complete  
**Milestone Reached:** End of Sprint-020

### Purpose
Implement the complete compliance and security layer: the Policy Engine (governance of all decisions), regulatory compliance rules (RBI, DPDP), authentication, authorization/RBAC, secrets management, encryption, privacy, PII protection, audit, and AI governance.

### Scope
- **Policy Engine** — unified PDP (PERMIT|DENY|REQUIRE|FORBID), inheritance, deny-overrides, RBI/DPDP rule packs
- **Regulatory Compliance** — RBI fair-practice rules, DPDP consent gate, recording consent, retention schedules
- **Authentication** — OAuth2/OIDC/JWT (users), mTLS (services), API keys, device attestation
- **Authorization/RBAC** — RBAC+ABAC, tenant isolation invariant, JIT privileges
- **AI Governance** — GovernanceVerdict (APPROVE|REQUIRE_HUMAN|BLOCK), Law-of-Authority enforcement gate
- **Secrets Management** — vault, runtime injection only, rotation, emergency revocation
- **Encryption** — AES-256-GCM at rest, TLS 1.3/SRTP, envelope encryption (DEK/KEK), crypto-shredding
- **Privacy** — data minimization, purpose limitation, right-to-erasure workflow
- **PII Protection** — detection, redaction, tokenization
- **Audit** — immutable append-only tamper-evident audit trail
- **API Security, Runtime Security, AI Safety** — hardening across all attack surfaces

### Exit Criteria
- Policy Engine: all RBI calling-hours tests pass, all DPDP consent-gate tests pass
- Cross-tenant data access: 100% blocked in security tests
- AI Governance gate: no LLM output with invented facts reaches TTS
- Encryption: all Postgres PII columns encrypted at field level
- Audit: every auth event, policy decision, data access, PTP creation, consent change is audited
- PII redaction: no PII in application logs

### Architecture References
V4 (all chapters); DocSuite-05, DocSuite-08

---

## Epic E6 — SaaS Platform

**Sprints:** 021–025  
**Milestone:** SaaS Platform Complete  
**Milestone Reached:** End of Sprint-025

### Purpose
Build the complete multi-tenant SaaS platform that financial institution customers use to configure, monitor, and operate VoiceOS: tenant management, CRM, collections system of record, campaigns, contact center, billing, analytics, and the public API.

### Scope
- **Multi-Tenancy** — isolation profiles, org hierarchy, tenant lifecycle (TRIAL→PRODUCTION→DELETED)
- **User Management** — roles, org-scoped RBAC, SSO integration stub
- **CRM** — authoritative party data, CustomerContext assembly
- **Loan & Collections** — LoanAccount, EMI, DPD, PTP (idempotent), settlement, callback, escalation
- **Campaign Management** — audience selection, RBI-compliant scheduling, A/B testing
- **Contact Center** — AI+human blended, live transfer, supervisor join/monitor/barge
- **Billing** — subscription + usage-based + enterprise contracts, entitlements
- **Usage Metering** — real-time meter events, aggregation, limit enforcement
- **Analytics** — per-call, per-campaign analytics
- **Reporting** — scheduled reports, export
- **Admin Portal** — full admin API backend
- **AI Configuration Platform** — prompt versioning, model config per tenant/campaign
- **Integration & API Platform** — webhooks, public REST API (OpenAPI 3.1 spec-first)

### Exit Criteria
- CustomerContext assembly: correct facts (amount, DPD, account) from CRM + collections data
- PTP idempotency: double-submission → single record
- Campaign scheduling: zero calls outside RBI-permitted hours
- Billing: usage accumulation + invoice calculation correct
- Tenant isolation: cross-tenant API requests return 403
- Public API: all endpoints covered by OpenAPI spec and validated

### Architecture References
V5 (all chapters); V4 Ch5–6; DocSuite-03, DocSuite-04, DocSuite-05

---

## Epic E7 — Production Alpha

**Sprints:** 026–028  
**Milestone:** Production Alpha  
**Milestone Reached:** End of Sprint-028

### Purpose
Stand up the complete production infrastructure, execute all production-readiness gates (performance, load, chaos, security pen test, compliance), and deploy the production alpha via canary rollout.

### Scope
- **Infrastructure as Code** — Terraform, Helm charts for all services, Kubernetes architecture
- **Kubernetes** — node pools (CPU, GPU-tainted, data, system), Guaranteed QoS, PDBs
- **GPU Fleet** — node pools, warm-before-admit, graceful failover
- **Monitoring** — Prometheus, Grafana SLO dashboards, error-budget burn
- **Alerting** — CRITICAL/WARNING/INFO, multi-window burn-rate
- **Logging** — centralized structured logs, PII redacted
- **Tracing** — distributed traces end-to-end
- **Disaster Recovery** — Postgres PITR, Redis backup, multi-AZ, RTO runbook
- **Autoscaling** — HPA + custom metrics
- **Performance Validation** — latency budget (p95 ≤ 1.5s), per-stage measurement
- **Load Testing** — target concurrency, GPU utilization ≤ 0.80
- **Chaos Engineering** — GPU failure, Redis failure, packet loss
- **Security Penetration Test** — external pen test, fix all CRITICAL/HIGH
- **Compliance Validation** — RBI/DPDP automated test suite
- **Production Alpha Deploy** — canary 5%→25%→50%→100%

### Exit Criteria
- First-audio p95 ≤ 1.5s under load (target concurrent calls)
- Zero CRITICAL security findings; all HIGH findings resolved
- Disaster recovery drill: RTO ≤ 30 min achieved
- Compliance test suite: all RBI + DPDP tests pass
- Canary rollout complete with no auto-rollback triggers

### Architecture References
V7 (all chapters); V4 Ch21; V1 Ch23; DocSuite-09, DocSuite-10

---

## Epic E8 — Founder Validation → Pilot → Production Release

**Sprints:** 029–031  
**Milestone:** Production Release  
**Milestone Reached:** End of Sprint-031

### Purpose
Validate conversation quality with domain experts (founder/collections specialists), run a controlled live-production pilot, then execute the full production release with all operational processes active.

### Scope
- **Founder Validation** — structured review of 50+ calls against rubric (Law of Authority, negotiation, tone, RBI compliance, MOS, latency)
- **Pilot Deployment** — 10–20 live borrower accounts, on-call rotation, daily report, issue triage
- **Production Release** — full launch, SLO dashboards live, customer API documentation published, runbooks finalized

### Exit Criteria
- Founder Validation: zero Law-of-Authority violations, zero RBI compliance failures, negotiation within envelope 100%, MOS ≥ 3.5, first-audio p95 ≤ 1.5s
- Pilot: ≥95% calls complete without system intervention, all SLOs met, zero regulatory violations
- Production Release: v1.0.0 tagged, all tracking documents updated to reflect 31/31 sprints complete

### Architecture References
V2 (quality review); V7 Ch16, Ch17; DocSuite-07, DocSuite-09, DocSuite-10
