# VoiceOS v2 — Documentation Suite

## Document 2 — Interface Contracts

**Type:** Canonical interface reference (documentation layer over Volumes 1–7)
**Status:** Living reference · **Owner:** Chief Architect / per-interface owners (below)
**Authority:** Volumes 1–7 are immutable + canonical. This document **records** the public interfaces already defined in the volumes; it does not introduce, modify, or redesign any interface. Signatures are reproduced from their source volume; the volume is authoritative. Citations: `V<n> Ch<c>`.

> **Per-interface template:** Purpose · Inputs · Outputs · Methods · Error Codes · Version · Backward Compatibility · Dependencies · Ownership. Error codes follow the uniform error shape (stable `code` enums, no internal leakage) per V6 Ch5 / V5 Ch16. Versioning follows SemVer (V6 GIT-5); public APIs are URL-versioned (V6 API-DS-3). Backward-compatibility rule throughout: additive-only within a version; breaking changes → new version + deprecation window.

---

# Part A — Runtime Interfaces (Volume 1 / Volume 2)

## A.1 Media Gateway
- **Purpose.** Real-time telephony ingress/egress; RTP transport, jitter handling, μ-law/A-law. *Source: V1 Ch3–4.*
- **Inputs.** Carrier/RTP packets, call control (connect/teardown).
- **Outputs.** Normalized audio frames inbound; synthesized audio outbound.
- **Methods.** `ingest(rtp_packet) -> AudioFrame`; `play(audio_chunk) -> None`; `on_call_start/on_call_end(call) -> None`.
- **Error Codes.** `TRANSPORT_UNAVAILABLE`, `CODEC_UNSUPPORTED`, `JITTER_OVERFLOW`, `CALL_TEARDOWN`.
- **Version.** v1. **Backward Compat.** Frame contract (`AudioFrame`, V1 App.A) additive-only.
- **Dependencies.** Carrier/Twilio; Audio Pipeline; RI-1 (no blocking).
- **Ownership.** Voice Runtime.

## A.2 Dialogue Manager
- **Purpose.** Normalize a turn into `TurnInput` for the intelligence layer; manage turn-taking. *Source: V1 Ch9 / V2 Ch9.*
- **Inputs.** Transcribed speech (STT), VAD/endpoint signals, call context.
- **Outputs.** `TurnInput`.
- **Methods.** `build_turn(stt_result, context) -> TurnInput`; `on_endpoint() -> None`; `on_barge_in() -> None`.
- **Error Codes.** `TURN_INCOMPLETE`, `CONTEXT_UNAVAILABLE`.
- **Version.** v1. **Backward Compat.** `TurnInput` additive-only.
- **Dependencies.** STT (V1 Ch8), VAD (V1 Ch6), CustomerContext (V1 Ch11).
- **Ownership.** Conversation Intelligence.

## A.3 Conversation Engine
- **Purpose.** Deterministic owner of state/business/authority logic; produces the `ResponsePlan`. Nothing bypasses it (AR-1). *Source: V1 Ch10 / V2.*
- **Inputs.** `TurnInput`, `CustomerContext`, policy + engine outputs.
- **Outputs.** Sealed `ResponsePlan` (AR-4); emits `DecisionEnvelope` (AR-5).
- **Methods.** `plan(turn: TurnInput) -> ResponsePlan`; `decide(...) -> DecisionEnvelope`.
- **Error Codes.** `PLANNING_FAILED`, `POLICY_DENIED`, `RISK_VETO`, `AUTHORITY_UNAVAILABLE`.
- **Version.** v1. **Backward Compat.** `ResponsePlan`/`DecisionEnvelope` additive-only (protected, V6 Ch11).
- **Dependencies.** Engines (Intent/Strategy/Negotiation/Risk/Empathy, V2), Policy Engine (V4 Ch4), CustomerContext, Memory.
- **Ownership.** Conversation Intelligence (+ Architecture Board for the contracts).

