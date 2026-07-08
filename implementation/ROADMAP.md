# VoiceOS v2 — Implementation Roadmap

**Generated:** 2026-06-29 · **Updated:** 2026-07-04 (Sprint-015 complete — status line corrected; see PROJECT_STATUS.md/implementation/BACKLOG.md for live progress tracking)
**Status:** Planning complete. Implementation in progress — 15/34 sprints done (through Sprint-015). This document is the static sprint specification; for current status always defer to `PROJECT_STATUS.md` and `implementation/BACKLOG.md`, which are updated after every sprint.
**Total Sprints:** 34
**Total Epics:** 8 (plus 2 extended epics for post-E6 platform features)  

---

## Two-Phase Sprint Execution Model

Every sprint from Sprint-009 onward follows a mandatory two-phase execution model. **A sprint is not complete until both phases pass.**

### Phase 1 — Local Development & Mock Validation

Everything that can be completed without real production infrastructure:

- Source code implementation
- Unit tests, mock integration tests, static analysis (`ruff check`, `ruff format --check`, `mypy --strict`, `check_boundaries.py`)
- Minimum coverage gate: ≥ 85%
- Mock backends: `FakeRedisClient`, `TestPostgres` Docker fixture, `FakeEventBus`, `FakeGPUScheduler`, `AsyncMock` for AI backends, `FakeKMSClient`, `FakeVaultClient`

> **No CPU or GPU infrastructure is required for Phase 1.** Each sprint's Phase 1 section explicitly states this.

### Phase 2 — Deployment & Real Infrastructure Validation

Separated into two sub-sections:

**CPU Node** — services deployed, why, deployment procedure, health checks, integration validation, rollback procedure. Lists all previously deployed services that remain running (cumulative deployment model).

**GPU Node** — either:
- GPU model deployments (VRAM, latency, streaming, utilization validation), OR
- Explicit statement: *"GPU node is not required during this sprint. Previously deployed GPU services remain running unchanged."*

Every Phase 2 section includes **Infrastructure Validation** and **Regression Validation** subsections.

### Cumulative Deployment Model

Services deployed in previous sprints remain deployed and running. The objective is that after Sprint-034, the CPU node and GPU node are already running the complete VoiceOS platform. There is no separate final deployment project — the system is built incrementally into its production state sprint by sprint.

---

## Infrastructure Snapshot & Disaster Recovery

Every sprint from Sprint-009 onward includes a mandatory **Infrastructure Snapshot** section (appended after Completion Criteria). **A sprint is not complete until the infrastructure snapshot is updated.**

### Persistent Infrastructure State Documents

Two living documents track the complete current state of both nodes:

- **`deployment/CPU_NODE_STATE.md`** — Complete CPU node state: OS, system packages, Python/venv, Docker, Kubernetes (namespaces, resource quotas, priority classes), Helm, all deployed services (image, port, namespace, startup order), Redis configuration, PostgreSQL (schema/Alembic version), MongoDB collections, EventBus consumer groups, environment variable names (never values), database schema, observability stack, port map, volumes, health check commands, rollback procedures.

- **`deployment/GPU_NODE_STATE.md`** — Complete GPU node state: OS, NVIDIA driver, CUDA, cuDNN, Container Toolkit, Python/venv, Docker runtime configuration, vLLM, all deployed AI models (VRAM, port, precision, latency targets, health checks), model paths/cache directories, model download procedures, service startup order, CPU↔GPU communication endpoints, latency validation commands, rollback procedures.

Both documents must reflect the **entire** system state, not just the delta from the current sprint. After every sprint they are updated to show the complete cumulative picture.

### Machine-Rebuild Scripts

The following scripts must be kept current so that either node can be rebuilt from scratch with no manual steps beyond providing secrets:

**CPU node:**
- `deployment/cpu/bootstrap.sh` — OS-level setup: apt packages, Python 3.12, Docker, kubectl, Helm, Terraform, database clients
- `deployment/cpu/restore.sh` — Application restore: source env, install Python deps, apply K8s manifests, run Alembic migrations, `helm upgrade --install`, wait for ready, run healthcheck, run regression tests
- `deployment/cpu/healthcheck.sh` — Verifies all deployed services, Redis, Postgres, MongoDB; exits non-zero on any failure
- `deployment/cpu/.env.example` — All environment variable names and descriptions; never actual values

**GPU node:**
- `deployment/gpu/bootstrap.sh` — OS-level: NVIDIA driver, CUDA, cuDNN, Container Toolkit, Python 3.12, Docker configured with NVIDIA runtime, model download tooling
- `deployment/gpu/restore.sh` — Model downloads (driven by `model_manifest.yaml`), service startup (Whisper→Veena→vLLM), VRAM check, latency validation, notify CPU node GPU Scheduler
- `deployment/gpu/healthcheck.sh` — nvidia-smi VRAM budget check; STT/LLM/TTS endpoint health; exits non-zero on any failure
- `deployment/gpu/model_manifest.yaml` — Machine-readable model registry: download commands, VRAM allocations, ports, health checks, latency targets, startup order, post-restore validation
- `deployment/gpu/.env.example` — All GPU environment variable names; never actual values

### DR Validation (Phase 2 Requirement)

Each sprint's Phase 2 Infrastructure Validation includes a DR validation step:

1. Fresh CPU node rebuild: `bootstrap.sh` → `restore.sh` → `healthcheck.sh` — all services healthy
2. Fresh GPU node rebuild (GPU sprints): `bootstrap.sh` → `restore.sh` → `healthcheck.sh` — all models running; VRAM budget met; latency targets met
3. Cross-node communication verified: CPU↔GPU via GPU Scheduler
4. Regression test suite passes on rebuilt infrastructure

No manual configuration beyond providing secrets (SSH keys, API keys, passwords, certificates) is required. The rebuild is fully automated from the deployment scripts.

### Definition of Done Addition

A sprint's Definition of Done is not satisfied until:
- `deployment/CPU_NODE_STATE.md` updated to reflect complete current node state
- `deployment/GPU_NODE_STATE.md` updated to reflect complete current GPU state (or explicitly marked unchanged)
- All machine-rebuild scripts updated to deploy the complete system as of this sprint
- DR validation commands in Phase 2 pass on rebuilt infrastructure

---

## Overview

This roadmap was generated from a complete reading of all seven architecture volumes and the twelve-document Documentation Suite. It sequences all implementation work in strict dependency order: no sprint may begin until all its declared dependencies are complete.

The philosophy is **walking-skeleton-first**: a thin end-to-end call path is delivered by the end of Epic E3, then every layer is hardened in subsequent epics.

