# VoiceOS v2 — Documentation Suite

## Document 1 — Master Index

**Type:** Canonical navigation index (documentation layer over Volumes 1–7)
**Status:** Living reference · **Owner:** Chief Architect / Documentation Engineering
**Audience:** Engineers, AI coding agents, QA, DevOps/SRE, auditors, future maintainers.
**Authority:** Volumes 1–7 are **immutable + canonical**. This index does not define, redesign, or modify anything — it locates everything already defined across the seven volumes. Where this index and a volume disagree, the volume wins and this index is the bug to fix.

> **How to cite:** references use the form `V<n> Ch<c>` (e.g., `V1 Ch7` = Volume 1, Chapter 7) and, for appendices, `V<n> App.<X>`. Source volume files: `VoiceOS-v2-Vol1-Core-Voice-Architecture.md` … `VoiceOS-v2-Vol7-Operations-Deployment-HyperScale.md`. Sibling suite docs are cited as `DocSuite-NN`.

---

## 0. Suite map (the 12 documents)

| # | Document | Purpose | File |
|---|----------|---------|------|
| 1 | **Master Index** | Navigate everything in Vols 1–7 | `VoiceOS-DocSuite-01-Master-Index.md` |
| 2 | Interface Contracts | Every public interface (purpose/IO/methods/errors/version/owner) | `…-02-Interface-Contracts.md` |
| 3 | Data Dictionary | Every entity + field (type/constraints/source/lifecycle) | `…-03-Data-Dictionary.md` |
| 4 | API Reference | REST/WS/webhooks (auth/endpoints/errors/limits) | `…-04-API-Reference.md` |
| 5 | Configuration Reference | Every config item (values/default/risk) | `…-05-Configuration-Reference.md` |
| 6 | Prompt Library | Every production prompt (version/IO/vars/eval) | `…-06-Prompt-Library.md` |
| 7 | Voice Style Guide | The canonical TTS speaking standard | `…-07-Voice-Style-Guide.md` |
| 8 | Testing Catalog | Every test (purpose/criteria/automation/owner) | `…-08-Testing-Catalog.md` |
| 9 | Deployment Cookbook | Practical deploy/operate procedures | `…-09-Deployment-Cookbook.md` |
| 10 | AI Evaluation Handbook | How AI quality is measured | `…-10-AI-Evaluation-Handbook.md` |
| 11 | **Glossary** | Every term, defined + sourced | `VoiceOS-DocSuite-11-Glossary.md` |
| 12 | Engineering Templates | Reusable templates (ADR/spec/PR/runbook/…) | `…-12-Engineering-Templates.md` |

---

## 1. Index by Volume

### Volume 1 — Core Voice Architecture *(frozen)* — 25 chapters + Appendices A–F
The real-time voice runtime + its contracts. Latency budget governs everything.

| Ch | Title | Key objects (→ see) |
|----|-------|---------------------|
| 1 | Architecture Overview | runtime planes |
| 2 | Real-Time Principles | RI-1…RI-8 (App. E) |
| 3 | Media Gateway | RTP/telephony ingress |
| 4 | Transport & Jitter | jitter buffer, μ-law/A-law |
| 5 | Audio Preprocessing | AEC3, far-end ref |
| 6 | VAD & Endpointing | Silero VAD, barge-in |
| 7 | **GPU Scheduler** | admission control, VRAM ledger, OOM-by-construction (RI-8) |
| 8 | STT Engine | Faster-Whisper Large-v3 Turbo FP8 |
| 9 | Dialogue Manager | `TurnInput` |
| 10 | **Conversation Engine (runtime)** | deterministic owner of state/business logic |
| 11 | CustomerContext | authoritative context assembly |
| 12 | Prompt Builder | deterministic prompt (RI-7) |
| 13 | LLM Runtime | vLLM, Qwen2.5-7B-Instruct-FP8, TTFT, KV/prefix cache |
| 14 | Output Validator | the pre-TTS gate |
| 15 | Speech Rendering | text normalization |
| 16 | Voice Style | tone settings (→ DocSuite-07) |
| 17 | TTS Engine | Veena TTS, 24 kHz WebSocket |
| 18 | Prosody | emphasis, pauses |
| 19 | Emotion Rendering | emotion → prosody |
| 20 | TTS Streaming | SOXR resampling |
| 21 | Playback Scheduler | played-offset checkpoint |
| 22 | Audio Output | output coherence (RI-6) |
| 23 | **Latency Budget** | first-audio p95 ≤ 1.5 s |
| 24 | Graceful Degradation | fail-safe ladder |
| 25 | Runtime Synthesis | end-to-end call path |
| App. A | Contract Catalog | `ResponsePlan`, `TurnInput`, `AudioFrame`, … |
| App. B | Ownership Map | single-writer ownership (RI-2) |
| App. C | Sequence Library | — |
| App. D | Configuration | runtime config (→ DocSuite-05) |
| App. E | **Runtime Invariants** | RI-1…RI-8 |
| App. F | Glossary | (→ DocSuite-11) |