## A.4 Memory Service
- **Purpose.** Working Memory (bounded, RI-3) + Relationship Memory (system-of-record-backed view). *Source: V2 Ch10.*
- **Inputs.** Turn events, customer identity.
- **Outputs.** `WorkingMemory`, `RelationshipMemory` (carry no authority).
- **Methods.** `working(call_id) -> WorkingMemory`; `relationship(customer_id) -> RelationshipMemory`; `append(event) -> None`.
- **Error Codes.** `MEMORY_UNAVAILABLE`, `BOUND_EXCEEDED` (handled by RI-3 eviction).
- **Version.** v1. **Backward Compat.** Memory schemas additive-only.
- **Dependencies.** Redis hot-state (V3 Ch4), CRM/Collections (V5 Ch4–5).
- **Ownership.** Conversation Intelligence.

## A.5 Prompt Builder
- **Purpose.** Deterministically compose the LLM prompt from a sealed plan (RI-7). *Source: V1 Ch12.*
- **Inputs.** Sealed `ResponsePlan`, prompt version (V2 Ch19).
- **Outputs.** `ComposedPrompt` (deterministic; hashable).
- **Methods.** `build(plan, version) -> ComposedPrompt`; `prompt_hash(...) -> Hash`.
- **Error Codes.** `NONDETERMINISTIC_INPUT`, `PROMPT_VERSION_INVALID`, `CONTEXT_OVERFLOW`.
- **Version.** v1. **Backward Compat.** Determinism contract immutable (AR-13); prompt versions managed via V5 Ch14.
- **Dependencies.** Prompt versions (V2 Ch19 / DocSuite-06), CustomerContext.
- **Ownership.** Conversation Intelligence / AI Engineering.

## A.6 GPU Scheduler
- **Purpose.** Admission-control GPU/VRAM so OOM is impossible by construction (RI-8). *Source: V1 Ch7.*
- **Inputs.** Inference requests (latency class), VRAM ledger state.
- **Outputs.** Admission decision + dispatch, or shed → fallback (V3 Ch13).
- **Methods.** `admit(request) -> Admission`; `dispatch(request) -> InferenceHandle`; `vram_state(node) -> VRAMState`.
- **Error Codes.** `ADMISSION_SHED`, `VRAM_EXHAUSTED` (→ shed, never OOM), `EXECUTOR_UNAVAILABLE`.
- **Version.** v1. **Backward Compat.** Admission contract stable; OOM-by-construction invariant.
- **Dependencies.** GPU fleet (V7 Ch6), STT/LLM/TTS executors.
- **Ownership.** Voice Runtime / Platform.

## A.7 LLM Runtime
- **Purpose.** Serve LLM inference (vLLM, Qwen2.5-7B-Instruct-FP8) with continuous batching + KV/prefix cache. *Source: V1 Ch13.*
- **Inputs.** `ComposedPrompt`, decoding params.
- **Outputs.** Streamed tokens (TTFT-bounded).
- **Methods.** `generate(prompt, params) -> TokenStream`.
- **Error Codes.** `INFERENCE_TIMEOUT`, `CONTEXT_OVERFLOW`, `EXECUTOR_ERROR` (→ fallback V3 Ch13).
- **Version.** v1; model versions via approved catalog (V5 Ch14). **Backward Compat.** Model upgrades eval-gated (V6 AI-DS-7).
- **Dependencies.** GPU Scheduler (V1 Ch7), Prompt Builder.
- **Ownership.** AI Engineering / Voice Runtime.

## A.8 Output Validator / Output Evaluation
- **Purpose.** The mandatory gate every utterance passes before TTS; never bypassed (AR-6). V1 runtime validator; V2 evaluation ≥ Policy strictness. *Source: V1 Ch14 / V2 Ch17.*
- **Inputs.** Candidate response, `ResponsePlan`, policy.
- **Outputs.** Validated text or rejection (→ re-plan/fallback).
- **Methods.** `validate(response, plan) -> ValidationResult`.
- **Error Codes.** `OUTPUT_REJECTED`, `POLICY_VIOLATION`, `OUT_OF_ENVELOPE`, `SAFETY_BLOCK`.
- **Version.** v1. **Backward Compat.** Gate is mandatory + cannot be weakened.
- **Dependencies.** Policy Engine (V4 Ch4), AI Safety (V4 Ch14).
- **Ownership.** Conversation Intelligence / Security & Governance.