---

## Epic Summary

| Epic | ID | Sprints | Milestone |
|---|---|---|---|
| Foundation & Engineering Infrastructure | E1 | 001–003 | Foundation Complete (end Sprint-003) |
| Core Voice Runtime | E2 | 004–008 | Core Runtime Complete (end Sprint-008) |
| Conversation Intelligence | E3 | 009–012 | Conversation Intelligence Complete (end Sprint-012) |
| Reliability Infrastructure | E4 | 013–016 | Reliability Complete (end Sprint-016) |
| Compliance & Security | E5 | 017–020 | Compliance & Security Complete (end Sprint-020) |
| SaaS Platform | E6 | 021–025 | SaaS Platform Complete (end Sprint-025) |
| Production Alpha | E7 | 026–028 | Production Alpha (end Sprint-028) |
| Founder Validation → Pilot → Production Release | E8 | 029–031 | Production Release (end Sprint-031) |
| Enterprise Platform (SSO/SCIM/Multi-Region) | E6-Ext | 032 | Enterprise Platform Complete (end Sprint-032) |
| Workflow Automation + Customer Success | E6-Ext | 033 | Workflow & CS Complete (end Sprint-033) |
| Conversation Learning Layer | E3-Ext | 034 | Learning Layer Complete (end Sprint-034) |

---

## Epic E1 — Foundation & Engineering Infrastructure

**Goal:** Establish the repository structure, all shared typed contracts, runtime invariant guards, CI gates, and developer tooling that every subsequent sprint depends on. Nothing is built on sand.

### Sprint-001: Repository Scaffolding, Contracts & Invariants
**Objective:** Initialize the monorepo layout per Volume 6 Ch2, create the full `src/libs/contracts` and `src/libs/invariants` packages with all core typed data models, and stand up the CI skeleton.

**Key deliverables:**
- `pyproject.toml` with Python 3.12+, ruff, mypy, pytest configuration
- `src/libs/contracts/` — ResponsePlan, DecisionEnvelope, EventEnvelope, TurnInput, CustomerContext, AudioFrame, DomainEvent, all core enums and types
- `src/libs/invariants/` — RI-1 through RI-8 runtime guards as callable, testable assertions
- CI pipeline skeleton (GitHub Actions / equivalent): ruff lint → mypy --strict → pytest
- Module boundary enforcement (no cross-package imports outside defined interfaces)

**Architecture:** V6 Ch2, Ch3, Ch4 (AR-1–AR-20); V1 Appendix A, Appendix E; DocSuite-02, DocSuite-03, DocSuite-12

**Depends on:** None (first sprint)

---

### Sprint-002: Event Contracts & Data Models
**Objective:** Complete the full event contract library — all DomainEvent subtypes, all persistent data models (Postgres schemas, MongoDB document shapes), and the shared money/ID/timestamp types.

**Key deliverables:**
- `src/libs/contracts/events/` — all DomainEvent subtypes for every domain (audio, dialogue, intelligence, reliability, compliance, SaaS)
- `src/libs/contracts/models/` — Customer, LoanAccount, PromiseToPay, Consent, Campaign, Tenant, BillingRecord
- Postgres schema DDL scripts for all authoritative tables
- MongoDB collection schemas (lineage, response_plans, transcripts)
- Shared primitive types: Money (with currency), TenantId, CallId, TraceId, EntityId

**Architecture:** V3 Ch3, Ch5; V5 Ch4, Ch5; V6 Ch6, Ch7; DocSuite-03

**Depends on:** Sprint-001

---

### Sprint-003: Testing Infrastructure, CI/CD Pipeline & Developer Tooling
**Objective:** Deliver a complete, enforcing CI/CD pipeline — test pyramid harnesses, coverage gates (≥90% core, 100% invariants), Docker Compose dev environment, and all developer scripts.

**Key deliverables:**
- `tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/load/`, `tests/invariants/` directory structure with fixtures
- Audio injection test harness (fake RTP stream generator)
- CI/CD pipeline: lint → type-check → unit tests (with coverage gate) → invariant tests → integration tests → build
- Docker Compose file covering all services for local development
- `scripts/` — setup, seed data, test-run, lint, local dev helpers
- Pre-commit hooks

**Architecture:** V6 Ch9, Ch11, Ch12; V7 Ch4; DocSuite-08, DocSuite-09, DocSuite-12

**Depends on:** Sprint-001, Sprint-002

---

## Epic E2 — Core Voice Runtime

**Goal:** Build the complete real-time media plane: telephony ingress, session management, audio preprocessing, voice activity detection, and the GPU scheduler. These are the latency-critical foundations the AI services plug into.

### Sprint-004: Media Gateway
**Objective:** Implement the Media Gateway service — the telephony ingress boundary. TransportAdapter protocol with Twilio WebSocket and SIP/RTP concrete adapters; auth-before-allocation; inbound audio session negotiation.

**Key deliverables:**
- `src/services/media-gateway/` — TransportAdapter protocol (abstract base)
- `adapters/twilio_websocket.py` — Twilio Media Streams WebSocket handler
- `adapters/sip_rtp.py` — SIP signaling + RTP media ingress
- Auth-before-allocation enforcement (AR-2 compliant)
- Session admission (reject before resource allocation)
- Unit + integration tests (fake Twilio webhook, fake SIP flow)

**Architecture:** V1 Ch3; V6 Ch3–5; DocSuite-02

**Depends on:** Sprint-001, Sprint-002, Sprint-003

---

### Sprint-005: Audio Session Manager
**Objective:** Implement the Audio Session Manager — jitter buffer, packet loss concealment, session clock, and the session lifecycle state machine.

**Key deliverables:**
- `src/services/audio-session-manager/` — AudioSession, SessionClock
- Adaptive jitter buffer (configurable target delay, min/max bounds)
- Packet loss concealment (PLC) for G.711/G.722
- Session lifecycle state machine: CONNECTING → ACTIVE → BARGE_IN → ENDING → CLOSED
- Per-session metrics (jitter, loss, latency)
- Unit tests for jitter buffer correctness, PLC output quality

**Architecture:** V1 Ch4; DocSuite-02

**Depends on:** Sprint-004

---

### Sprint-006: Audio Preprocessing Pipeline
**Objective:** Implement the audio preprocessing pipeline — AEC3 (acoustic echo cancellation), NS (noise suppression), AGC (automatic gain control), and 8kHz→16kHz resampling.

