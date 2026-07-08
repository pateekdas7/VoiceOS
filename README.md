# VoiceOS v2

**An enterprise-grade AI Voice Operating System for real-time, regulated, multilingual voice automation.**

VoiceOS v2 conducts natural, low-latency spoken conversations — initially for collections and customer engagement in Indian financial services (Hindi / Hinglish / English) — on a fully specified, production-oriented architecture. The architecture is complete and frozen across seven volumes; this repository is where that architecture is **implemented, tested, validated, and deployed**.

> **Status:** 🟡 In development. Architecture (Volumes 1–7) and the Documentation Suite are complete. Implementation proceeds in dependency-ordered sprints. See [`PROJECT_STATUS.md`](./PROJECT_STATUS.md).

---

## 1. Project Overview

**What VoiceOS is.** VoiceOS is a real-time voice platform: audio arrives from a telephony carrier, is transcribed, reasoned over by a conversation-intelligence layer, and answered with synthesized speech — all within a strict first-audio latency budget. Around that runtime sits a reliability layer, a compliance and security layer, a multi-tenant SaaS platform, and a full operations stack.

**Vision.** A dependable, compliant voice agent that financial institutions can trust with sensitive customer conversations at scale — one that is fast, respectful, auditable, and faithful to authoritative business data.

**Objectives.**
- Deliver natural spoken conversations with **first-audio p95 ≤ 1.5 s**.
- Keep the agent **factually grounded** — the model renders language, never invents facts (the Law of Authority).
- Operate within **regulatory constraints** (RBI fair-practice, DPDP) by construction.
- Run as a **multi-tenant SaaS** with strict tenant isolation.
- Be **reliable** (no lost or duplicated effects; deterministic recovery) and **observable** end to end.
- Scale from a single GPU to a global fleet **without changing application logic**.

**High-level capabilities.** Streaming STT, streaming LLM reasoning, streaming TTS, barge-in handling, deterministic conversation control, negotiation within bounded envelopes, durable event-sourced state, immutable audit, multi-tenant administration, billing/metering, analytics, and production-grade deployment/operations.

---

## 2. Core Features

- **Real-time voice conversations** — telephony ingress (RTP/SIP/WebRTC), echo cancellation, VAD/endpointing, and barge-in, orchestrated on a non-blocking real-time path.
- **Streaming STT** — low-latency speech-to-text on GPU via a replaceable adapter.
- **Streaming LLM** — token-streaming reasoning behind a deterministic prompt contract; the LLM produces language, never authoritative facts.
- **Streaming TTS** — natural, multilingual speech synthesis (Hindi/Hinglish/English) with prosody and emotion, streamed for early playback.
- **Conversation Intelligence** — intent, strategy, negotiation, risk, empathy, and memory engines producing a sealed, versioned `ResponsePlan` and a recorded `DecisionEnvelope`.
- **Reliability** — event sourcing, idempotent (exactly-once) effects, deterministic replay/recovery, bounded queues, circuit breakers, and disaster recovery (RPO ≤ 5 min / RTO ≤ 30 min).
- **Compliance & Security** — a central Policy Engine, RBAC, absolute tenant isolation, encryption, PII redaction, crypto-shred erasure, and immutable audit.
- **SaaS platform** — multi-tenancy, CRM, collections (system of record), campaigns, billing, analytics, an admin portal, and a public API.
- **Monitoring & observability** — metrics, structured logs, and distributed tracing on every hop, with SLO dashboards and burn-rate alerting.

---

## 3. Repository Structure