### Volume 2 — Conversation Intelligence — 24 chapters
Reasoning, planning, negotiation, memory, learning. Renders language; never owns authority.

| Ch | Title | Key objects |
|----|-------|-------------|
| 1 | Intelligence Overview / Four-Class Decision Hierarchy | decision priority |
| 2 | Reasoning Architecture | — |
| 3 | Intent Engine | intent classification |
| 4 | Strategy Engine | strategy selection |
| 5 | Negotiation Engine | **Negotiation `Envelope`** (≠ DecisionEnvelope) |
| 6 | Risk Engine / Law of Authority | risk veto; authority |
| 7 | Empathy Engine | empathy modeling |
| 8 | **Policy DSL** | conversational policy language |
| 9 | Dialogue Management | turn intelligence |
| 10 | **Memory** | Working Memory, Relationship Memory |
| 11 | Context Assembly | `CustomerContext` prefetch |
| 12 | Knowledge Integration | — |
| 13 | Emotion State | emotion modeling |
| 14 | Personalization | — |
| 15 | **ResponsePlan & DecisionEnvelope** | the spine objects |
| 16 | Escalation | human handoff triggers |
| 17 | **Output Evaluation** | ≥ Policy strictness |
| 18 | Learning | offline improvement |
| 19 | **Prompt Versioning** | versioned prompts (→ DocSuite-06) |
| 20 | Knowledge / Retrieval | tenant-isolated KB |
| 21 | Predictive Prefetch | context/response prefetch |
| 22 | Quality Scoring | conversation quality |
| 23 | Intelligence Reliability | — |
| 24 | Intelligence Synthesis + ADRs | — |

### Volume 3 — Reliability & Distributed Systems — 24 chapters
Durability, recovery, resilience, observability, ops foundations.

| Ch | Title | Key objects |
|----|-------|-------------|
| 1 | Reliability Overview | availability ≥ 99.95%, 0 loss |
| 2 | Topology | multi-region, AZs, planes |
| 3 | **Event Sourcing** | immutable event log; lineage |
| 4 | Redis / Hot State | non-authoritative hot state |
| 5 | Persistence | Postgres authoritative store |
| 6 | Durable State | — |
| 7 | **Recovery** | deterministic replay |
| 8 | **Idempotency** | exactly-once effects, idempotency keys |
| 9 | Distributed Coordination | single-writer at scale |
| 10 | Queue Manager | bounded queues, DLQ, retry |
| 11 | Consistency | — |
| 12 | Health | health checks/gates |
| 13 | Fallback | graceful degradation paths |
| 14 | Load Shedding | shed lowest-priority |
| 15 | **Metrics** | Prometheus (→ DocSuite-05) |
| 16 | **Logging** | structured logs, correlation IDs |
| 17 | **Tracing** | OpenTelemetry, span correlation |
| 18 | **Backup & DR** | RPO ≤ 5m, RTO ≤ 30m |
| 19 | Performance Engineering | latency/efficiency |
| 20 | Stress & Chaos Testing | load/chaos framework |
| 21 | Deployment | canary, drain-aware |
| 22 | Capacity | capacity model |
| 23 | Runbooks | reliability runbooks |
| 24 | Reliability Synthesis + ADRs | — |

### Volume 4 — Compliance, Security & Governance — 24 chapters
The trust layer governing all of the above.