**Key deliverables:**
- `src/services/audio-preprocessing/` — AudioPreprocessor, pipeline composition
- AEC3 integration (WebRTC AEC3 via python-webrtc or equivalent)
- Noise suppression (RNNoise or WebRTC NS)
- AGC (WebRTC AGC2) with target loudness and compression
- 8kHz→16kHz resampling (sinc interpolation)
- Pipeline latency tests (preprocessing must not exceed budget)
- Audio quality regression tests (PESQ/POLQA or SNR comparison)

**Architecture:** V1 Ch5; DocSuite-02

**Depends on:** Sprint-005

---

### Sprint-007: VAD & Endpointing
**Objective:** Implement voice activity detection, adaptive endpointing, barge-in detection, and backchannel (filler sound) discrimination.

**Key deliverables:**
- `src/services/vad-endpointing/` — VADEngine, EndpointDetector, BargeinDetector
- Silero VAD integration (ONNX runtime) with calibrated thresholds
- Adaptive pause detection (silence_ms threshold adjusts per call segment)
- Barge-in signal: interruption → flush playback → yield turn (RI-1 compliant)
- Backchannel discrimination (filters "hmm", "haan" from full barge-in)
- Unit tests: VAD precision/recall on labelled audio clips
- Integration test: end-to-end barge-in through to playback flush signal

**Architecture:** V1 Ch6; DocSuite-02

**Depends on:** Sprint-006

---

### Sprint-008: GPU Scheduler
**Objective:** Implement the GPU Scheduler — the system-wide VRAM ledger, OOM-by-construction admission control, per-model GPU pools, warm-before-admit guarantee, and graceful failover.

**Key deliverables:**
- `src/services/gpu-scheduler/` — GPUScheduler, VRAMledger, ModelPool
- VRAM ledger: tracks allocated vs. available VRAM per GPU device
- Admission control: request is REJECTED (not queued-to-OOM) when VRAM insufficient (RI-8)
- Per-model pools: STT pool, LLM pool, TTS pool with configurable sizes
- Warm-before-admit: new model slot warms before old slot is released (GPU-1)
- Graceful failover: on GPU-N failure, drain to GPU-M without dropped calls (GPU-2)
- Priority queue: in-call STT/TTS > idle LLM prefill
- Unit tests: VRAM accounting correctness, OOM-rejection, failover simulation

**Architecture:** V1 Ch7; V7 Ch6; DocSuite-02

**Depends on:** Sprint-001, Sprint-002, Sprint-003

---

## Epic E3 — Conversation Intelligence

**Goal:** Build all AI services (STT, LLM, TTS) and all conversation intelligence engines, then wire them into a complete orchestrated call path — the "walking skeleton" first real call.

### Sprint-009: STT, LLM & TTS Adapter Services
**Objective:** Implement the three AI service adapters behind abstract interfaces. Each wraps a specific model (Whisper Large-v3 Turbo FP8 for STT, Qwen2.5-7B-Instruct-FP8/vLLM for LLM, Veena AI FP16 for TTS) and exposes a streaming contract.

**Key deliverables:**
- `src/services/stt/` — STTAdapter protocol + WhisperAdapter (streaming word hypotheses)
- `src/services/llm-runtime/` — LLMAdapter protocol + vLLM adapter (token streaming, templated prompt)
- `src/services/tts/` — TTSAdapter protocol + VeenaAdapter (clause-level audio streaming, Hindi/Hinglish/English)
- All adapters implement a replaceable interface (model-agnostic per CLAUDE.md AI Model Rules)
- GPU Scheduler integration: each adapter requests VRAM grant before inference
- Latency instrumentation (per-stage: TTFT, first-clause, full synthesis)
- Streaming contract tests (word-by-word STT, token-by-token LLM, clause-by-clause TTS)
- Adapter unit tests with mock model backends

**Architecture:** V1 Ch8, Ch13, Ch15–17; V7 Ch6; DocSuite-02, DocSuite-06

**Depends on:** Sprint-008

---

### Sprint-010: Intelligence Engines — Perception Layer
**Objective:** Implement the perception layer of conversation intelligence: Intent Engine, Entity Extraction Engine, Emotion Intelligence, Working Memory, and Relationship Memory.

**Key deliverables:**
- `src/engines/intent/` — IntentEngine (fine-tuned encoder, 13 intent labels: PAYMENT, PROMISE_TO_PAY, DISPUTE, HARDSHIP, CALLBACK, UNAVAILABLE, DISCONNECT, ABUSE, IDENTITY_VERIFY, CONSENT_GRANT, CONSENT_REVOKE, SILENCE, OTHER), calibrated confidence scores, <30ms latency
- `src/engines/entity-extraction/` — EntityExtractor (slot-filler: amounts, dates, account refs, PII entities)
- `src/engines/emotion/` — EmotionIntelligenceEngine (sentiment + arousal estimation from speech and text features)
- `src/engines/memory/working/` — WorkingMemoryStore (per-call in-flight state: Redis-backed, TTL-bound)
- `src/engines/memory/relationship/` — RelationshipMemoryStore (cross-call persistent state: Postgres-backed)
- `src/engines/conversation-state/` — ConversationStateIntelligence (turn-level state machine)
- Unit tests for intent classification accuracy (>90% on held-out set)
- Unit tests for entity extraction recall on standard fixtures

**Architecture:** V2 Ch3, Ch9, Ch10, Ch11, Ch12, Ch13; DocSuite-03, DocSuite-08

**Depends on:** Sprint-001, Sprint-002, Sprint-009

---

### Sprint-011: Intelligence Engines — Decision Layer
**Objective:** Implement the decision-making engines: Risk Engine, Dialogue Policy Engine, Strategy Engine, Goal Planner, Negotiation Engine, and Empathy Planner.

**Key deliverables:**
- `src/engines/risk/` — RiskEngine (risk flags: escalation triggers, hardship indicators, abuse detection, regulatory risk)
- `src/engines/dialogue-policy/` — DialoguePolicyEngine (legal/compliance guardrails — hard constraints on what can be said)
- `src/engines/strategy/` — StrategyEngine (action selection: ASK|VERIFY|NEGOTIATE|REASSURE|ESCALATE|TRANSFER|CLOSE|CONFIRM)
- `src/engines/goal-planner/` — GoalPlanner (constrained goal decomposition for this turn)
- `src/engines/negotiation/` — NegotiationEngine (NegotiationEnvelope: floors/ceilings, NegotiationMove types, boundary clamp is non-bypassable)
- `src/engines/empathy/` — EmpathyPlanner (tone and pacing adaptation from emotion signal)
- Unit tests: strategy action coverage, negotiation boundary clamping (must never produce offers outside floor/ceiling), risk flag accuracy