## A.9 Speech Rendering / TTS
- **Purpose.** Normalize text + synthesize speech (Veena TTS, 24 kHz WS) with prosody/emotion. *Source: V1 Ch15–20.*
- **Inputs.** Validated text, voice profile, emotion/prosody.
- **Outputs.** Streamed audio chunks (first-chunk-latency bounded).
- **Methods.** `normalize(text) -> NormalizedText`; `synthesize(text, voice, prosody) -> AudioStream`.
- **Error Codes.** `TTS_UNAVAILABLE`, `NORMALIZATION_ERROR`, `WS_DISCONNECT` (→ fallback voice).
- **Version.** v1; voices via catalog (V5 Ch14). **Backward Compat.** Voice profiles additive; style per DocSuite-07.
- **Dependencies.** GPU Scheduler, Voice Style (V1 Ch16), Playback Scheduler.
- **Ownership.** Voice Runtime.

## A.10 Voice Style
- **Purpose.** Define tone/prosody/emotion settings for synthesis (clamped). *Source: V1 Ch16; canonical guide DocSuite-07.*
- **Inputs.** Voice profile, emotion state (V2 Ch13).
- **Outputs.** `ToneSettings` (clamped to safe ranges).
- **Methods.** `resolve_style(profile, emotion) -> ToneSettings`.
- **Error Codes.** `STYLE_INVALID` (clamped, not failed).
- **Version.** v1. **Backward Compat.** Style ranges stable; guide is canonical (DocSuite-07).
- **Dependencies.** Emotion State (V2 Ch13), TTS.
- **Ownership.** Voice Runtime / AI Engineering.

## A.11 Playback Scheduler
- **Purpose.** Sequence synthesized audio to the caller; own output coherence (RI-6) + played-offset checkpoint. *Source: V1 Ch21–22.*
- **Inputs.** Audio stream, barge-in signals.
- **Outputs.** Ordered playback; played-offset.
- **Methods.** `enqueue(chunk) -> None`; `stop_for_barge_in() -> PlayedOffset`; `played_offset() -> PlayedOffset`.
- **Error Codes.** `PLAYBACK_UNDERRUN`, `OUT_OF_ORDER` (prevented by RI-6).
- **Version.** v1. **Backward Compat.** Coherence contract stable.
- **Dependencies.** Media Gateway, TTS.
- **Ownership.** Voice Runtime.

---

# Part B — Reliability Interfaces (Volume 3)

## B.1 Redis / Hot State
- **Purpose.** Fast, non-authoritative working state; always rehydratable (DM-1). *Source: V3 Ch4.*
- **Inputs/Outputs.** Hot-state reads/writes (call state, locks, rate-limit counters).
- **Methods.** `get/set/del(key, tenant)`; `lock(key)`; `rate_limit(key)`.
- **Error Codes.** `REDIS_UNAVAILABLE` (→ rehydrate V3 Ch7), `LOCK_CONTENTION`.
- **Version.** v1. **Backward Compat.** Never authoritative; key schema additive.
- **Dependencies.** Cluster (failover V3 Ch13).
- **Ownership.** Platform/SRE.

## B.2 Event Bus / Event Log
- **Purpose.** Append-only immutable event log; the lineage/replay/audit/analytics substrate. *Source: V3 Ch3.*
- **Inputs.** `EventEnvelope` (typed, tenant-scoped, lineage-linked).
- **Outputs.** Durable, ordered, replayable events.
- **Methods.** `emit(event: EventEnvelope) -> None`; `subscribe(type, handler)`; `replay(range) -> Stream`.
- **Error Codes.** `SCHEMA_INVALID`, `APPEND_FAILED`, `ORDER_VIOLATION`.
- **Version.** v1; event schemas versioned (EV-4/5). **Backward Compat.** Additive-only; old events readable forever.
- **Dependencies.** Persistence (V3 Ch5).
- **Ownership.** Platform/SRE (+ Architecture Board for envelope).