| Ch | Title | Key objects |
|----|-------|-------------|
| 1 | Trust Overview / human accountability | — |
| 2 | Regulatory & Residency | RBI/DPDP, data residency |
| 3 | **Law of Authority (governance)** | authority enforcement |
| 4 | **Policy Engine** | enterprise policy superset |
| 5 | Authentication | OAuth2/OIDC/SAML, mTLS |
| 6 | **Authorization / RBAC / Tenant Isolation** | PDP, isolation invariant |
| 7 | Secrets | vault |
| 8 | Encryption | at-rest/in-transit, TLS |
| 9 | Erasure | crypto-shred + tombstone |
| 10 | PII Protection | redaction, tokenization |
| 11 | **Audit** | immutable audit, hash chain |
| 12 | API Security | gateway pipeline |
| 13 | Prompt Security | prompts ≠ secrets |
| 14 | AI Safety | toxicity/bias/hallucination/compliance |
| 15 | Human Oversight | takeover/approval |
| 16 | Security Monitoring | SOC, security alerts |
| 17 | Incident Response | containment, breach notification |
| 18 | Security Operations | — |
| 19 | Vendor / Third-Party | vendor assessment |
| 20 | Supply Chain | SBOM, provenance |
| 21 | Penetration Testing | red-team |
| 22 | Governance Runbooks | — |
| 23 | MTTD / MTTR | < 5 min MTTD |
| 24 | Governance Synthesis + ADRs | — |

### Volume 5 — SaaS Platform & Business Systems — 24 chapters
The commercial multi-tenant platform around the runtime.

| Ch | Title | Key objects |
|----|-------|-------------|
| 1 | Platform Overview / config-over-customization | — |
| 2 | **Tenancy Model** | org→BU→branch hierarchy |
| 3 | Tenant Lifecycle | provisioning, crypto-shred deletion |
| 4 | **CRM** | system of record (customers) |
| 5 | **Collections** | loans, PTP, settlement (system of record) |
| 6 | Campaigns | outbound dialer initiation |
| 7 | Contact Center | agent surface |
| 8 | Users & Orgs | user model, roles |
| 9 | Billing | rating, invoicing |
| 10 | Metering / Usage | idempotent usage events |
| 11 | Analytics | KPIs, outcome attribution |
| 12 | Reporting | reports/exports (→ DocSuite-04) |
| 13 | Administration Portal | control surface |
| 14 | **AI Configuration** | safe config of models/prompts/voices |
| 15 | Integration Platform | LMS/CRM/payment connectors |
| 16 | **API Platform** | public REST/WS/SDK (→ DocSuite-04) |
| 17 | Workflow Automation | no-code automation |
| 18 | Customer Success | health/onboarding |
| 19 | Marketplace | governed extensions |
| 20 | White-Label | branding/domains/policies |
| 21 | Business Intelligence | forecasting, benchmarking |
| 22 | Enterprise Platform | SSO/SCIM/residency/deployment tiers |
| 23 | SaaS Operations | fleet provisioning/flags/rollout |
| 24 | Platform Synthesis + ADRs | ADR-V5-001…012 |

### Volume 6 — Engineering Standards & Developer Handbook — 24 chapters + Appendix
How engineers + AI agents build VoiceOS.

| Ch | Title | Rule prefix |
|----|-------|-------------|
| 1 | Engineering Philosophy | — |
| 2 | Repository Structure | (protected paths) |
| 3 | Coding Standards | CS-1…10 |
| 4 | **Architecture Rules** | **AR-1…20** |
| 5 | API Development | API-DS-1…6 |
| 6 | Event & Message | EV-1…8 |
| 7 | Data Modeling | DM-1…8 |
| 8 | AI Development | AI-DS-1…7 |
| 9 | Testing | TEST-1…7 |
| 10 | Documentation | DOC-1…6 |
| 11 | Git Workflow | GIT-1…6 |
| 12 | CI/CD | CICD-1…8 |
| 13 | **AI Coding Agent Guidelines** | AGENT-1…12 |
| 14 | Feature Workflow | FDW-1…2 |
| 15 | Debugging | DBG-P1…P10 |
| 16 | Performance | PERF-1…5 |
| 17 | Code Quality | CQ-1…4 |
| 18 | Engineering Metrics | MET-1…3 |
| 19 | Engineering Playbooks | PLAY-1…7 |
| 20 | Sprint Methodology | SPRINT-1…4 |
| 21 | Code Review | REV-1 |
| 22 | AI-Assisted Engineering | AIE-1…3 |
| 23 | Engineering Culture | — |
| 24 | **Engineering Decision Records** | EDR-V6-001…010 |
| App | Glossary/templates/checklists | (→ DocSuite-11/12) |

### Volume 7 — Operations, Deployment & Hyper-Scale — 24 chapters + Appendices
How VoiceOS runs in production, at scale, for years.