**Architecture:** V2 Ch4, Ch5, Ch6, Ch7, Ch8, Ch14; DocSuite-03, DocSuite-08

**Depends on:** Sprint-010

---

### Sprint-012: Conversation Orchestration — Walking Skeleton
**Objective:** Assemble all engines and services into the complete orchestrated call path. Implement the Dialogue Manager, Conversation Engine (CIL orchestrator), Prompt Builder, Output Validator, True Streaming Pipeline, Playback Scheduler, Audio Output, and Output Evaluation Engine. Deliver the first complete end-to-end call test.

**Key deliverables:**
- `src/services/dialogue-manager/` — DialogueManager (turn coordination: barge-in handling, turn yield, backchannel gating)
- `src/services/conversation-engine/` — ConversationEngine (full CIL orchestration: assembles TurnInput → calls all engines → builds ResponsePlan → dispatches to delivery)
- `src/engines/prompt-builder/` — PromptBuilder (deterministic prompt assembly from ResponsePlan + CustomerContext; versioned templates from DocSuite-06; RI-7 compliant)
- `src/services/llm-runtime/output_validator.py` — OutputValidator (grounds LLM output against ResponsePlan.facts; rejects hallucinations; RI-5 check)
- `src/services/tts/streaming_pipeline.py` — True Streaming Pipeline (STT stream → LLM token stream → TTS clause stream → playback; RI-1 compliant)
- `src/services/playback/` — PlaybackScheduler, AudioOutput (clause queue, barge-in flush)
- `src/engines/output-evaluation/` — OutputEvaluationEngine (post-turn quality scoring)
- **End-to-end integration test:** inject fake audio → VAD → STT → CIL → LLM → TTS → playback assertion (full call path in test)
- Latency instrumentation test: assert p95 first-audio ≤ 1.5s on integration fixture

**Architecture:** V1 Ch9, Ch10, Ch12, Ch14, Ch18, Ch21, Ch22; V2 Ch15, Ch17; DocSuite-02, DocSuite-06, DocSuite-08

**Depends on:** Sprint-009, Sprint-010, Sprint-011

---

## Epic E4 — Reliability Infrastructure

**Goal:** Add the durable reliability layer beneath the walking skeleton: event sourcing, Redis hot state, persistent storage, crash recovery, idempotency, concurrency controls, and observability.

### Sprint-013: Event Bus & Redis Architecture
**Objective:** Implement the Event Bus (immutable ordered event stream with at-least-once delivery and DLQ) and the Redis layer (hot state, distributed locks with fencing tokens, rate limiting).

**Key deliverables:**
- `src/libs/event-bus/` — EventBus, Publisher, Consumer, DLQHandler
- DomainEvent schema enforcement; event_id as globally unique dedup key
- At-least-once delivery guarantee; consumer-side dedup by event_id
- Dead Letter Queue: events that exhaust retry budget → DLQ with alerting
- `src/libs/redis-client/` — RedisClient wrapper, distributed lock (fencing token), rate limiter
- Redis key naming convention (namespace:tenant_id:resource:id)
- TTL discipline: every key has a mandatory TTL; no stale accumulation
- Redis is NOT authoritative state (invariant enforced by tests)
- Integration tests: event publish→consume round-trip, dedup, DLQ routing, lock fencing

**Architecture:** V3 Ch3, Ch4; DocSuite-03

**Depends on:** Sprint-001, Sprint-002, Sprint-003

---

### Sprint-014: Persistent Storage — Schemas & Migrations
**Objective:** Implement all Postgres and MongoDB schemas, migration tooling (Alembic), and the data access repositories for every authoritative domain.

**Key deliverables:**
- Postgres schemas (Alembic migrations): `customers`, `loan_accounts`, `promises_to_pay`, `consents`, `idempotency_keys`, `audit_log`, `tenants`, `users`, `campaigns`, `billing_subscriptions`, `usage_events`
- MongoDB collections: `response_plans`, `decision_envelopes`, `call_transcripts`, `call_lineage`
- Object storage bucket definitions: audio recordings, exports
- Repository classes for each domain entity (CRUD + domain queries)
- Expand-contract migration discipline (additive changes only; no destructive migrations without ADR)
- Per-tenant data isolation enforced at query layer (AR-8)
- Data access integration tests against real Postgres + MongoDB (no mocks for persistence)

**Architecture:** V3 Ch5; V6 Ch7; DocSuite-03

**Depends on:** Sprint-002, Sprint-003

---

### Sprint-015: State Persistence, Crash Recovery & Idempotency
**Objective:** Implement Snapshot + event-tail replay for durable conversation state, the deterministic per-failure-class crash recovery protocol, and IdempotencyGuard.execute_once() for all authoritative effects.

**Key deliverables:**
- `src/libs/state/` — Snapshot, EventTailReplay, Recoverable (protocol mixin)
- Snapshot: periodic state snapshots to Postgres; replays event tail on recovery
- Crash recovery matrix: CPU restart → replay from last snapshot; GPU failure → graceful TTS fade + re-queue; Redis outage → degrade to Postgres; DB outage → halt new calls + hold existing; Twilio disconnect → 30s reconnect then end
- `src/libs/idempotency/` — IdempotencyGuard.execute_once(key, effect_fn): atomic "check + execute + record" using Postgres idempotency_keys table; fencing-token protected
- Consumer-side dedup for all event consumers
- Recovery integration tests: simulate each failure class and assert deterministic recovery + zero duplicate effects

**Architecture:** V3 Ch6, Ch7, Ch8; DocSuite-03

**Depends on:** Sprint-013, Sprint-014

---

### Sprint-016: Concurrency, Circuit Breakers, Health & Observability
**Objective:** Complete the reliability layer: single-writer concurrency architecture, bounded queues with backpressure, circuit breakers, health monitoring, service discovery, and the full observability stack (metrics, logs, traces).

**Key deliverables:**
- `src/libs/concurrency/` — per-call media thread, cognition worker pool, fair-share scheduler, backpressure signals, load-shedding (drop non-critical work first)
- `src/libs/queues/` — BoundedQueue (max_depth enforced; no unbounded growth), priority lanes, exponential-backoff retry, DLQ
- `src/libs/circuit-breaker/` — CircuitBreaker (CLOSED → OPEN → HALF_OPEN), per-service, configurable thresholds
- `src/libs/health/` — HealthCheck protocol, component-level health probes, readiness vs. liveness
- `src/libs/service-discovery/` — ServiceRegistry, endpoint resolution (Kubernetes-native)
- `src/libs/observability/` — Prometheus metrics (standard RED: requests, errors, duration), structured JSON logger (correlation_id, tenant_id, call_id on every log line), OpenTelemetry tracer (trace_id propagation end-to-end)
- Grafana dashboard definition files (SLO attainment, error-budget burn)
- Integration tests: circuit-breaker trip/reset, backpressure under simulated load, trace propagation end-to-end