## B.3 Persistence
- **Purpose.** Authoritative relational store (Postgres). *Source: V3 Ch5.*
- **Inputs/Outputs.** Authoritative records (loans, PTP, consent, …).
- **Methods.** `read/write(entity, tenant)`; transactional ops; migrations (expand-contract, DM-6).
- **Error Codes.** `DB_UNAVAILABLE`, `CONSTRAINT_VIOLATION`, `REPLICATION_LAG`, `TENANT_PREDICATE_MISSING` (bug, AR-8).
- **Version.** v1; schema via migrations. **Backward Compat.** Expand-contract; reversible.
- **Dependencies.** Backup/DR (V3 Ch18).
- **Ownership.** Platform/SRE + domain owners.

## B.4 Recovery
- **Purpose.** Deterministic crash/failure recovery via replay; re-read authoritative facts (RI-5). *Source: V3 Ch7.*
- **Inputs.** Event log, checkpoints, played-offset.
- **Outputs.** Restored state (no duplicate/lost effects).
- **Methods.** `recover(call_id) -> RecoveredState`; `reconcile(idempotency_key)`.
- **Error Codes.** `RECOVERY_FAILED`, `LINEAGE_GAP`.
- **Version.** v1. **Backward Compat.** Replay determinism stable.
- **Dependencies.** Event log, Idempotency (V3 Ch8).
- **Ownership.** Platform/SRE.

## B.5 Queue Manager
- **Purpose.** Bounded queues, retry/backoff, DLQ; at-least-once delivery. *Source: V3 Ch10.*
- **Inputs.** Command messages.
- **Outputs.** Delivered (idempotent consumer) or dead-lettered.
- **Methods.** `enqueue(msg)`; `consume(handler)`; `dlq()`.
- **Error Codes.** `QUEUE_FULL` (bounded, RI-3), `POISON_MESSAGE` (→ DLQ), `DELIVERY_FAILED`.
- **Version.** v1. **Backward Compat.** Message schemas additive.
- **Dependencies.** Idempotency (V3 Ch8).
- **Ownership.** Platform/SRE.

## B.6 Health APIs
- **Purpose.** Liveness/readiness/health for gating + orchestration. *Source: V3 Ch12.*
- **Outputs.** Health status per component.
- **Methods.** `liveness()`; `readiness()`; `health()`.
- **Error Codes.** `UNHEALTHY`, `NOT_READY`.
- **Version.** v1. **Backward Compat.** Stable.
- **Dependencies.** All components; consumed by K8s (V7 Ch5) + deploy gates (V7 Ch4).
- **Ownership.** Platform/SRE.

---

# Part C — Compliance & Security Interfaces (Volume 4)

## C.1 Policy Engine
- **Purpose.** Evaluate compliance/security/commercial policy; hard rules (DPDP/RBI) unweakenable. Superset of the V2 Ch8 Policy DSL. *Source: V4 Ch4.*
- **Inputs.** Policy query (context, action, tenant).
- **Outputs.** Policy decision (allow/deny/constraints, `must_say`/`must_not_say`).
- **Methods.** `evaluate(query) -> PolicyDecision`; `hard_rules() -> RuleSet`.
- **Error Codes.** `POLICY_DENIED`, `HARD_RULE_VIOLATION`, `POLICY_UNAVAILABLE` (fail-closed).
- **Version.** v1; policies versioned. **Backward Compat.** Hard rules cannot be weakened by tenant config.
- **Dependencies.** Consumed by Conversation Engine, Output Validator, entitlements (V5 Ch9), flags (V5 Ch23).
- **Ownership.** Security & Governance (protected path, V6 Ch11).

## C.2 Audit
- **Purpose.** Immutable, tamper-evident audit trail (hash-chained). *Source: V4 Ch11.*
- **Inputs.** Auditable events (decisions, access, changes) — often via `DecisionEnvelope`.
- **Outputs.** Append-only audit records; verifiable exports.
- **Methods.** `record(event) -> None`; `verify(range) -> IntegrityReport`; `export(range, format)`.
- **Error Codes.** `AUDIT_APPEND_FAILED`, `INTEGRITY_VIOLATION`.
- **Version.** v1. **Backward Compat.** Append-only; immutable.
- **Dependencies.** Event log; reconciled with crypto-shred (V4 Ch9).
- **Ownership.** Security & Governance.

