# VoiceOS v2 — Documentation Suite

## Document 11 — Glossary

**Type:** Canonical terminology reference (documentation layer over Volumes 1–7)
**Status:** Living reference · **Owner:** Chief Architect / Documentation Engineering
**Authority:** Volumes 1–7 are immutable + canonical. This glossary **records** the terminology defined in the volumes; it never redefines it. Each entry cites its originating volume + chapter (`V<n> Ch<c>`). Where this glossary and a volume disagree, the volume wins.

> Terms are alphabetical. **Bold** cross-terms link conceptually to their own entries. The "Source" column is the authoritative definition location; consult it for full detail.

---

## A

**A-law / μ-law** — Companding schemes for telephony audio encoding handled at the Media Gateway. *Source: V1 Ch4.*

**AEC3 (Acoustic Echo Cancellation)** — Echo cancellation in audio preprocessing, using the far-end reference to remove echo; ERLE measures effectiveness. *Source: V1 Ch5.*

**Admission control** — The GPU Scheduler's gate that admits inference work only within the VRAM budget, making **OOM-by-construction** true (RI-8). *Source: V1 Ch7.*

**ADR (Architecture Decision Record)** — An immutable record of an architecture-affecting decision (Context · Alternatives · Decision · Trade-offs · Risks · Future). *Source: V2/V3/V4/V5/V7 Ch24; template DocSuite-12.*

**AR rules (Architecture Rules)** — The 20 enforceable engineering rules (AR-1…20) translating Vols 1–5 into checkable constraints. *Source: V6 Ch4.*

**AGENT rules** — The 12 binding rules (AGENT-1…12) governing AI coding agents (read architecture first, never invent interfaces, etc.). *Source: V6 Ch13.*

**Authoritative source / system of record** — The store that owns a fact (e.g., **CRM** for customers, **Collections** for loans); the **Law of Authority** requires facts be read from it, never inferred. *Source: V4 Ch3 / V5 Ch4–5.*

## B

**Barge-in** — Caller interruption of the agent's speech; detected via VAD/endpointing, triggering playback stop + re-planning. *Source: V1 Ch6, Ch21.*

**Blue-green / Canary / Rolling / Progressive** — Deployment strategies; all drain-aware + gated + reversible (zero dropped calls). *Source: V3 Ch21 / V7 Ch4.*

**Business Continuity Plan (BCP)** — The plan + practices (incl. chaos engineering) ensuring the platform + organization continue through major disruption. *Source: V7 Ch20.*

## C

**Campaign** — An outbound calling initiative that selects accounts + initiates calls via the dialer within compliance windows. *Source: V5 Ch6.*

**Canary** — see Blue-green. A small live-traffic cohort gated by health/perf before progressive promotion. *Source: V7 Ch4.*

**Circuit breaker** — A resilience pattern that fails fast + isolates a failing dependency. *Source: V3 Ch14.*

**Collections** — The SaaS context owning loan/repayment domain logic (DPD, PTP, settlement) and the authoritative loan facts. *Source: V5 Ch5.*

**Commit-before-act (RI-4)** — The invariant that an externally-visible effect is durably committed before it is performed. *Source: V1 App.E / V3 Ch8.*

**Conversation Engine** — The deterministic owner of state, business, and authority logic; nothing bypasses it (AR-1). Runtime in V1, intelligence in V2. *Source: V1 Ch10 / V2.*

**Correlation ID / Causation ID / Trace ID** — Identifiers propagated across every hop to group (correlation), causally link (causation), and trace (trace) a request. *Source: V3 Ch16–17 / V6 Ch6.*

**Crypto-shred + tombstone** — Erasure method: destroy the per-record encryption key (crypto-shred) + leave an immutable tombstone — reconciling right-to-erasure with immutable audit. *Source: V4 Ch9.*

**CustomerContext** — The assembled, authoritative context for a call (customer, loan, history), populated from the system of record; the runtime never invents its contents. *Source: V1 Ch11 / V2 Ch11.*

## D

**DecisionEnvelope** — The recorded-decision object `{decision, confidence, reasoning, evidence, timestamp, source_engine, version}`. Serves quintuple duty: reasoning record (V2), event-sourcing log (V3), audit trail (V4), business-analytics substrate (V5), operational forensics (V7). **Distinct from the Negotiation `Envelope`.** *Source: V2 Ch15.*

**Deterministic prompt (RI-7)** — The invariant that the Prompt Builder produces an identical prompt from the same sealed plan + versions (hashable, replayable). *Source: V1 Ch12 / App.E.*