| Ch | Title | Rule prefix |
|----|-------|-------------|
| 1 | Operations Philosophy / SLOs & error budgets | — |
| 2 | Production Topology | TOP-1 |
| 3 | Infrastructure as Code | IaC-3 |
| 4 | Deployment Strategy | DEP-1 |
| 5 | Kubernetes Architecture | K8S-1…2 |
| 6 | GPU Fleet Management | GPU-1…2 |
| 7 | Monitoring Platform | MON-1 |
| 8 | Alerting Architecture | ALERT-1 |
| 9 | Logging Platform | LOG-1 |
| 10 | Distributed Tracing | TRACE-1 |
| 11 | Incident Management | INC-1…2 |
| 12 | Capacity Planning | CAP-1 |
| 13 | Autoscaling | SCALE-1…2 |
| 14 | Disaster Recovery | DR-1…3 |
| 15 | Cost Optimization | COST-1 |
| 16 | Release Management | REL-1…2 |
| 17 | Global Scaling | GLOBAL-1…2 |
| 18 | Operational Security | SECOPS-1…3 |
| 19 | Production Runbooks | RUNBOOK-1 (RB-GPU…RB-NET) |
| 20 | Business Continuity | BCP-1…2 |
| 21 | Operational Analytics | OPS-AN-1 |
| 22 | Enterprise Operations | ENT-1…2 |
| 23 | Future Hyper-Scale Roadmap | HYPER-1 |
| 24 | **Architecture Decision Records** | ADR-V7-001…010 |
| App | Checklists/handbooks/templates | (→ DocSuite-12) |

---

## 2. Alphabetical Index — key objects

### 2.1 Interfaces / Services (→ DocSuite-02 for contracts)
- Admin Portal — V5 Ch13
- Analytics — V5 Ch11
- API Platform — V5 Ch16 · API security V4 Ch12
- Audit — V4 Ch11
- Billing — V5 Ch9 · Metering V5 Ch10
- Campaigns — V5 Ch6
- Conversation Engine — V1 Ch10 (runtime) · V2 (intelligence)
- CRM — V5 Ch4
- Collections — V5 Ch5
- Dialogue Manager — V1 Ch9 · V2 Ch9
- Event Bus / Event Log — V3 Ch3
- GPU Scheduler — V1 Ch7
- Health APIs — V3 Ch12
- Integration Platform — V5 Ch15
- LLM Runtime — V1 Ch13
- Marketplace — V5 Ch19
- Media Gateway — V1 Ch3–4
- Memory Service — V2 Ch10
- Output Validator / Output Evaluation — V1 Ch14 · V2 Ch17
- Persistence — V3 Ch5
- Playback Scheduler — V1 Ch21
- Policy Engine / Policy DSL — V4 Ch4 · V2 Ch8
- Prompt Builder — V1 Ch12
- Queue Manager — V3 Ch10
- RBAC / Authorization — V4 Ch6
- Recovery — V3 Ch7
- Redis / Hot State — V3 Ch4
- Reporting — V5 Ch12
- Risk Engine — V2 Ch6
- Security — V4 (Ch5–22)
- Speech Rendering / TTS — V1 Ch15–20
- STT Engine — V1 Ch8
- Strategy Engine — V2 Ch4
- Voice Style — V1 Ch16 (→ DocSuite-07)
- Workflow Automation — V5 Ch17

### 2.2 Core schemas / data objects (→ DocSuite-03)
- `AudioFrame` — V1 App.A
- `CustomerContext` — V1 Ch11 · V2 Ch11
- `DecisionEnvelope` — V2 Ch15 (lineage; quintuple-duty)
- `EventEnvelope` — V3 Ch3 / V6 Ch6
- Loan / EMI / `PromiseToPay` / Settlement — V5 Ch5
- Negotiation `Envelope` — V2 Ch5 *(distinct from DecisionEnvelope)*
- `RelationshipMemory` / `WorkingMemory` — V2 Ch10
- `ResponsePlan` — V2 Ch15 (sealed unit of output)
- `TurnInput` — V1 Ch9
- Transcript / Call / ConversationState — V1 Ch9–10 · V5 Ch5/7

### 2.3 Databases / stores
- Postgres (authoritative relational) — V3 Ch5
- Event log (append-only, lineage) — V3 Ch3
- Redis (hot, non-authoritative) — V3 Ch4
- Object storage (recordings/blobs/archive) — V3 Ch5 / V7 Ch9
- OpenSearch (operational logs) — V7 Ch9
- Prometheus TSDB / Tempo (metrics/traces) — V7 Ch7/10
- Audit store (immutable) — V4 Ch11