**Architecture:** V3 Ch9, Ch10, Ch11, Ch12, Ch13, Ch14, Ch15, Ch16, Ch17; V7 Ch7; DocSuite-03, DocSuite-08

**Depends on:** Sprint-013, Sprint-014, Sprint-015

---

## Epic E5 — Compliance & Security

**Goal:** Implement the complete compliance and security layer: Policy Engine, regulatory compliance rules, authentication, authorization/RBAC, secrets management, encryption, privacy/PII, audit, and AI governance.

### Sprint-017: Policy Engine & Regulatory Compliance
**Objective:** Implement the central Policy Decision Point (PDP) that governs all authorization, compliance, and conversational policy decisions across the entire system.

**Key deliverables:**
- `src/services/policy-engine/` — PolicyEngine (unified PDP), PolicyDecision (PERMIT|DENY|REQUIRE|FORBID), PolicyRule, PolicySet
- Inheritance hierarchy: global → tenant → campaign → call-level rules
- Deny-overrides: any DENY from any level blocks the action
- Domain packs: Authorization, Compliance, AI Governance, Conversational Policy
- RBI fair-practice rules: calling hours, frequency limits, language, disclosure requirements
- DPDP rules: consent check before processing, right to erasure trigger
- Recording consent gate: call cannot proceed without verified consent
- Emergency/break-glass policy (supervisor override with full audit)
- Policy decision audit: every DENY or REQUIRE emits a PolicyDecisionEvent
- Unit tests: all RBI/DPDP rule scenarios, inheritance, deny-override precedence

**Architecture:** V4 Ch2, Ch4; DocSuite-03, DocSuite-05

**Depends on:** Sprint-001, Sprint-002, Sprint-013, Sprint-014

---

### Sprint-018: Authentication, Authorization/RBAC & AI Governance
**Objective:** Implement the complete auth layer (OAuth2/OIDC/JWT for users, mTLS for services, API keys), the RBAC+ABAC authorization engine, and the AI Governance layer (Law-of-Authority enforcement, GovernanceVerdict).

**Key deliverables:**
- `src/services/auth/` — JWTValidator, OIDCProvider integration, mTLS enforcer, APIKeyValidator
- Service-to-service mTLS (all internal calls require mutual TLS; no plaintext)
- `src/services/authz/` — RBACEngine (roles: ADMIN, SUPERVISOR, MANAGER, AGENT, AUDITOR), ABAC attribute evaluation, JIT temporary privilege grants, approval workflow
- Tenant isolation invariant: every authz check includes tenant_id scope (AR-8 enforcement)
- `src/services/ai-governance/` — GovernanceLayer, GovernanceVerdict (APPROVE|REQUIRE_HUMAN|BLOCK), Law-of-Authority checker (LLM output vs. ResponsePlan.facts), explainability from DecisionEnvelope lineage
- AI Governance is called on every LLM output before it reaches TTS (mandatory gate)
- Unit tests: RBAC coverage (all role×action combinations), tenant isolation enforcement, Law-of-Authority rejection on hallucination

**Architecture:** V4 Ch3, Ch5, Ch6; DocSuite-02, DocSuite-05

**Depends on:** Sprint-017

---

### Sprint-019: Secrets Management, Encryption & Privacy Architecture
**Objective:** Implement vault-based secrets management, the complete encryption architecture (at-rest AES-256-GCM, in-transit TLS/SRTP/mTLS, envelope encryption, crypto-shredding), and the privacy architecture (data minimization, purpose limitation, right to erasure).

**Key deliverables:**
- `src/libs/secrets/` — SecretsManager (vault integration: HashiCorp Vault or AWS Secrets Manager), runtime injection only (no secrets in config files or env vars), rotation with grace window, emergency revocation
- `src/libs/encryption/` — EncryptionService: AES-256-GCM (at rest), TLS 1.3 enforcement, SRTP for audio, envelope encryption (DEK encrypted by KEK in KMS), crypto-shredding (DEK deletion = data erasure)
- `src/libs/privacy/` — PrivacyEngine: purpose-limitation registry, data minimization filter (collect only what consent permits), DataErasureJob (crypto-shred DEK + tombstone record)
- PII field-level encryption for sensitive Postgres columns (DPDP requirement)
- Right-to-erasure workflow: request → verify consent revocation → crypto-shred DEK → audit record
- Integration tests: encryption round-trip, DEK rotation, erasure + verification, secrets rotation without downtime

**Architecture:** V4 Ch7, Ch8, Ch9; DocSuite-05

**Depends on:** Sprint-017, Sprint-018

---

### Sprint-020: PII Protection, Audit, API Security & AI Safety
**Objective:** Complete the compliance and security layer: PII detection/redaction, the immutable audit trail, API security hardening, runtime security, and AI safety controls.

**Key deliverables:**
- `src/libs/pii/` — PIIDetector (entity recognition: Aadhaar, PAN, phone, account number, amount), PIIRedactor (mask/tokenize for logs and non-authoritative storage), PIITokenizer (reversible with access control)
- `src/libs/audit/` — AuditLogger (immutable, append-only, tamper-evident), AuditEvent schema, mandatory audit for: auth events, policy decisions, data access, PTP creation, consent changes, AI governance verdicts
- API security: rate limiting per tenant/user, input validation (no injection vectors), CORS policy, security headers (HSTS, CSP)
- Runtime security: container image scanning integration, no privileged containers, read-only root filesystem, network policy (deny-all default + explicit allow-list)
- AI safety: content moderation gate (blocks abusive/out-of-scope LLM output), prompt injection detection, output length and format validation
- Human oversight: REQUIRE_HUMAN verdict routing to supervisor queue, supervisor review dashboard stub
- Security integration tests: PII redaction coverage, audit trail completeness, API injection attempts blocked

**Architecture:** V4 Ch10, Ch11, Ch12, Ch13, Ch14; DocSuite-05, DocSuite-08

**Depends on:** Sprint-018, Sprint-019

---

## Epic E6 — SaaS Platform

**Goal:** Build the complete multi-tenant SaaS platform: tenant management, CRM, collections system of record, campaign management, contact center, billing/metering, analytics, admin portal, and public API.

### Sprint-021: Multi-Tenancy, Tenant Lifecycle & User Management
**Objective:** Implement the multi-tenant foundation: isolation profiles, tenant hierarchy, the tenant lifecycle state machine, and user/organization management.