**Disaster Recovery (DR)** — Regional failover + data recovery within RTO ≤ 30 min / RPO ≤ 5 min, with 0 committed-effect loss; drilled. *Source: V3 Ch18 / V7 Ch14.*

**DPD (Days Past Due)** — The delinquency bucket of a loan; drives campaign selection + strategy. *Source: V5 Ch5.*

**Drain-aware** — A deploy/scale-down that finishes in-flight calls (accepts no new) so no call is dropped. *Source: V3 Ch21 / V7 Ch4/Ch13.*

## E

**EDR (Engineering Decision Record)** — An immutable record of an engineering-practice decision (engineering analog of an ADR). *Source: V6 Ch24.*

**EMI (Equated Monthly Installment)** — A fixed periodic loan repayment amount. *Source: V5 Ch5.*

**Endpointing** — Detecting end-of-turn (caller stopped speaking) to trigger response. *Source: V1 Ch6.*

**Error budget** — `1 − SLO`; the allowable unreliability that objectively governs change pace (exhausted → freeze risky changes). *Source: V7 Ch1.*

**Event log / Event sourcing** — The append-only, immutable record of facts that happened; the substrate for deterministic replay, idempotency, audit, and analytics. *Source: V3 Ch3.*

**EventEnvelope** — The mandatory event wrapper (event_id, type, schema_version, tenant_id, occurred_at, correlation/causation/trace IDs, payload, lineage_ref). *Source: V3 Ch3 / V6 Ch6.*

**Exactly-once** — The effective guarantee (via idempotency + at-least-once delivery + dedup) that an authoritative effect happens once. *Source: V3 Ch8.*

## F

**Fallback** — A graceful-degradation path (e.g., alternate executor/voice) engaged when a component degrades. *Source: V3 Ch13.*

**Far-end reference** — The played-audio signal fed to AEC3 to cancel echo. *Source: V1 Ch5.*

**Four-Class Decision Hierarchy** — The priority ordering of decision classes (policy/compliance highest), within which strategy chooses approach but never overrides policy/risk. *Source: V2 Ch1.*

## G

**Graceful degradation** — Failing down a defined ladder (rather than catastrophically) to preserve a safe, working call. *Source: V1 Ch24.*

**GPU Scheduler** — The component arbitrating GPU/VRAM via admission control, making **OOM-by-construction** (RI-8) true; K8s places GPU pods, the scheduler arbitrates VRAM. *Source: V1 Ch7 / V7 Ch5–6.*

## H

**Hallucination detection** — AI-safety evaluation ensuring the model does not assert unsupported facts; reinforced by the Law of Authority (facts come from the system of record). *Source: V4 Ch14 / V2 Ch17.*

**Hot state** — Fast, non-authoritative working state (Redis); always rehydratable, never the authoritative source. *Source: V3 Ch4 (DM-1).*

**Hyper-scale** — The roadmap state (thousands of GPUs, millions of conversations, global/edge/multi-cloud, autonomous ops) reached by scaling + automating the same architecture (HYPER-1 preserves invariants). *Source: V7 Ch23.*

## I

**Idempotency / Idempotency key** — The property (+ deterministic key) ensuring a repeated operation has the effect of one; foundation of exactly-once effects. *Source: V3 Ch8 (AR-15).*

**Immutable infrastructure** — Servers/containers replaced from versioned images, never mutated in place (no drift). *Source: V7 Ch1/Ch3.*

**Incident Commander (IC)** — The coordinator of a SEV1/2 incident response (decides, owns comms). *Source: V7 Ch11.*

**Infrastructure as Code (IaC)** — All infrastructure declarative, versioned, reviewed, reproducible; no manual prod changes (IaC-3). *Source: V7 Ch3.*

## J

**Jitter buffer** — The transport buffer absorbing network jitter for smooth audio. *Source: V1 Ch4.*

## K

**KV cache / Prefix cache** — LLM caches (key-value attention state / shared prompt prefix) that cut TTFT and cost; reused safely without breaking authority. *Source: V1 Ch12–13 / V7 Ch15.*

## L

**Latency budget** — The per-stage time allocation guaranteeing first-audio p95 ≤ 1.5 s; the contract operations defends. *Source: V1 Ch23.*

**Law of Authority (RI-5)** — The cornerstone control: the model never owns authoritative facts/money/state/policy; these are computed deterministically + validated. Facts originate in the system of record and are re-read on recovery, never inferred. *Source: V1 App.E / V2 Ch6 / V4 Ch3.*

**Load shedding** — Dropping/deferring lowest-priority work under overload to protect the critical path. *Source: V3 Ch14.*

## M