### 2.4 Invariants & enforceable rules
- **Runtime invariants RI-1…RI-8** — V1 App.E (mechanized as AR-9…14, V6 Ch4)
  - RI-1 real-time-thread purity · RI-2 single-writer · RI-3 bounded buffers · RI-4 commit-before-act · RI-5 Law of Authority · RI-6 ordering/flush coherence · RI-7 deterministic prompt · RI-8 OOM-by-construction
- **Architecture Rules AR-1…20** — V6 Ch4
- Engineering rule families — V6: CS / EV / DM / AI-DS / TEST / DOC / GIT / CICD / AGENT / FDW / DBG / PERF / CQ / MET / PLAY / SPRINT / REV / AIE
- Operations rule families — V7: TOP / IaC / DEP / K8S / GPU / MON / ALERT / LOG / TRACE / INC / CAP / SCALE / DR / COST / REL / GLOBAL / SECOPS / RUNBOOK / BCP / OPS-AN / ENT / HYPER

### 2.5 Decision records
- **ADRs (architecture)** — V2 Ch24, V3 Ch24, V4 Ch24, **V5 Ch24 (ADR-V5-001…012)**, **V7 Ch24 (ADR-V7-001…010)**
- **EDRs (engineering)** — **V6 Ch24 (EDR-V6-001…010)**

### 2.6 Engines (V2 reasoning layer)
Intent (Ch3) · Strategy (Ch4) · Negotiation (Ch5) · Risk (Ch6) · Empathy (Ch7) · Planning/Dialogue (Ch9) · Memory (Ch10) · Evaluation (Ch17) — all consumed by the Conversation Engine.

### 2.7 Configuration sections (→ DocSuite-05)
Runtime (V1 App.D) · reliability (V3) · trust/security (V4) · platform (V5) · CI-CD + standards (V6) · operations: topology/IaC/deploy/K8s/GPU/monitoring/alerting/logging/tracing/capacity/autoscaling/DR/cost/release/global/opsec/BCP (V7 Ch2–20).

### 2.8 Mermaid diagrams
Every chapter of V1–V7 includes component + sequence diagrams (V2–V7 use the 20-section template's §10/§11; V6 embeds diagrams inline). The diagram set is indexed implicitly by chapter — to find a flow, locate its chapter above. Notable: end-to-end call path (V1 Ch25), GPU scheduling (V1 Ch7 / V7 Ch5), event sourcing (V3 Ch3), regional failover (V7 Ch14), AI-agent workflow (V6 Ch13).

---

## 3. Shared primitives (carried across all volumes)

| Primitive | Defined | Roles across volumes |
|---|---|---|
| **Law of Authority (RI-5)** | V1 App.E / V2 Ch6 / V4 Ch3 | facts originate in V5 system of record; model never owns them (V1–2); governed (V4); enforced in code (V6 AR-3); preserved in ops incl. edge (V7) |
| **`DecisionEnvelope` lineage** | V2 Ch15 | reasoning (V2) · event log (V3) · audit (V4) · analytics (V5) · enforced contract (V6) · ops forensics (V7) |
| **`ResponsePlan`** | V2 Ch15 | sealed/versioned/governed/billable/analyzable/testable/deployable unit of output |
| **Tenant isolation** | V4 Ch6 | absolute from runtime (V1–4) → multi-tenancy (V5) → code (V6 AR-8) → fleet (V7) |
| **Policy Engine** | V4 Ch4 (superset of V2 Ch8 DSL) | conversational + security + commercial + operational policy |
| **RI-1…RI-8** | V1 App.E | governed (V4), enforced (V6 AR-9…14), preserved at hyper-scale (V7 HYPER-1) |

---

## Version history & change log

| Version | Date | Change | Owner |
|---|---|---|---|
| 1.0 | (initial) | Master Index created over Vols 1–7 | Documentation Engineering |

**Change-log policy:** update this index whenever a volume is amended (volumes are immutable in normal operation; changes occur only via the V6 PLAY-6 / ADR architecture-change path). Every new interface/event/schema/ADR added to a volume MUST be added here (enforced by the suite consistency audit, DocSuite-12 final section).

*End of Document 1 — Master Index.*