```
voiceos-v2/
├── README.md                  # This file — entry point for the whole project
├── CLAUDE.md                  # Engineering constitution (binding rules for all work)
├── PROJECT_STATUS.md          # Single source of truth for current implementation status
├── CHANGELOG.md               # Notable implementation changes (Keep a Changelog)
│
├── architecture/              # The 7 authoritative architecture volumes (immutable)
│   ├── Volume-1-Core-Voice-Architecture.md
│   ├── Volume-2-Conversation-Intelligence.md
│   ├── Volume-3-Reliability.md
│   ├── Volume-4-Compliance-Security.md
│   ├── Volume-5-SaaS-Platform.md
│   ├── Volume-6-Engineering-Standards.md
│   └── Volume-7-Operations-Deployment.md
│
├── documentation/             # Documentation Suite (Docs 1–12): index, contracts,
│   │                          # data dictionary, API ref, config, prompts, voice style,
│   │                          # testing, deployment, AI evaluation, glossary, templates
│   └── 01-Master-Index.md … 12-Engineering-Templates.md
│
├── implementation/            # Implementation planning & live project tracking
│   ├── CURRENT_SPRINT.md      # The sprint currently being implemented
│   ├── ROADMAP.md             # Dependency-ordered roadmap (epics → sprints)
│   ├── EPICS.md               # Epic definitions and scope
│   ├── BACKLOG.md             # All sprints, ordered, with status
│   ├── DONE.md                # Completed sprints log
│   ├── BUILD_ORDER.md         # Canonical build/implementation order
│   ├── DEPENDENCY_GRAPH.md    # Component/sprint dependency graph
│   ├── SPRINT_GRAPH.md        # Sprint sequencing graph
│   ├── MILESTONES.md          # Milestones and their completion criteria
│   └── sprints/               # Per-sprint documentation
│
├── src/                       # Application source (layout per Volume 6)
│   ├── services/              # Deployable services (media-gateway, gpu-scheduler,
│   │                          # stt, llm-runtime, tts, conversation-engine, …)
│   ├── engines/               # Intelligence engines (intent, strategy, negotiation,
│   │                          # risk, empathy, memory)
│   └── libs/
│       ├── contracts/         # Shared typed contracts (ResponsePlan, DecisionEnvelope,
│       │                      # TurnInput, CustomerContext, EventEnvelope) — protected
│       └── invariants/        # Enforceable runtime invariants (RI-1…8 / AR rules) — protected
│
├── tests/                     # Unit, integration, e2e, load, chaos, invariant suites
├── docs/                      # Developer-facing docs (READMEs, guides, generated API docs)
├── scripts/                   # Tooling, dev/ops scripts, local environment helpers
├── adr/                       # Architecture Decision Records
└── evaluation/                # AI/voice evaluation: golden sets, benchmarks, reports
```

**Directory purposes.**

- **`architecture/`** — the seven authoritative volumes (§4). Immutable during implementation; changed only via an approved ADR.
- **`documentation/`** — the Documentation Suite that consolidates, indexes, and cross-references the volumes (interface contracts, data dictionary, API reference, configuration, prompt library, voice style guide, testing catalog, deployment cookbook, AI evaluation handbook, glossary, templates). Reference layer, not a place to redesign.
- **`implementation/`** — the planning and tracking layer: the roadmap, epics, backlog, current sprint, and the graphs/milestones that order the work.
- **`src/`** — the application source, organized per Volume 6: `services/` (deployable processes), `engines/` (intelligence components), and `libs/` (shared `contracts` and `invariants`, both **protected paths** that require Architecture-Board review to change).
- **`tests/`** — all automated tests (the test taxonomy is defined in the Documentation Suite); coverage and invariant gates run in CI.
- **`docs/`** — developer-facing documentation and generated API docs (distinct from `architecture/` and `documentation/`).
- **`scripts/`** — repeatable tooling for development and operations.
- **`adr/`** — Architecture Decision Records (§8).
- **`evaluation/`** — AI and voice quality evaluation artifacts and reports (§9).

---

## 4. Architecture

The complete system architecture is defined by **seven authoritative volumes** in `architecture/`. They are the source of truth; implementation conforms to them and does not modify them outside an approved ADR.