**Media Gateway** — The real-time telephony/RTP ingress + egress edge of the runtime. *Source: V1 Ch3–4.*

**Metering / Usage** — Idempotent usage events (GPU-seconds, tokens, STT/TTS) reconciled to the immutable event log; the basis for billing. *Source: V5 Ch10.*

**MTTD / MTTR / MTBF** — Mean time to detect (< 5 min) / recover / between failures; core reliability metrics. *Source: V4 Ch23 / V7 Ch11/Ch21.*

## N

**Negotiation `Envelope`** — The bounded space within which settlement/PTP negotiation operates. **Distinct from the `DecisionEnvelope`** — flagged in every cross-volume audit to prevent conflation. *Source: V2 Ch5.*

**Node pool** — A K8s group of like nodes (CPU/media high-priority, GPU tainted, data, system). *Source: V7 Ch5.*

## O

**OOM-by-construction (RI-8)** — The guarantee that the GPU admission control never admits work it cannot fit in VRAM, so out-of-memory is structurally impossible. *Source: V1 Ch7 / App.E.*

**Output Validator / Output Evaluation** — The mandatory gate every utterance passes before TTS (V1 runtime validator; V2 evaluation ≥ Policy strictness); never bypassed (AR-6). *Source: V1 Ch14 / V2 Ch17.*

## P

**Played-offset checkpoint** — The Playback Scheduler's record of how much audio has actually played; used for barge-in + recovery coherence. *Source: V1 Ch21.*

**Playback Scheduler** — The component sequencing synthesized audio to the caller, owning output coherence (RI-6). *Source: V1 Ch21–22.*

**Policy DSL / Policy Engine** — The conversational policy language (V2) and its enterprise superset hosting compliance/security/commercial policy (V4); hard rules (DPDP/RBI) are unweakenable by tenant config. *Source: V2 Ch8 / V4 Ch4.*

**Predictive response / prefetch** — Speculatively assembling context/responses ahead of need to reduce latency. *Source: V2 Ch21.*

**Prompt Builder** — The deterministic component composing the LLM prompt from a sealed plan (RI-7). *Source: V1 Ch12.*

**Prosody** — Speech rhythm, emphasis, and intonation applied in TTS. *Source: V1 Ch18.*

**PTP (Promise To Pay)** — A commitment by a borrower to pay by a date; an authoritative effect captured idempotently. *Source: V5 Ch5.*

## Q

**Queue Manager** — The bounded-queue, retry/backoff, DLQ message infrastructure (at-least-once delivery). *Source: V3 Ch10.*

## R

**RBAC (Role-Based Access Control)** — Authorization by role via the PDP, tenant-scoped. *Source: V4 Ch6.*

**Relationship Memory** — The durable, system-of-record-backed view of a customer relationship (not model memory; carries no authority itself). *Source: V2 Ch10.*

**ResponsePlan** — The sealed, versioned unit of agent output; the governed/billable/analyzable/testable/deployable artifact every turn produces. *Source: V2 Ch15.*

**Risk Engine** — The reasoning engine that can veto a response; never overridden by strategy or fluency. *Source: V2 Ch6.*

**RI-1…RI-8 (Runtime Invariants)** — Real-time-thread purity (1), single-writer (2), bounded buffers (3), commit-before-act (4), Law of Authority (5), ordering/flush coherence (6), deterministic prompt (7), OOM-by-construction (8). *Source: V1 App.E.*

**RPC (Right-Party Contact)** — Reaching the correct borrower (vs wrong party); a collections outcome. *Source: V5 Ch5.*

**RPO / RTO** — Recovery Point Objective (≤ 5 min data loss) / Recovery Time Objective (≤ 30 min restoration). *Source: V3 Ch18 / V7 Ch14.*

## S

**SBOM (Software Bill of Materials)** — The dependency manifest attached to every artifact for supply-chain security. *Source: V4 Ch20 / V6 Ch12.*

**Settlement** — A negotiated resolution of a debt (within the Negotiation Envelope); an authoritative effect. *Source: V5 Ch5.*

**Silero VAD** — The Voice Activity Detection model used for speech detection + endpointing. *Source: V1 Ch6.*

**Single-writer (RI-2)** — The invariant that each mutable state has exactly one writer; cross-task communication is by message, not shared mutation. *Source: V1 App.E.*

**SLO / SLA** — Service Level Objective (internal target driving the error budget) / Agreement (looser customer commitment). Canonical SLOs: first-audio p95 ≤ 1.5 s, availability ≥ 99.95%, 0 dup effects, RPO ≤ 5 m, RTO ≤ 30 m, MTTD < 5 m. *Source: V7 Ch1 / V5 Ch22.*