**Key deliverables:**
- `src/services/tenant-management/` — TenantService, TenantLifecycle (TRIAL→SANDBOX→PRODUCTION→SUSPENDED→CANCELLED→DELETING→DELETED), IsolationProfile (ROW_LEVEL | SCHEMA | DEDICATED_DB)
- Org hierarchy: Organization → BusinessUnit → Branch (3-level)
- Tenant provisioning workflow: create → configure isolation → provision resources → activate
- Tenant suspension workflow: suspend → drain calls → freeze data
- Tenant deletion: async deletion job with crypto-shredding per privacy architecture
- `src/services/user-management/` — UserService, RoleAssignment (scoped to org hierarchy), invitation workflow, SSO integration stub
- Tenant isolation tests: cross-tenant data access attempts must fail

**Architecture:** V5 Ch2, Ch3, Ch8; DocSuite-03

**Depends on:** Sprint-018, Sprint-020

---

### Sprint-022: CRM & Loan/Collections Management
**Objective:** Implement the authoritative CRM (party data) and the collections system of record (loan accounts, EMI schedules, DPD tracking, PTPs, settlements, callbacks).

**Key deliverables:**
- `src/services/crm/` — CustomerService, PartyRepository (borrower/co-borrower/guarantor), CustomerContext assembler (builds the CustomerContext delivered to CIL)
- `src/services/collections/` — LoanAccountService, EMIScheduleRepository, PromiseToPayService (idempotent PTP creation using IdempotencyGuard), SettlementService, CallbackScheduler, EscalationWorkflow
- CustomerContext: assembled from CRM + loan data; authoritative (Law of Authority); immutable once assembled for a call
- DPD (Days Past Due) calculation: real-time from EMI schedule
- PTP workflow: create → notify → track → close (fulfilled/broken)
- Settlement workflow: offer → accept → authorize → disburse (with Policy Engine check at each step)
- Integration tests: PTP idempotency (same request twice = one record), CustomerContext assembly correctness

**Architecture:** V5 Ch4, Ch5; DocSuite-03

**Depends on:** Sprint-021, Sprint-014, Sprint-015

---

### Sprint-023: Campaign Management & Contact Center Platform
**Objective:** Implement campaign orchestration (audience targeting, RBI-compliant scheduling, A/B testing) and the contact center platform (AI+human blended, live transfer, supervisor controls).

**Key deliverables:**
- `src/services/campaign-management/` — CampaignService, AudienceSelector, ScheduleEngine (RBI calling hours enforced), RetryPolicyEngine, ABTestingFramework (voice/strategy variant assignment)
- Campaign lifecycle: DRAFT→REVIEW→APPROVED→ACTIVE→PAUSED→COMPLETED→ARCHIVED
- Audience selection: SQL-based cohort builder (DPD range, amount band, language preference, consent status)
- `src/services/contact-center/` — ContactCenterService, SkillsBasedRouter, LiveTransferService, SupervisorService (join/monitor/barge-in with full context handoff)
- Human agent context handoff: full call summary + DecisionEnvelope + CustomerContext delivered to agent screen
- Supervisor barge-in: real-time audio inject + live transcript + AI suggestion panel
- Integration tests: campaign scheduling respects RBI hours, live transfer preserves context

**Architecture:** V5 Ch6, Ch7; DocSuite-03

**Depends on:** Sprint-022

---

### Sprint-024: Billing Platform, Usage Metering & Analytics
**Objective:** Implement the billing platform (subscription + usage-based + enterprise contracts), real-time usage metering, and the analytics and reporting platform.

**Key deliverables:**
- `src/services/billing/` — BillingService, SubscriptionManager (TRIAL/GROWTH/ENTERPRISE tiers), UsageEntitlementEngine (via Policy Engine), InvoiceGenerator, PaymentProcessor integration stub
- `src/services/metering/` — MeteringService (real-time meter events: call-minutes, STT tokens, LLM tokens, GPU-seconds, storage-GB), UsageAggregator, UsageLimitEnforcer (blocks usage when entitlement exhausted)
- `src/services/analytics/` — AnalyticsService, CallAnalytics (per-call: outcome, duration, intent sequence, negotiation result, sentiment arc), CampaignAnalytics (conversion, contactability, promise rate)
- `src/services/reporting/` — ReportingService, ScheduledReportEngine, ExportService (CSV/XLSX/PDF)
- Integration tests: usage accumulation correctness, billing calculation, entitlement enforcement

**Architecture:** V5 Ch9, Ch10, Ch11, Ch12; DocSuite-03

**Depends on:** Sprint-021, Sprint-022

---

### Sprint-025: Admin Portal, AI Configuration & Integration Platform
**Objective:** Complete the SaaS platform with the administration portal, AI configuration platform (prompt management, model config), and the integration/API platform (webhooks, public REST API, SDK).

**Key deliverables:**
- `src/services/admin-portal/` — Admin API backend (tenant config, user management, billing, campaign approval, audit log viewer, AI config management)
- `src/services/ai-config/` — PromptVersioningService (versioned prompt templates with hash-pinning), ModelConfigService (adapter selection, temperature, inference params per tenant/campaign), EvaluationRunService
- `src/services/integration-platform/` — WebhookService (event fanout: call-completed, PTP-created, etc.), WebhookDeliveryEngine (with retry + signature verification)
- `src/services/api-platform/` — Public REST API (OpenAPI 3.1 spec-first), API versioning (URL path: /v1/), SDK generation stubs
- API security: tenant-scoped API keys, rate limiting per plan, signed webhooks
- Integration tests: webhook delivery with retry, API key scoping, prompt version immutability

**Architecture:** V5 Ch13, Ch14, Ch15, Ch16; V4 Ch5, Ch12; DocSuite-04, DocSuite-05

**Depends on:** Sprint-021, Sprint-023, Sprint-024

---

## Epic E7 — Production Alpha

**Goal:** Stand up the complete production infrastructure, wire up monitoring and alerting, execute performance validation (latency budget), load testing, security penetration testing, and compliance validation. Deploy to production alpha.

### Sprint-026: Infrastructure as Code & Kubernetes Architecture
**Objective:** Implement the complete Infrastructure as Code (Terraform/Helm/Kubernetes) and the production Kubernetes architecture — node pools, GPU scheduling integration, resource guarantees.