| Volume | Title | Defines |
|---|---|---|
| **1** | Core Voice Architecture | Runtime pipeline, audio processing, streaming, GPU Scheduler, STT/LLM/TTS, playback, runtime contracts, **deployment topology** |
| **2** | Conversation Intelligence | Intent, strategy, negotiation, risk, empathy engines; memory; `ResponsePlan`; `DecisionEnvelope` |
| **3** | Reliability | Event sourcing, recovery/replay, idempotency, queues, health, monitoring, tracing, disaster recovery |
| **4** | Compliance & Security | Policy Engine, RBAC, tenant isolation, encryption, privacy, audit, AI governance |
| **5** | SaaS Platform | Multi-tenancy, CRM, collections, campaigns, billing, analytics, admin portal |
| **6** | Engineering Standards & Developer Handbook | Coding standards, testing, repository structure, CI/CD, workflow, documentation |
| **7** | Operations & Deployment | Deployment, Kubernetes, GPU fleet, monitoring, scaling, capacity planning, production operations |

A companion **Documentation Suite** (`documentation/`, Documents 1–12) indexes and cross-references all seven volumes for day-to-day reference. **These documents are authoritative**; when code and a volume disagree, the volume wins.

A small set of **shared primitives** threads through every volume and must be preserved by all implementation work:

- **Law of Authority** — authoritative facts come from systems of record; the model never invents them, and business logic never lives in prompts.
- **`ResponsePlan` / `DecisionEnvelope`** — the sealed unit of agent output and the recorded decision/lineage object.
- **Tenant isolation** — absolute across the runtime, platform, code, and fleet.
- **Runtime invariants (RI-1…8)** — real-time purity, single-writer state, bounded buffers, commit-before-act, the Law of Authority, output coherence, deterministic prompts, and OOM-by-construction.

---

## 5. Engineering Workflow

Implementation flows from architecture to committed code along a fixed path:

```
Architecture
      ↓
Implementation Roadmap
      ↓
Epics
      ↓
Sprints
      ↓
Implementation
      ↓
Tests
      ↓
Documentation
      ↓
Git Commit
```

Work is delivered **one sprint at a time**, in dependency order, with each sprint fully completed (built, tested, documented, tracked) before the next begins.

### How a Claude Code session is expected to work

```
1. Read CLAUDE.md                         # the engineering constitution
2. Read PROJECT_STATUS.md                 # current status / next action
3. Read implementation/CURRENT_SPRINT.md  # the sprint to implement
4. Read ONLY the architecture chapters referenced by that sprint
5. Implement ONLY the current sprint      # no future sprints, no scope creep
6. Run tests; verify acceptance criteria + Definition of Done
7. Update tracking files (CURRENT_SPRINT, DONE, BACKLOG, CHANGELOG, PROJECT_STATUS)
8. STOP                                    # do not auto-start the next sprint
```

This discipline keeps changes focused, prevents architecture drift, and ensures every increment is tested, documented, and reversible.

---

## 6. Project Tracking

The `implementation/` directory holds the live project-tracking layer:

| File | Purpose |
|---|---|
| **ROADMAP.md** | The complete dependency-ordered plan (epics → sprints) generated from the architecture. |
| **EPICS.md** | Epic definitions, scope, and the milestone each epic delivers. |
| **CURRENT_SPRINT.md** | The single sprint currently being implemented — objective, dependencies, acceptance criteria, Definition of Done. |
| **BACKLOG.md** | Every sprint, in order, with status (pending / current / done / blocked). |
| **DONE.md** | Log of completed sprints with summaries and acceptance results. |
| **CHANGELOG.md** | Notable implementation changes (Keep a Changelog; SemVer). *(Lives at repo root.)* |
| **BUILD_ORDER.md** | The canonical order in which components are built. |
| **DEPENDENCY_GRAPH.md** | Dependencies between components and sprints. |
| **SPRINT_GRAPH.md** | Sprint sequencing and parallelization opportunities. |
| **MILESTONES.md** | Project milestones and the criteria that mark each complete. |

`PROJECT_STATUS.md` (repo root) is the **single source of truth** for current status and is updated at the end of every session.

---

## 7. Development Standards