**SOXR** — The high-quality resampler used in TTS streaming. *Source: V1 Ch20.*

**Strategy Engine** — Selects the conversational approach (within the Four-Class Hierarchy); never overrides policy/risk. *Source: V2 Ch4.*

**Streaming clause / overlap** — Beginning audio synthesis on early clauses to start playback sooner (latency overlap). *Source: V1 Ch23.*

**STT (Speech-to-Text)** — Transcription via Faster-Whisper Large-v3 Turbo FP8. *Source: V1 Ch8.*

## T

**Tenant isolation** — The absolute invariant that no tenant can access another's data/resources; foundation of multi-tenancy. *Source: V4 Ch6 / V5 Ch2 (AR-8).*

**Tombstone** — see Crypto-shred. The immutable erasure marker. *Source: V4 Ch9.*

**Trace ID** — see Correlation ID. The distributed-trace identifier. *Source: V3 Ch17.*

**Transcript** — The text record of a call's speech. *Source: V1 Ch9 / V5 Ch5/7.*

**TTFT (Time To First Token)** — LLM latency to first output token; a key budget line. *Source: V1 Ch13/Ch23.*

**TTS (Text-to-Speech)** — Synthesis via Veena TTS (24 kHz WebSocket). *Source: V1 Ch17.*

**TurnInput** — The normalized per-turn input handed to the intelligence layer. *Source: V1 Ch9.*

## V

**VAD (Voice Activity Detection)** — see Silero VAD. *Source: V1 Ch6.*

**Veena TTS** — The production text-to-speech engine (24 kHz, WebSocket streaming). *Source: V1 Ch17.*

**vLLM** — The LLM serving runtime (continuous batching, paged attention) hosting Qwen2.5-7B-Instruct-FP8. *Source: V1 Ch13.*

**VRAM ledger** — The GPU Scheduler's accounting of VRAM that admission control consults to guarantee OOM-by-construction; reconciled fleet-wide. *Source: V1 Ch7 / V7 Ch6.*

## W

**White-label** — Operating VoiceOS under a partner's brand via configuration (branding/domains/voices/prompts/policy overlay) on the shared platform; cannot weaken regulatory hard rules. *Source: V5 Ch20.*

**Working Memory** — The bounded (RI-3), in-conversation memory of the agent; distinct from authoritative state. *Source: V2 Ch10.*

---

## Acronym quick-reference

| Acronym | Expansion | Source |
|---|---|---|
| ADR / EDR | Architecture / Engineering Decision Record | V*/Ch24; V6 Ch24 |
| AEC | Acoustic Echo Cancellation | V1 Ch5 |
| AR | Architecture Rule | V6 Ch4 |
| BCP | Business Continuity Plan | V7 Ch20 |
| DPD | Days Past Due | V5 Ch5 |
| DR | Disaster Recovery | V7 Ch14 |
| EMI | Equated Monthly Installment | V5 Ch5 |
| IC | Incident Commander | V7 Ch11 |
| IaC | Infrastructure as Code | V7 Ch3 |
| MTTD/MTTR/MTBF | Mean Time To Detect/Recover/Between Failures | V4 Ch23 / V7 |
| OOM | Out Of Memory | V1 Ch7 |
| PDP | Policy Decision Point | V4 Ch6 |
| PII | Personally Identifiable Information | V4 Ch10 |
| PTP | Promise To Pay | V5 Ch5 |
| RBAC | Role-Based Access Control | V4 Ch6 |
| RI | Runtime Invariant | V1 App.E |
| RPC | Right-Party Contact | V5 Ch5 |
| RPO/RTO | Recovery Point/Time Objective | V3 Ch18 |
| SBOM | Software Bill of Materials | V4 Ch20 |
| SLO/SLA | Service Level Objective/Agreement | V7 Ch1 |
| STT/TTS | Speech-to-Text / Text-to-Speech | V1 Ch8/Ch17 |
| TTFT | Time To First Token | V1 Ch13 |
| VAD | Voice Activity Detection | V1 Ch6 |
| VRAM | Video RAM (GPU memory) | V1 Ch7 |

---

## Version history & change log

| Version | Date | Change | Owner |
|---|---|---|---|
| 1.0 | (initial) | Glossary consolidated from Vols 1–7 | Documentation Engineering |

**Change-log policy:** every term introduced or changed in a volume MUST appear here with its source. The suite consistency audit (DocSuite-12) verifies glossary coverage. This glossary supersedes the per-volume glossary appendices for cross-volume lookup but never contradicts them.

*End of Document 11 — Glossary.*