## C.3 RBAC / Authorization
- **Purpose.** Tenant-scoped authorization via the PDP; the isolation invariant (AR-8). *Source: V4 Ch6.*
- **Inputs.** Subject, action, resource, tenant.
- **Outputs.** Authorization decision (allow/deny).
- **Methods.** `authorize(subject, action, resource) -> Decision`.
- **Error Codes.** `UNAUTHORIZED`, `FORBIDDEN`, `TENANT_MISMATCH`.
- **Version.** v1. **Backward Compat.** Isolation absolute; roles additive.
- **Dependencies.** Authn (V4 Ch5); enforced everywhere.
- **Ownership.** Security & Governance.

## C.4 Security (Authn / Secrets / Encryption)
- **Purpose.** Authentication (OAuth2/OIDC/SAML, mTLS, SSO/SCIM), secrets (vault), encryption (at-rest/in-transit). *Source: V4 Ch5/7/8; V5 Ch22.*
- **Methods.** `authenticate(credential) -> Subject`; `get_secret(ref)` (vault); `encrypt/decrypt(...)`; `rotate_secret(ref)` (V7 Ch18).
- **Error Codes.** `AUTH_FAILED`, `TOKEN_EXPIRED`, `SECRET_UNAVAILABLE`, `CERT_EXPIRED`.
- **Version.** v1. **Backward Compat.** Stable; secrets never exposed.
- **Dependencies.** Vault, IdP, PKI; ops rotation (V7 Ch18).
- **Ownership.** Security & Governance.

---

# Part D — SaaS Interfaces (Volume 5)

## D.1 CRM
- **Purpose.** System of record for customers; populates `CustomerContext`. *Source: V5 Ch4.*
- **Methods.** `get_customer(id, tenant)`; `upsert(customer)`; `history(customer_id)`.
- **Error Codes.** `CUSTOMER_NOT_FOUND`, `TENANT_MISMATCH`, `STALE_DATA`.
- **Version.** v1. **Backward Compat.** Customer schema additive. **Dependencies.** Integration (V5 Ch15). **Owner.** SaaS/Product.

## D.2 Collections
- **Purpose.** System of record for loans/repayment (DPD, PTP, settlement); authoritative loan facts for the Law of Authority. *Source: V5 Ch5.*
- **Methods.** `get_loan(id, tenant)`; `capture_ptp(ptp, idempotency_key)`; `record_settlement(...)`; `update_dpd(...)`.
- **Error Codes.** `LOAN_NOT_FOUND`, `PTP_DATE_INVALID`, `OUT_OF_ENVELOPE`, `DUPLICATE_EFFECT` (prevented, AR-15).
- **Version.** v1. **Backward Compat.** Additive; effects idempotent. **Dependencies.** LMS sync (V5 Ch15), Policy Engine. **Owner.** SaaS/Product.

## D.3 Campaign
- **Purpose.** Select accounts + initiate outbound calls within compliance windows. *Source: V5 Ch6.*
- **Methods.** `create_campaign(spec)`; `initiate_calls(campaign_id)`; `pause/resume(...)`.
- **Error Codes.** `OUTSIDE_WINDOW` (RBI), `CAMPAIGN_INVALID`, `CAPACITY_LIMIT`.
- **Version.** v1. **Backward Compat.** Additive. **Dependencies.** Dialer (V1 Ch3), Policy (V4 Ch2). **Owner.** SaaS/Product.

## D.4 Billing
- **Purpose.** Rate metered usage → invoices; entitlements. *Source: V5 Ch9–10.*
- **Methods.** `rate(usage, tenant)`; `invoice(tenant, period)`; `entitlement(tenant, feature)`.
- **Error Codes.** `USAGE_UNAVAILABLE`, `ENTITLEMENT_DENIED`.
- **Version.** v1. **Backward Compat.** Additive; usage idempotent. **Dependencies.** Metering (V5 Ch10), Policy Engine. **Owner.** SaaS/Product.

## D.5 Analytics
- **Purpose.** KPIs + outcome attribution via `DecisionEnvelope` lineage. *Source: V5 Ch11.*
- **Methods.** `kpi(metric, range, tenant)`; `attribute(outcome)`.
- **Error Codes.** `METRIC_UNAVAILABLE`, `STALE_DATA`.
- **Version.** v1. **Backward Compat.** Additive. **Dependencies.** Event log, lineage. **Owner.** SaaS/Product.