Engineering standards are defined by **Volume 6 (Engineering Standards & Developer Handbook)** and are enforced in review and CI. In summary:

- **All code must include tests** — unit, integration, and (where applicable) regression and performance tests. Every invariant touched must have a test. Tests must pass before a sprint is complete.
- **Documentation must be updated** whenever implementation changes — including API documentation and the `CHANGELOG.md`.
- **Architecture cannot be modified without an ADR** — the volumes are immutable during implementation; a proposed change stops work and routes through `adr/`.
- **One sprint = one logical implementation unit** — atomic, focused commits; no unrelated refactoring; no partial or placeholder features.
- **Strong typing, explicit error handling, structured logging**, and documented public interfaces are required (Volume 6). Latency budgets (Volume 1) and security rules (Volume 4) are honored on every change; secrets are never committed.
- **STT/LLM/TTS are replaceable adapters** — business behavior stays model-agnostic.

---

## 8. Architecture Decision Records

The **`adr/`** directory contains Architecture Decision Records — the only sanctioned mechanism for changing the architecture.

**When an ADR is required:**
- Any change to an architecture volume or a defined contract.
- Any new system, removed component, or altered interface.
- Any deviation from a defined invariant, latency budget, or security control.
- Any significant new dependency or cross-cutting technical decision.

**ADR process:** document the problem, the alternatives considered, the trade-offs, and the recommendation; mark it *Proposed*; and **wait for approval** before implementing. Accepted ADRs are immutable (superseded, never edited away). A template is provided in the Documentation Suite (Engineering Templates).

---

## 9. Evaluation

The **`evaluation/`** directory holds the artifacts and reports that prove AI and voice quality before changes ship. Evaluation is a gate, not an afterthought: changes to prompts or models must pass it.

- **Founder validation** — structured expert review of real conversations for tone, compliance, and collections appropriateness (blocking for pilot and major AI changes).
- **Benchmarks** — golden datasets and regression benchmarks scored against a baseline so quality cannot silently regress.
- **Audio quality reports** — pronunciation correctness and Mean Opinion Score (MOS) against the Voice Style Guide.
- **Latency reports** — per-stage and end-to-end latency against the first-audio budget.
- **AI evaluation** — conversation/negotiation/compliance/empathy/risk scoring, hallucination checks, and the Law-of-Authority red-team (zero unauthorized effects).
- **Regression testing** — conversation replay of recorded calls to detect behavioral drift across prompt/model versions.

The full method is defined in the Documentation Suite (AI Evaluation Handbook).

---

## 10. Getting Started

A new developer — or a new Claude Code session — should begin by reading, in order:

1. **`README.md`** (this file) — orientation.
2. **`CLAUDE.md`** — the binding engineering constitution.
3. **`PROJECT_STATUS.md`** — current status and the next action.
4. **`implementation/CURRENT_SPRINT.md`** — the sprint to implement.
5. **The referenced architecture chapters** — only those the current sprint cites (do not load unrelated architecture).
6. **Begin implementation** — current sprint only; build, test, document, update tracking files, then stop.

Local tooling and environment setup live in `scripts/`; coding, testing, and CI standards are in Volume 6.

---

## 11. License

_License: TBD._ (Placeholder — to be finalized before any public release.)

---

## 12. Roadmap

Implementation follows a **dependency-ordered set of sprints generated from the architecture**, grouped into epics and mapped to project milestones (from foundation through core runtime, conversation intelligence, reliability, compliance and security, the SaaS platform, production alpha, founder validation, pilot, and production release). A thin end-to-end "walking skeleton" (a complete real call) is delivered early, then hardened layer by layer. The authoritative plan lives in `implementation/ROADMAP.md`, with current status in `PROJECT_STATUS.md`.

```
Foundation → Core Runtime → Conversation Intelligence → Reliability
   → Compliance & Security → SaaS Platform → Production Alpha
   → Founder Validation → Pilot → Production Release
```

---

*VoiceOS v2 — built to its architecture, one sprint at a time.*