**Key deliverables:**
- `infra/terraform/` — VPC, subnets, security groups, GPU instance groups, managed Postgres (RDS/Cloud SQL), managed Redis (ElastiCache/Memorystore), object storage, KMS keys
- `infra/helm/` — Helm charts for every service: media-gateway, audio-preprocessing, vad-endpointing, gpu-scheduler, stt, llm-runtime, tts, conversation-engine, policy-engine, auth, crm, collections, campaign, billing, analytics, admin-api
- Kubernetes node pools: CPU/media nodes (burstable), GPU nodes (tainted, dedicated), data nodes, system nodes
- K8S resource guarantees: Guaranteed QoS for all hot-path services (K8S-1)
- GPU nodes: tainted; only GPU Scheduler pods schedule there (K8S-2)
- Pod Disruption Budgets: no single-point-of-failure for hot-path services
- Network policies: deny-all default; explicit allow-list per service
- Integration tests: IaC plan validates, Helm chart renders clean

**Architecture:** V7 Ch2, Ch3, Ch5; DocSuite-09

**Depends on:** Sprint-016, Sprint-020, Sprint-025

---

### Sprint-027: Monitoring, Alerting, Logging, Tracing & Disaster Recovery
**Objective:** Wire up the full production observability stack (Prometheus, Grafana, Alertmanager, structured logging, OpenTelemetry tracing), multi-window burn-rate alerting, and the disaster recovery / business continuity procedures.

**Key deliverables:**
- Prometheus scrape configs for all services (standard RED metrics + business metrics)
- Grafana dashboards: SLO attainment (first-audio p95, availability), error-budget burn, GPU utilization, per-service latency histograms, call funnel (connect→VAD→STT→CIL→TTS→playback)
- Alertmanager routing: CRITICAL (page on-call <5 min), WARNING (ticket auto-created), INFO (Slack channel)
- Multi-window burn-rate alerts: 1h + 6h windows for first-audio SLO, availability SLO
- Centralized structured log aggregation (ELK/Loki): correlation_id searchable, PII redacted in logs
- OpenTelemetry traces ingested (Jaeger/Tempo): end-to-end trace per call, per-stage spans
- Disaster recovery: Postgres PITR (RPO ≤ 5 min), Redis AOF + RDB snapshot, multi-AZ failover, RTO ≤ 30 min runbook
- Autoscaling: HPA for CPU services, custom metrics autoscaling for GPU pools
- DR integration test: simulate AZ failure → assert recovery within RTO

**Architecture:** V7 Ch7, Ch8, Ch9, Ch10, Ch13, Ch14; V3 Ch15–17; DocSuite-09

**Depends on:** Sprint-026

---

### Sprint-028: Performance Validation, Load Testing, Security Pen Testing & Production Alpha Deploy
**Objective:** Execute all production-readiness gates: latency validation (first-audio p95 ≤ 1.5s), load testing (target call concurrency), chaos engineering, security penetration testing, compliance validation (RBI/DPDP), and deploy the production alpha.

**Key deliverables:**
- **Latency validation:** per-stage instrumented test against real AI models (media GW → preprocessing → VAD → STT → CIL → LLM → TTS → playback); assert p95 ≤ 1.5s; identify and fix any budget overruns
- **Load test:** simulate target concurrent calls (500 simultaneous); assert no degradation >10% in first-audio p95; assert GPU utilization ≤ 0.80
- **Chaos engineering:** kill GPU node → assert graceful failover; kill Redis → assert degraded-mode continuation; introduce 20% packet loss → assert PLC compensates
- **Security penetration test:** external pen test (OWASP top 10, tenant isolation breach attempts, prompt injection, API key brute-force); fix all CRITICAL and HIGH findings
- **Compliance validation:** RBI calling hours enforcement test, DPDP consent gate test, audit trail completeness test, data retention test
- **Production alpha deployment:** canary deploy (5% → 25% → 50% → 100%) with auto-rollback gate
- Evaluation report: `evaluation/production-alpha-report.md`

**Architecture:** V1 Ch23; V7 Ch4, Ch7, Ch15, Ch20; V4 Ch21; DocSuite-09, DocSuite-10

**Depends on:** Sprint-026, Sprint-027, Sprint-022, Sprint-023

---

## Epic E8 — Founder Validation → Pilot → Production Release

**Goal:** Validate conversation quality with domain experts, run a controlled pilot with live accounts, then execute the full production release with complete operational readiness.

### Sprint-029: Founder Validation
**Objective:** Structured expert review of real AI conversations for collections appropriateness, tone, compliance, factual grounding, and negotiation quality. Gate for pilot approval.

**Key deliverables:**
- Run 50+ calls on the production alpha system against test accounts
- Structured review rubric (per DocSuite-10 AI Evaluation Handbook): intent accuracy, Law-of-Authority compliance (zero unauthorized effects), negotiation within envelope, tone/empathy score, RBI compliance, MOS audio quality, first-audio latency
- Founder sign-off document: named reviewers, scores, pass/fail verdict per dimension
- **Mandatory pass criteria:** zero Law-of-Authority violations, zero RBI compliance failures, negotiation within envelope 100%, MOS ≥ 3.5, first-audio p95 ≤ 1.5s
- Remediation loop for any failures: fix → re-evaluate → re-sign-off
- `evaluation/founder-validation-report.md`

**Architecture:** V2 (all chapters for quality review); DocSuite-07, DocSuite-10

**Depends on:** Sprint-028

---

### Sprint-030: Pilot Deployment
**Objective:** Controlled live-production pilot: 10–20 real borrower accounts, live calls, real-time monitoring, rapid issue triage. Exit criteria before full production release.

**Key deliverables:**
- Pilot cohort selection (low-risk, consent-verified, single campaign)
- On-call rotation + incident runbook for pilot period
- Real-time supervisor dashboard: live call monitoring, intervention capability
- Daily pilot report: call outcomes, issues, latency, SLO compliance
- Issue triage SLA: CRITICAL → fix in 2h, HIGH → fix in 24h, MEDIUM → sprint backlog
- Exit criteria (all must pass): ≥95% calls complete without system intervention; SLO met (first-audio p95 ≤ 1.5s, availability ≥ 99.95%); zero regulatory violations; zero data breaches; PTP conversion rate within expected range
- `evaluation/pilot-report.md`

**Architecture:** V7 Ch1, Ch16, Ch18; DocSuite-09, DocSuite-10

**Depends on:** Sprint-029

---

### Sprint-031: Production Release
**Objective:** Full production launch — unrestricted tenant onboarding, public API live, all SLO dashboards operational, runbooks finalized (including governance and enterprise ops runbooks), support processes active, launch communications complete.