## D.6 Admin
- **Purpose.** Tenant control surface; AI changes route through AI Config (V5 Ch14). *Source: V5 Ch13.*
- **Methods.** `get_settings(domain)`; `update_settings(change, actor)`; `preview(change)`.
- **Error Codes.** `UNAUTHORIZED`, `VALIDATION_FAILED`, `ENTITLEMENT_DENIED`.
- **Version.** v1. **Backward Compat.** API-parity with portals (V5 Ch16). **Dependencies.** AI Config, Policy, Users. **Owner.** SaaS/Product.

## D.7 Workflow
- **Purpose.** No-code event-driven automation (triggers→conditions→actions/approvals). *Source: V5 Ch17.*
- **Methods.** `define(workflow)`; `trigger(event)`; `approve_step(run, step, approver)`.
- **Error Codes.** `WORKFLOW_INVALID`, `LOOP_DETECTED`, `UNAUTHORIZED_ACTION`.
- **Version.** v1. **Backward Compat.** Additive. **Dependencies.** Event log (V3 Ch3), approvals (V4 Ch15). **Owner.** SaaS/Product.

## D.8 Marketplace
- **Purpose.** Governed, sandboxed extensions via supported surfaces (V5 Ch14/15/16). *Source: V5 Ch19.*
- **Methods.** `publish(item)`; `install(tenant, item)`; `enable/revoke(...)`.
- **Error Codes.** `CERT_REQUIRED`, `SANDBOX_VIOLATION`, `REVOKED`.
- **Version.** v1. **Backward Compat.** Extension contracts versioned. **Dependencies.** AI Config, Integration, API platform; governance (V4 Ch3/14). **Owner.** SaaS/Product.

## D.9 API Platform (public)
- **Purpose.** Public REST/WS/webhooks/SDKs over the full V4 Ch12 security pipeline. *Source: V5 Ch16.* Full reference: **DocSuite-04.**
- **Methods.** Versioned REST + WS; webhook subscriptions; SDKs.
- **Error Codes.** Uniform error shape (DocSuite-04 §errors).
- **Version.** URL-versioned (v1, v2). **Backward Compat.** 12-month support window; additive within version.
- **Dependencies.** Authn (V4 Ch5), rate limit (V4 Ch12), all platform contexts. **Owner.** SaaS/Product.

---

## Cross-cutting contract rules (apply to all interfaces)
- **Tenant scoping (AR-8):** every method that touches data is tenant-scoped.
- **Idempotency (AR-15):** state-changing/authoritative methods accept idempotency keys.
- **Reuse contracts (AR-20):** types come from `libs/contracts`; no parallel definitions.
- **Authz first (API-DS-5):** authorize before business logic.
- **Versioning (GIT-5 / API-DS-3):** SemVer; additive within a version; breaking → new version + deprecation.
- **Protected contracts:** `ResponsePlan`, `DecisionEnvelope`, `EventEnvelope`, Policy Engine, and `libs/invariants` change only via Architecture-Board review (V6 Ch11).

## Ownership summary

| Layer | Owner team |
|---|---|
| Runtime (Media/STT/GPU/LLM/TTS/Playback) | Voice Runtime |
| Conversation Engine + engines + Memory + Prompt | Conversation Intelligence / AI Engineering |
| Reliability (Redis/Event/Persistence/Recovery/Queue/Health) | Platform/SRE |
| Compliance/Security (Policy/Audit/RBAC/Security) | Security & Governance |
| SaaS (CRM/Collections/Campaign/Billing/Analytics/Admin/Workflow/Marketplace/API) | SaaS/Product |
| Shared contracts (`libs/contracts`, `libs/invariants`) | Architecture Board |

---

## Version history & change log

| Version | Date | Change | Owner |
|---|---|---|---|
| 1.0 | (initial) | Interface contracts consolidated from Vols 1–7 | Documentation Engineering |

**Change-log policy:** any new/changed interface in a volume MUST be recorded here. The suite consistency audit (DocSuite-12) verifies every public interface has a contract entry.

*End of Document 2 — Interface Contracts.*