**Key deliverables:**
- Production release deployment (canary complete, traffic 100%)
- SLO dashboards live and error-budget alerting active
- Customer-facing API documentation published
- Support runbooks: incident response playbook, on-call escalation, common failure modes
- Governance runbooks: data-breach response, privacy-incident, AI misbehavior, credential compromise, compliance violation (V4 Ch22)
- Enterprise operations guides: dedicated-cluster-ops, premium-sla-delivery, air-gapped-deployment (V7 Ch22)
- Tenant onboarding guide (self-serve and assisted)
- Release notes published
- CHANGELOG.md updated to v1.0.0
- `PROJECT_STATUS.md` updated: phase → Production; sprints → 34/34 complete

**Architecture:** V4 Ch22; V7 Ch16, Ch17, Ch21, Ch22; DocSuite-09

**Depends on:** Sprint-030, Sprint-032, Sprint-033, Sprint-034

---

## Epic E6-Extended — Enterprise Platform & Advanced SaaS

**Goal:** Complete the enterprise tier of the SaaS platform with full SSO/SCIM identity federation, multi-region data residency, workflow automation, and customer success tooling. These sprints can run in parallel with E7/E8 after Sprint-025 is complete.

### Sprint-032: Enterprise Platform — SSO, SCIM & Multi-Region
**Objective:** Implement the full SAML/OIDC identity federation, SCIM 2.0 user provisioning/deprovisioning, tamper-evident audit export pipeline, multi-region data residency enforcement, and dedicated-cluster provisioning for ENTERPRISE tier customers.

**Key deliverables:**
- `src/services/enterprise-platform/sso/` — SAMLIdentityProvider, OIDCProvider, IdentityFederation (claim → role mapping)
- `src/services/enterprise-platform/scim/` — SCIMService (SCIM 2.0 REST API), UserProvisioner, GroupSyncer
- `src/services/enterprise-platform/audit_export/` — AuditExportPipeline, ExportSigner (HMAC-signed NDJSON)
- `src/services/enterprise-platform/data_residency/` — DataResidencyEnforcer, RegionRouter
- `src/services/enterprise-platform/cluster_provisioning/` — ClusterProvisioningService (dedicated K8s cluster for ENTERPRISE)
- Integration tests: SAML SP flow, SCIM provisioning, cross-region write rejection

**Architecture:** V5 Ch22; V7 Ch22; V4 Ch3, Ch5, Ch11

**Depends on:** Sprint-025, Sprint-021, Sprint-018, Sprint-019, Sprint-020, Sprint-026

---

### Sprint-033: Workflow Automation & Customer Success Platform
**Objective:** Implement the event-driven workflow automation engine (trigger-condition-action, durable, idempotent, with approval flows) and the customer success platform (tenant health scoring, churn prediction, onboarding tracking, NPS/CSAT collection).

**Key deliverables:**
- `src/services/workflow-engine/` — WorkflowEngine, WorkflowExecutor (durable, restart-safe), TriggerEvaluator (EventBus subscriber), ActionDispatcher, ApprovalFlowAdapter (integrates HITLQueue), WorkflowBuilderAPI
- `src/services/customer-success/` — CustomerSuccessService, OnboardingTracker, TenantHealthScorer, ChurnPredictor, NPSCollector
- Integration tests: workflow execution survives process restart, approval flow connects to HITL queue

**Architecture:** V5 Ch17, Ch18; V3 Ch3; V4 Ch15

**Depends on:** Sprint-025, Sprint-013, Sprint-022, Sprint-023, Sprint-024, Sprint-032

---

## Epic E3-Extended — Conversation Learning Layer

**Goal:** Close the offline learning loop by mining production calls for failures, generating training data, tracking model drift, and safely promoting improved models using the same quality gate as Founder Validation.

### Sprint-034: Conversation Learning Layer
**Objective:** Implement the offline learning pipeline: FailedTurnMiner, WeakLabelGenerator, ModelDriftTracker, OfflineTrainingPipeline, and ModelPromoter with A/B test + PromotionGate.

**Key deliverables:**
- `src/services/learning-layer/mining/` — FailedTurnMiner, MiningCriteria
- `src/services/learning-layer/labeling/` — WeakLabelGenerator, LabelSchema
- `src/services/learning-layer/drift/` — ModelDriftTracker, DriftAlerter
- `src/services/learning-layer/training/` — OfflineTrainingPipeline, TrainingDataset
- `src/services/learning-layer/promotion/` — ModelPromoter, PromotionGate (same rubric as Sprint-029)
- Integration test: full pipeline mine → label → train → evaluate → promote/rollback

**Architecture:** V2 Ch18; V6 Ch9; DocSuite-10

**Depends on:** Sprint-010, Sprint-012, Sprint-029

---

## Risk Register

| ID | Risk | Prob | Impact | Mitigation Sprint |
|---|---|---|---|---|
| R-1 | GPU concurrency / cost-per-call exceeds budget | High | High | Sprint-008, Sprint-028 |
| R-2 | First-audio latency > 1.5s under load | Med | High | Sprint-012, Sprint-028 |
| R-3 | RBI/DPDP compliance gap discovered post-launch | Med | Critical | Sprint-017, Sprint-028 |
| R-4 | Tenant isolation breach | Low | Critical | Sprint-020, Sprint-028 |
| R-5 | Scope overrun for lean team | Med | Med | Walking-skeleton-first (Sprint-012 delivers first call) |
| R-6 | LLM hallucination violating Law of Authority | Med | Critical | Sprint-012, Sprint-018 (AI Governance), Sprint-029 |
| R-7 | Model adapter coupling prevents swap | Low | Med | AR-16 (model-agnostic adapters), Sprint-009 |

---

## Dependency Summary

```
E1 (001-003) → E2 (004-008) → E3 (009-012)
                                 ↓
                              E4 (013-016)
                                 ↓
                              E5 (017-020)
                                 ↓
                              E6 (021-025)
                                 ↓
                              E7 (026-028)
                                 ↓
                              E8 (029-030) ──────────────────────────────────► Sprint-031 (FINAL)
                                                                                    ↑
                              E6-Ext (032-033) ──────────────────────────────────► │
                              E3-Ext (034) ────────────────────────────────────────┘
```

- E4 and E5 can begin in parallel with E3 once Sprint-012 contracts are stable (after Sprint-001/002)
- E6 depends on E4+E5; E7 depends on E4+E5+E6; E8 depends on E7
- E6-Ext (Sprint-032, Sprint-033) can begin after Sprint-025 and Sprint-026 are complete — runs in parallel with E8 (Sprint-029/030)
- E3-Ext (Sprint-034) can begin after Sprint-010, Sprint-012, and Sprint-029 are complete
- Sprint-031 (Production Release) is the convergence point for E8 + E6-Ext + E3-Ext — all must complete before Sprint-031
