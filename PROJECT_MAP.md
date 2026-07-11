# VoiceOS v2 — Project Map

> **Purpose of this document:** a single reference for an engineer joining the project — what VoiceOS is, how it's architected, what's actually built vs. spec-only, how it deploys, and where every moving part lives. Produced by a full read-through of the architecture volumes, documentation suite, sprint history, infrastructure code, and application source tree. Read-only research artifact — no code was changed to produce this.
>
> **Snapshot date:** 2026-07-11 (as of Sprint-027 complete, Sprint-028 not started).

---

## 0. Read this first — known gaps and discrepancies in the source material

1. **Architecture Volumes 1 and 2 are truncated on disk.** Volume 1 (Core Voice Architecture) is written through Chapter 6 of a promised 25 and stops with an explicit "End of Batch 1" marker. Volume 2 (Conversation Intelligence) is written through Chapter 5 of a promised 24, same marker pattern. This means the **GPU Scheduler internals, LLM Runtime, Output Validator, Speech Rendering/TTS, Playback Scheduler chapters (Vol 1 Ch.7–25, including the named Ch.26 Runtime Deployment Topology chapter)** and the **Risk Engine, Goal Planner, Relationship/Working Memory, Response Planning/`DecisionEnvelope` chapters (Vol 2 Ch.6–24)** do not exist as authored text — only as citations from later, complete volumes (3–7). Everywhere this document describes those components, it is reconstructed from cross-references, not sourced from a primary chapter. Volumes 3–7 are complete and self-consistent.
2. **Data-store discrepancy.** The architecture docs (Documentation Suite §03/§09) state the canonical stores are **Postgres (authoritative) + append-only event log + Redis (hot, non-authoritative)**, and treat any mention of MongoDB as a documentation artifact to reconcile away. But the actual deployed system (`docs/security/threat-model.md`, and `CPU_NODE_STATE.md` §7.3) runs a real **MongoDB** instance storing transcripts, response plans, decision envelopes, and call lineage. This is either genuine drift from the documented design or an unreconciled doc inconsistency — flagged here rather than silently resolved.
3. **Model version drift.** Configuration Reference (DocSuite-05) cites `llm.model = qwen3-8b`; the actual deployed/running model (per `GPU_NODE_STATE.md`, sprint history, and code) is **Qwen2.5-7B-Instruct-FP8-dynamic**. Treat the deployed reality (this document's §5) as authoritative over the doc's placeholder name.
4. **"Library class" services.** Of the 38 directories under `src/services/`, only **3 have a real wired HTTP entrypoint** (`admin_portal`, `api_platform`, `hitl`) plus a generic mountable health app (`src/libs/health`). Every other service is a fully implemented, fully tested Python library class with no standalone process/port yet — this is a deliberate, tracked gap (backlog item **TT-006**), not an oversight. Kubernetes today runs all 26 chart-defined services behind a **shared placeholder image** (`deployment/k8s/health_stub/`) that only proves the infrastructure path (QoS, PDB, NetworkPolicy, taints) works — not that business logic is reachable over HTTP.
5. **Every production-readiness evaluation report is an unexecuted template.** Load testing, latency validation, chaos engineering, penetration testing, and the RBI/DPDP compliance report under `evaluation/` all have real methodology and gates defined, but every result cell reads `TBD` and the overall `production-alpha-report.md` status is literally `PENDING PHASE 2 EXECUTION`. VoiceOS v2 has been validated against mocks/fakes/deterministic fixtures and a real (but single-node, non-HA) CPU+GPU deployment — it has **not** been validated at production load or under chaos conditions.

---

## 1. What VoiceOS Is

VoiceOS v2 is an enterprise-grade, real-time AI Voice Operating System purpose-built for **collections and customer engagement for Indian financial institutions**, holding natural spoken conversations in Hindi/Hinglish/English. Audio flows from telephony ingress through streaming STT, a deterministic conversation-intelligence layer (intent/strategy/negotiation/risk/empathy/memory), streaming LLM reasoning, streaming TTS, and playback — targeting a first-audible-audio p95 ≤ 1.5s. It's built as a multi-tenant SaaS platform, governed end-to-end by the **Law of Authority**: the LLM renders language but never invents or owns a fact, a number, or a policy decision — all of that comes from deterministic engines reading authoritative systems of record. RBI (calling-hours/frequency/disclosure) and DPDP (consent/retention/erasure) compliance is structural, not bolted on.

The 7-volume architecture + 12-document Documentation Suite is the frozen spec; this repository's job is disciplined, sprint-by-sprint implementation, testing, and deployment — never architecture redesign without an approved ADR.

**Current status (Sprint-027 complete, 27/34 sprints, ~79%):** Core runtime, conversation intelligence, reliability, compliance/security, and SaaS platform layers are fully implemented and unit/integration/e2e-tested (≈2,091 tests passing). A real Kubernetes cluster and a real GPU inference node are deployed and running. Production-scale validation (load, chaos, pen-test, canary rollout — Sprint-028) has not started.

---

## 2. Overall Architecture (7 Volumes)

| Volume | Scope | Status on disk |
|---|---|---|
| **V1 — Core Voice Architecture** | Runtime audio pipeline, streaming, GPU Scheduler, STT/LLM/TTS, playback, deployment topology | Ch.1–6 only (of 25) |
| **V2 — Conversation Intelligence** | Intent/Strategy/Negotiation/Goal Planner/Policy/Risk engines, Working/Relationship Memory, `ResponsePlan`/`DecisionEnvelope` | Ch.1–5 only (of 24) |
| **V3 — Reliability Architecture** | Redis, persistence, event sourcing, recovery/replay, idempotency, health, monitoring/tracing, DR | Complete |
| **V4 — Compliance & Security** | Compliance regimes, security tenets, privacy, RBAC, audit, AI governance | Complete |
| **V5 — SaaS Platform** | Multi-tenancy, CRM, Collections, Campaigns, Contact Center, Billing/Metering, Analytics, Admin, Integrations, Marketplace/Enterprise | Complete |
| **V6 — Engineering Standards** | 20 Architecture Rules (AR-1…20), coding/testing standards, AI coding-agent rules (AGENT-1…12), repo structure | Complete |
| **V7 — Operations & Scaling** | SLOs, deployment model, Kubernetes design, GPU fleet mgmt, monitoring, incident mgmt, capacity, DR, cost | Complete |

**Core data contracts (the architectural spine, sourced only from `src/libs/contracts`, AR-20):**
- **`ResponsePlan`** — immutable, versioned, sealed per-turn object: `intents`, `entities`, `emotion`, `policy_constraints`, `risk_flags`, `goal`, `strategy`, `negotiation_envelope`, `delivery`, provenance-tagged `facts`, `retrieval`, `must_say`/`must_not_say`. The LLM only renders wording from this; everything else is deterministic.
- **`DecisionEnvelope`** — `{decision, confidence, reasoning, evidence, timestamp, source_engine, version}`. Quintuple duty: reasoning record, event-sourcing log entry, audit trail, analytics substrate, ops-forensics data. (Distinct from the Negotiation Engine's `Envelope{min_acceptable, settlement_floor_pct, max_extension_days, allowed_instruments}` — the two are frequently and easily confused.)
- **`EventEnvelope`** — mandatory wrapper for every domain event on the event bus: `event_id`, `event_type`, `schema_version`, `tenant_id`, `occurred_at`, `correlation_id`/`causation_id`/`trace_id`, `payload`, `lineage_ref`.
- **`CustomerContext`** — assembled, authoritative call context (customer + loan + relationship history), a direct Law-of-Authority dependency.

**Four-Class Decision Hierarchy (strict precedence, V2 Ch.1):** Class 1 Policy/Compliance (deterministic, absolute veto) → Class 2 Goals/Strategy → Class 3 Mechanics/Delivery (tone/empathy) → Class 4 Language (the LLM, and only the LLM).

**8 Runtime Invariants (RI-1…RI-8)**, mechanically enforced (`src/libs/invariants/`):
| ID | Invariant |
|---|---|
| RI-1 | Real-time media thread purity — never blocks |
| RI-2 | Single-writer conversation state |
| RI-3 | Bounded buffers/queues everywhere |
| RI-4 | Commit-before-act — durable commit precedes any external effect |
| RI-5 | **Law of Authority** — the LLM never owns an authoritative fact |
| RI-6 | Ordering / flush coherence on playback |
| RI-7 | Deterministic prompt — same sealed plan + version ⇒ identical prompt, always |
| RI-8 | OOM-by-construction — GPU admission control makes VRAM overrun structurally impossible |

**20 Architecture Rules (AR-1…20, V6 Ch.4)** mechanize the above plus: AR-1 never bypass the Conversation Engine; AR-6 nothing reaches TTS without Output Validation; AR-7 never hardcode policy in prompts; AR-8 tenant isolation is absolute; AR-15 idempotency keys on every authoritative state-changing effect; AR-19 no secrets in config; AR-20 no parallel contract definitions outside `libs/contracts`.

---

## 3. Request Flow — one call, end to end

1. **Ingress.** Carrier media (SIP/Twilio-WS/WebRTC) hits **Media Gateway** (`src/services/media_gateway/`) on a CPU/media-plane pod (Guaranteed K8s QoS, high PriorityClass). Auth runs *before* any buffer is allocated (DoS hardening). Codec is transcoded to PCM16 and handed to **Audio Session Manager** (adaptive jitter buffer, packet-loss concealment).
2. **Conditioning & turn-taking.** **Audio Preprocessing** runs AEC3 echo cancellation, noise suppression, AGC, resample 8k→16k (<12ms budget). **VAD & Endpointing** (Silero VAD + hysteresis + adaptive `pause_target`) detects speech onset/offset and separately watches for barge-in (≥280ms sustained speech while agent is talking → flush within <50ms).
3. **Perception.** **STT** (Whisper, via the GPU Scheduler's admission control) transcribes; **Dialogue Manager** assembles a `TurnInput` and hands off to the Conversation Intelligence Layer.
4. **Reasoning** — `ResponsePlanningEngine` (`src/engines/response_planning/`) orchestrates 6 stages:
   - Stage 1 (parallel): Intent Engine, Entity Extractor, Emotion Engine
   - Stage 2: Risk Engine (can veto outright), Dialogue Policy Engine (supplies `must_say`/`must_not_say` from RBI/DPDP rule packs)
   - Stage 3: Strategy Engine (`score(a) = w_g·goal_progress + w_p·P(success) − w_r·risk_cost`), Goal Planner
   - Stage 4 (conditional, only if strategy == NEGOTIATE): Negotiation Engine — pulls authoritative loan facts from `src/services/collections/`, computes a bounded `Envelope`, and **unconditionally clamps** any move to it
   - Stage 5: Empathy Planner (reads emotion, sets tone/pacing)
   - Stage 6: Adaptive Conversation Engine (loop detection, silence recovery)
5. **Sealing the plan.** `ResponsePlanningEngine` assembles the immutable `ResponsePlan`; the turn's reasoning is recorded as a `DecisionEnvelope` and appended to the event log (same object later serves as audit trail, analytics substrate, and ops-forensics data).
6. **Governance gate.** `AIGovernanceService`'s `LawOfAuthorityChecker` verifies every authoritative field carries correct provenance; high-stakes actions (e.g. large settlements) route to Human-in-the-Loop (`src/services/hitl/`) for approval.
7. **Generation.** `PromptBuilder` (`src/engines/prompt_builder/`) deterministically renders the sealed plan into a prompt (RI-7) and calls the LLM Runtime (Qwen2.5-7B-FP8 via vLLM) through the GPU Scheduler.
8. **Validation.** Streamed candidate text passes the Output Validator / `OutputEvaluationEngine` — cross-checks every number against the plan's provenance-tagged facts and the negotiation envelope, screens for toxicity/bias/hallucination. Last deterministic gate before any audio is produced.
9. **Delivery.** Speech Rendering normalizes text for speakability; Voice Style + Prosody engine compose delivery from the Empathy Planner's output; **Veena TTS** streams synthesis per-clause (vLLM `AsyncLLMEngine`, sliding-window SNAC decode) so audio starts before generation finishes; Playback Scheduler paces output and can flush within tens of ms on barge-in; Audio Output resamples 24kHz→8kHz μ-law and hands back to Media Gateway.
10. **Side effects, concurrently.** Any captured commitment (e.g. a Promise-to-Pay) is committed-before-act with an idempotency key into Postgres/Collections workflow state (RI-4); Redis holds only ephemeral, reconstructable hot state; usage is metered immutably for billing; metrics/logs/traces stream continuously into the observability stack.

---

## 4. Service Map (`src/services/`, 38 directories)

| Service | Purpose | HTTP entrypoint? |
|---|---|---|
| admin_portal | Tenant/user/campaign/billing/audit/AI-config admin backend | **Yes** — 21 routes, `/admin/v1/*` |
| ai_config | Prompt versioning, model config inheritance, eval runs | No |
| ai_governance | Law-of-Authority enforcement, GovernanceVerdicts | No |
| analytics | Call/campaign analytics, real-time dashboards, daily rollups | No |
| api_platform | Spec-first public REST API, API-key auth, tiered rate limiting | **Yes** — 6 routes + `/openapi.json` |
| audio_preprocessing | DSP pipeline (AEC3→NS→AGC→Resample) | No |
| audio_session_manager | Per-call jitter buffer, PLC, session clock | No |
| auth | JWT/OIDC, mTLS validation | No (middleware library) |
| authz | RBAC + ABAC, tenant isolation, JIT privileges | No |
| bi_platform | BI warehouse, forecasting, cross-tenant benchmarking | No |
| billing | Subscriptions, entitlements, invoicing, payments | No |
| campaign_management | Audience targeting, RBI-compliant scheduling, A/B testing | No |
| collections | Loan account, EMI/DPD, PTP, settlement, callback, escalation | No |
| compliance_monitoring | Real-time compliance signal correlation + alerting | No |
| contact_center | AI+human blended ops, live transfer, supervisor monitoring | No |
| conversation_engine | Top-level CIL orchestrator ("the brain") | No — internal orchestrator |
| conversation_quality | Async turn-level quality scoring | No |
| cost_optimizer | GPU cost efficiency, instance mix optimization | No |
| crm | Authoritative customer/party records, `CustomerContext` assembly | No |
| dialogue_manager | Turn state machine, `TurnInput` assembly | No |
| gpu_scheduler | System-wide VRAM ledger, admission control, model pool leasing | No |
| hitl | Human review queue, SLA enforcement, override audit | **Yes** — 2 routes |
| incident_response | Incident lifecycle, playbooks, DPDP notification timer | No |
| integration_platform | Signed webhook registration, delivery, retry/DLQ | No |
| knowledge_retrieval | Pre-seeded vector search for collections KB | No |
| llm_runtime | LLM adapter service (vLLM), prompt contract enforcement | No |
| media_gateway | Telephony ingress (Twilio WS, SIP/RTP) | No — transport, not REST |
| metering | Event-driven usage collection, aggregation, limit enforcement | No |
| ops_analytics | Business/technical KPIs, unit economics | No |
| org_management | Organization/BusinessUnit/Branch hierarchy | No |
| playback | TTS playback scheduling + audio output conversion | No |
| policy_engine | Enterprise PDP — PERMIT/DENY/REQUIRE/FORBID DSL (largest service, 18 files) | No |
| reporting | Scheduled reports, CSV/XLSX/PDF export | No |
| saas_ops | Feature flags, fleet rollout rings, tenant data migrations | No |
| stt | Speech-to-Text adapter (Whisper) | No |
| tenant_management | Multi-tenant lifecycle, isolation, provisioning | No |
| tts | Text-to-Speech adapter (Veena), clause splitting, Hindi script conversion | No |
| user_management | User CRUD, invitation workflow, SSO stub (raises `SSONotImplementedError`) | No |
| vad_endpointing | VAD, adaptive endpointing, barge-in, backchannel discrimination | No |

**Framework note:** zero FastAPI usage anywhere — `fastapi` is explicitly not a project dependency. All 3 wired HTTP surfaces use **Starlette** ASGI apps directly.

### Engine Map (`src/engines/`, 17 directories — the Conversation Intelligence Layer)

| Engine | Role |
|---|---|
| intent | Classifies `IntentLabel` (PAYMENT, PROMISE_TO_PAY, DISPUTE, HARDSHIP, ABUSE, …) — Stage 1 |
| entity_extraction | NER over customer turns (amounts, dates) — Stage 1 |
| emotion | Emotion Intelligence, perception layer — Stage 1, feeds Empathy |
| risk | Deterministic risk signal detection (abuse, legal threat), can veto — Stage 2 |
| dialogue_policy | Hard compliance constraint evaluation — Stage 2 |
| strategy | Deterministic next-action selection — Stage 3 |
| goal_planner | Primary goal selection — Stage 3 |
| negotiation | Bounded offer computation, envelope clamping — Stage 4 (conditional) |
| empathy | Tone/pacing/register adaptation — Stage 5 |
| adaptive_conversation | Loop detection, silence recovery — Stage 6 |
| response_planning | Top-level CIL orchestrator, assembles `ResponsePlan` + `DecisionEnvelope` |
| conversation_state | Conversation state machine/transitions |
| memory | `WorkingMemoryStore` (Redis) + `RelationshipMemoryStore` (Postgres) |
| prompt_builder | Deterministic LLM prompt assembly (RI-7) |
| prosody | Maps `EmpathyConfig` → `VoiceConfig` for TTS |
| output_evaluation | Async LLM output quality scoring, non-blocking |
| predictive_response | Early intent caching from partial STT, runs concurrently with streaming STT |

### Library Map (`src/libs/`, 23 directories)

| Lib | Purpose |
|---|---|
| ai_safety | Content moderation, prompt-injection defense, human-oversight routing |
| api_security | Rate limiting, input validation, security headers, CORS |
| audit | Append-only, hash-chained, tamper-evident audit trail |
| circuit_breaker | Fail-fast protection for cross-service calls |
| concurrency | `WorkerPool`, `BoundedQueue` (RI-3), `LoadShedder`, backpressure |
| contracts | Shared typed Pydantic contracts — the architectural spine (AR-20) |
| encryption | AES-256-GCM, envelope encryption, crypto-shredding, KMS adapters |
| event_bus | Redis Streams-backed EventBus — publish/consume/dedup/DLQ/replay |
| health | `/health/live`, `/health/ready` ASGI app builder |
| idempotency | `IdempotencyGuard.execute_once()`, fencing tokens |
| invariants | Runtime guards for RI-1…RI-8 |
| observability | Prometheus metrics, structured JSON logging, OTel tracing |
| performance_engineering | Profiling harness, `BenchmarkSuite`, `RegressionDetector` |
| pii | Detection, redaction, tokenization |
| privacy | Minimization, purpose limitation, right-to-erasure, retention |
| recovery | Per-failure-class recovery strategies (Redis outage, GPU failure, DB outage, …) |
| redis_client | Pooled client, `DistributedLock` (fencing), `RateLimiter`, `TTLGuard` |
| repositories | Tenant-scoped Postgres DAO layer — raw SQL, no ORM (31 files) |
| runtime_security | Container hardening, default-deny network policies |
| secrets | Vault-backed secret fetch/rotation/revocation (`SecretsManager`) |
| service_discovery | Registry, DNS resolution, resilient `ServiceClient` |
| state | Snapshot + event-tail replay (`Recoverable` protocol) |

---

## 5. AI Model Map (deployed, GPU node)

| Model | Purpose | Source | Precision | Port | VRAM (actual) |
|---|---|---|---|---|---|
| Whisper Large-v3 Turbo | STT | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | int8_float16 | 8100 | 1,242 MB |
| Qwen2.5-7B-Instruct-FP8-dynamic | LLM | `RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic` via vLLM 0.24.0 | W8A8 FP8, `--gpu-memory-utilization 0.55` | 8000 | 12,628 MB |
| Veena (3B) + SNAC 24kHz codec | TTS | `maya-research/Veena` + `hubertsiuzdak/snac_24khz` | BF16 (Veena) / FP32 (SNAC) | 8200 | 7,980 MB |
| **Total** | | | | | **21,850 / 23,034 MB (L4 GPU)** |

TTS serving was rewritten in **ADR-001** (Sprint-012 Phase 3) from HuggingFace batch generation to `vllm.AsyncLLMEngine` streaming with a 28-token sliding-window SNAC decode — this cut TTS time-to-first-audio p95 from 8,730ms to 873ms. Public repo weights are used for Veena (BF16), not the internal FP16 registry (VPN-gated, unavailable).

---

## 6. Deployment Map

| Tier | Mechanism | What runs there |
|---|---|---|
| **CPU node** (`101.53.141.75`, real KVM VM, Ubuntu 24.04, unrestricted) | `deployment/cpu/bootstrap.sh` + `restore.sh` | Native Postgres 16.14 / Redis / MongoDB 7.0.37 / Vault processes; kubeadm control plane; all 26 Helm-deployed service pods (health-stub image); full observability stack |
| **GPU node** (`217.18.55.96`, NVIDIA L4, separate provider) | systemd units (`voiceos-{llm,stt,tts}.service`), `Restart=on-failure` | Whisper STT / Qwen2.5 LLM (vLLM) / Veena TTS, all three real, running, systemd-persisted. **Not** a Kubernetes cluster member (see §11) |
| **Kubernetes cluster** (single-node kubeadm v1.36.2 + Calico v3.28.0, co-located with the CPU node) | Helm umbrella chart `voiceos-platform` (25 sub-charts + `saas-ops` templated at umbrella level = 26 services) | Every VoiceOS service as a Deployment behind the shared `health_stub` placeholder image (TT-006) |

The **target/IaC (Terraform+EKS) topology** differs from the above bare-metal reality: `infra/terraform` provisions a real AWS EKS cluster with 4 node pools (cpu, gpu-tainted, data, system), RDS Postgres, ElastiCache Redis, self-hosted MongoDB on EC2, S3 (recordings/exports/backups), and per-service ECR repos — this is the intended production target, not what's currently running.

---

## 7. CPU Node Responsibilities

Per `CPU_NODE_STATE.md` and Volume 3 Ch.2/Volume 7 Ch.5, the CPU node hosts:
- **The real-time media plane**: Media Gateway, Audio Session Manager, Audio Preprocessing, VAD/Endpointing, Playback, Audio Output — all real-time-sensitive, must never block (RI-1), run as Guaranteed-QoS/high-priority pods.
- **Deterministic intelligence orchestration**: all Vol2 engines, event publishing, checkpointing.
- **Data tier**: Postgres 16.14 (authoritative, 54 tables, `voiceos` DB), Redis (hot/non-authoritative), MongoDB 7.0.37 (5 collections: `response_plans`, `decision_envelopes`, `call_transcripts`, `call_lineage`, connectivity test collection), self-hosted Vault (KV v2 + Transit).
- **Kubernetes control plane** (this is also the sole worker node — untainted after `kubeadm init`).
- **Full observability stack**: Prometheus, Grafana, Alertmanager, Loki, FluentBit, OTel Collector, Jaeger — all in the `voiceos-ops` namespace.

Real machine specs: Ubuntu 24.04.4 LTS, kernel 6.8.0-117-generic, 8 CPU cores, 12 GiB RAM, 84 GB disk.

**Known network restriction:** this VM's outbound network blocks GitHub/Docker Hub/PyPI/GCS/Quay/Snapcraft at the TLS/SNI layer (hosting-provider DPI). Workarounds in place: Docker pulls routed through `mirror.gcr.io`; Python packages installed offline via relayed wheels; any `kubectl`/`helm`/GitHub-release binary must be downloaded elsewhere and `scp`'d in.

---

## 8. GPU Node Responsibilities

Per `GPU_NODE_STATE.md`, the GPU node hosts exclusively the three AI inference services (§5 above), governed by the CPU node's GPU Scheduler over HTTP:

| Direction | Protocol | Port | Purpose |
|---|---|---|---|
| CPU GPUScheduler → GPU STT | HTTP | 8100 | STT inference requests |
| CPU GPUScheduler → GPU LLM | HTTP (OpenAI-compatible) | 8000 | LLM completions |
| CPU GPUScheduler → GPU TTS | HTTP | 8200 | TTS synthesis requests |
| GPU services → CPU EventBus | Redis Streams | 6379 | Completion events |

Machine specs: Ubuntu 22.04.5 LTS, 28 CPU cores, 121 GiB RAM, NVIDIA L4 (23,034 MiB VRAM), CUDA 13.0, driver 580.126.20, Docker with `nvidia` default runtime, Python 3.12.13 venv at `/opt/voiceos-gpu/venv`.

Service startup order: **Qwen2.5 LLM first** (vLLM, ~90s warmup) → Whisper STT (~7s) → Veena TTS (~28s) → verify all healthy → signal CPU node GPU Scheduler.

---

## 9. Networking / CPU↔GPU / Kubernetes cluster membership

**The GPU node is not a member of the CPU node's Kubernetes cluster (BACKLOG TT-015).** A `kubeadm join` was attempted (Sprint-026 Phase 2), succeeded at the join step, but Calico's in-cluster bootstrap could not complete: the control plane's advertised address (`10.0.2.2`) is NAT-internal to the CPU VM's hypervisor and unroutable from the GPU node's different cloud provider — a structural "no shared VPC" limitation, not a fixable bug. The join was cleanly reverted; nothing GPU-service-related was touched. This same root cause was later found (Sprint-027) to **also block in-cluster pods** (Prometheus, FluentBit) from reaching `kubernetes.default.svc`'s ClusterIP — Kubernetes-SD-based Prometheus scrape jobs are non-functional; the static-target scrape job is the actually-working path.

**Kubernetes network policy model:** default-deny (`podSelector: {}`, both Ingress+Egress) applied to all 4 namespaces (`voiceos-runtime`, `voiceos-ops`, `voiceos-platform`, `voiceos-data`), then every chart layers its own explicit allow-list NetworkPolicy. Known gap (**TT-016**): generated `egressPorts` only ever cover datastore ports, never peer-service HTTP ports — will need extension once real service-to-service HTTP calls exist.

**GPU node pool/taint strategy (IaC target):** `nvidia.com/gpu=true:NO_SCHEDULE` taint on the EKS GPU node group; only the `gpu-scheduler` chart tolerates it. Currently validated only by static Helm chart inspection plus the one real (if reverted) `kubeadm join`, not by a permanently joined, `Ready` GPU node.

---

## 10. Database Schema Summary

**No ORM anywhere** — all persistence is raw parameterized SQL through `BaseRepository` (`src/libs/repositories/base.py`), which mechanically enforces `tenant_id = %s` as the mandatory first WHERE clause (AR-8).

| Table(s) | Domain |
|---|---|
| `tenants` | Tenant lifecycle |
| `customers`, `customer_contacts`, `customer_addresses`, `parties` | CRM |
| `loan_accounts`, `emi_entries`, `promises_to_pay`, `settlements`, `callback_requests`, `escalation_records`, `call_dispositions` | Collections |
| `campaigns`, `campaign_audiences`, `ab_test_variants`, `campaign_results` | Campaign Management |
| `hitl_queue`, `hitl_decisions` | Human-in-the-loop |
| `billing_subscriptions`, `usage_events`, `invoices` | Billing/Metering |
| `webhook_registrations`, `webhook_deliveries`, `webhook_delivery_attempts`, `webhook_dead_letter_queue`, `api_keys`, `api_key_usage`, `api_rate_limits` | Integration Platform |
| `prompt_versions`, `model_configs`, `campaign_prompt_pins` | AI Config |
| `organizations`, `business_units`, `branches` | Org Management |
| `users`, `roles`, `role_assignments`, `invitations` | User Management |
| `consents`, `consent_records` | Compliance/DPDP |
| `policies` | Policy Engine |
| `idempotency_keys` | Reliability (AR-15) |
| `audit_log` | Hash-chained (`seq`, `hash`), append-only, tamper-evident |
| `feature_flags`, `tenant_rollout_rings`, `fleet_versions`, `tenant_migrations` | SaaS Ops (migration `0026`) |
| `analytics_daily` | Analytics rollups |

Migration head: **`0026`** (Alembic). 54 tables total on the current CPU node. Additional read-only/aggregation repositories exist (`admin_audit_view`, `bi`, `performance_baseline`) not fully enumerated to table level.

**MongoDB** (5 collections, per `CPU_NODE_STATE.md`): `response_plans`, `decision_envelopes`, `call_transcripts`, `call_lineage`, plus one Sprint-003 connectivity-test collection. 22 indexes. See §0.2 for the discrepancy this represents against the documented "Postgres + event log only" design.

---

## 11. Redis Usage

Explicitly documented in code as **non-authoritative — coordination and cache only, never the system of record** (`src/libs/redis_client/client.py`).

1. **Event streaming** — the platform's single event log is one Redis Stream, `voiceos-events` (`src/libs/event_bus/bus.py`), with consumer groups for fan-out and `XRANGE` replay for crash recovery. DLQ is a derived second stream.
2. **Distributed locking** — key prefix `voiceos:lock:`, atomic `SET NX PX` acquire + a Lua compare-then-delete release script (fencing-token pattern, prevents stale-owner release / Redlock split-brain).
3. **Working memory** — `WorkingMemoryStore` (per-call, TTL-disciplined).
4. **Rate limiting** — sliding-window limiter backing `APIRateLimiter` for per-tenant-tier RPS/burst enforcement.
5. **TTL enforcement guard** — `TTLGuard` mechanically enforces that every `SET` in the codebase carries a bounded TTL (CI-checked via `scripts/check_boundaries.py`'s 5th rule — any raw `.set()` call outside `ttl_guard.py` fails).
6. **Snapshots** — keys of the form `snap:{tenant_id}:{call_id}:v{version}` used by the recovery manager for crash recovery.

---

## 12. Vault / Secrets Usage

Hard rule enforced in code (`src/libs/secrets/manager.py`): **no secret is ever read from `os.environ` or a config file** — every value comes from an injected `SecretProvider` at runtime, cached in-process (Fernet-encrypted) for a bounded TTL (default 300s).

- **Backend**: self-hosted HashiCorp Vault v2.0.3 (no cloud KMS account) — KV v2 engine at `secret/` for app credentials, Transit engine at `transit/` for envelope-encryption key wrapping (one KEK per tenant, `tenant-<id>`). An `AWSSecretsProvider` alternative exists — the backend is pluggable/cloud-agnostic.
- **Rotation** — `rotate()` keeps the pre-rotation value valid for an additional grace window (default 60s) so in-flight callers don't fail mid-request.
- **Emergency revocation** — `revoke()` immediately invalidates a cached secret; every fetch is optionally audited (path + actor logged, **never the value**).
- **Consumers**: `src/services/billing/payment.py` (payment gateway keys), `src/libs/encryption/kms_client.py` (`VaultTransitKMSClient`), `src/libs/pii/tokenizer.py` (tokenization keys).
- **Provisioning scripts**: `scripts/vault/bootstrap_vault.sh`, `scripts/vault/provision_datastore_auth.sh` — both fully idempotent, both had real latent bugs found and fixed during the Sprint-026 fresh-Vault-init migration (health-check treating Vault's correct `501 Not Initialized` as failure; a `pipefail`-vs-exit-code bug in the unseal check).

---

## 13. Observability Stack

Deployed for real (Sprint-027) into the `voiceos-ops` namespace, config sourced from `monitoring/` and generated into ConfigMaps at deploy time (never hand-copied):

| Component | Image | Purpose |
|---|---|---|
| Prometheus | `prom/prometheus:v2.54.1` | Scrapes all 26 service pods (static-target job — the actually-functional path — plus a documented-but-broken K8s-SD job), 30d retention |
| Grafana | `grafana/grafana:11.2.2` | 15 dashboards: 6 core (slo-overview, call-funnel, gpu-fleet, latency-breakdown, reliability, business), 5 governance, 4 security |
| Alertmanager | `prom/alertmanager:v0.27.0` | CRITICAL→PagerDuty, WARNING→Jira, INFO→Slack (placeholder webhook URLs — no real accounts provisioned) |
| Loki | `grafana/loki:3.1.1` | Centralized logs, 30d retention |
| FluentBit | `fluent/fluent-bit:3.1.9` (DaemonSet) | Tails every pod's container log → Loki (its `kubernetes` enrichment filter is disabled — found to starve Loki delivery) |
| OTel Collector | `otel/opentelemetry-collector-contrib:0.111.0` | OTLP receiver (4317 gRPC / 4318 HTTP), PII-redaction processor, exports to Jaeger |
| Jaeger | `jaegertracing/all-in-one:1.60` | Trace storage (badger, 7d retention) + query UI (16686) |

**Recording rules**: `first_audio_seconds` p95 burn-rate (5m/30m/1h/6h), `availability` burn-rate, PTP-rate/campaign-completion business ratios — 8 groups / 34 rules.

**Known gaps**: GPU node's STT/LLM/TTS never bind a `/metrics` endpoint (3 Prometheus targets permanently down — TT-017); Kubernetes-SD scrape jobs non-functional (same NAT root cause as TT-015).

---

## 14. Security Stack

- **Trust zones** (per `docs/security/threat-model.md`): External (untrusted) → DMZ (Media Gateway/Public API/Admin Portal/Integration Platform) → Internal mesh (mTLS via `MTLSEnforcer` + self-managed CA) → GPU tier (VRAM ledger/admission control) → Data tier (Postgres/Redis/MongoDB/Vault, highest trust) → Observability (PII-redacted on every log path).
- **Authn**: JWT (RS256, `JWTValidator`, PyJWT), mTLS for service-to-service, API keys for the public API.
- **Authz**: `RBACEngine` + `ABACEvaluator`, tenant isolation enforced mechanically by `TenantIsolationGuard`/`BaseRepository`.
- **Encryption**: AES-256-GCM envelope encryption at rest, per-tenant KEKs via Vault Transit, crypto-shredding for erasure.
- **PII**: `PIIRedactor` on every log path, CI-gated by `scripts/check_pii_logs.py` (3 canary values: phone, Aadhaar, PAN).
- **LLM defense-in-depth**: `PromptInjectionDetector` (heuristic pre-filter) → `PromptContract` (RI-7 structural separation) → `LawOfAuthorityChecker` (the real backstop — blocks fabricated facts regardless of how the model was steered) → `ContentModerator`/`AIOutputValidator` on the way out.
- **Threat registry**: 30 entries (THR-001–030) across all STRIDE categories. Open high-priority items: **THR-001** SIP caller-ID spoofing (no SHAKEN/STIR), **THR-023** RTP volumetric flood (no perimeter DDoS protection — "needs a dedicated ADR before GA"). Open medium items: no API-key rotation policy, Redis Streams events unsigned, no SSRF allow-listing on webhook targets, tenant Redis-namespace isolation is convention- not ACL-enforced, no per-tenant token-budget circuit breaker.
- **Secrets scanning**: `scripts/check_secrets.py` (regex + optional trufflehog) + gitleaks in CI.
- **Cloud IaC posture** (Terraform target): EKS API server private-only, RDS/ElastiCache SGs have zero egress, S3 fully public-blocked + TLS-only, all EC2 enforces IMDSv2. Kyverno `ClusterPolicy`s enforce non-privileged/read-only-rootfs cluster-wide as defense-in-depth.
- **Production audit finding (2026-07-08, TT-020, resolved)**: a real unencrypted SSH private key (`temporary.pem`) was found sitting in the repo root and relocated to `~/.ssh/`.

---

## 15. Compliance

- **RBI**: calling-hours/frequency windows, mandatory disclosures, no coercion — enforced by the Dialogue Policy Engine's hard rules, tested in `tests/compliance/test_rbi_compliance.py` (e.g. 50 out-of-hours calls must all be denied).
- **DPDP**: consent tracking, purpose limitation, retention, right-to-erasure via crypto-shred + tombstone (`DataErasureJob`, produces a `DataErasureCertificate`).
- **PCI DSS**: card-data scope minimized via a compliant payment processor (billing service) — real payment gateway integration (Stripe/Razorpay) is currently an honest stub that always simulates success (**TT-023**, open, must resolve before Pilot/Sprint-030 processes real revenue).
- **AI Governance**: every `ResponsePlan` carries a `GovernanceVerdict{APPROVE|REQUIRE_HUMAN|BLOCK}`; explainability = lineage reconstruction from `DecisionEnvelope`, not a separate model.

---

## 16. CI/CD

**`.github/workflows/ci.yml`** (push/PR to main + feature/fix/hotfix): static-analysis (ruff, mypy --strict, PII-log check) → boundary-check (`check_boundaries.py`) → unit-tests (contracts ≥90%/invariants =100% coverage) → integration-tests (real docker-compose Postgres/Redis/MongoDB) → coverage-gate (overall ≥85%) → secrets-scan (gitleaks + trufflehog + `check_secrets.py`) → docker-build (compose validation) → performance-regression-gate (`check_performance_regression.py`).

**`.github/workflows/release.yml`** (on `vX.Y.Z` tag or manual dispatch): ci-gate → docker-build-push (ghcr.io) → helm-lint (`helm lint --with-subcharts`, `kubeconform`, dry-run apply) → terraform-plan (dev, `tfsec` HIGH-severity gate) → terraform-apply (staging/production — **manual dispatch only**, gated by required GitHub Environment reviewers) → notify.

**`.pre-commit-config.yaml`**: ruff (lint+format+fix), mypy --strict, and a local hook running `pytest tests/unit/ -x -q` on every commit.

**Known gap (TT-021, partially resolved)**: as of the 2026-07-08 production audit, this CI/CD pipeline had **never actually executed once** — no `.git` repository existed until that audit initialized one. A first local commit was made but no remote/push existed yet at that point, so GitHub Actions remained unproven-in-practice even though the workflow files themselves are complete and well-authored.

---

## 17. Production Rollout Process

Two-phase, per V7 Ch.1 and `evaluation/production-alpha-report.md`:

1. **Phase 1 (current state, complete)**: implement + unit/integration/e2e/compliance tests against mocks/fakes/real-Postgres pass; all CI quality gates pass; chaos/load/latency/pen-test/compliance-report *scripts and templates* exist and are syntactically validated but not run against real infrastructure.
2. **Phase 2 (gated, not started — Sprint-028)**: execute against real production/staging infra — latency validation (100 calls, warm GPU, p95 ≤1.5s), load test (500 concurrent, 45min, p95 ≤1.65s/GPU util ≤0.80/error<0.1%), all 5 chaos scenarios pass, pen test returns zero critical/exploitable-high findings, RBI/DPDP suite passes 100% on staging.
3. Only once **all six evaluation reports are green** does the **canary rollout** begin: 5%→25%(1hr hold)→50%(1hr hold)→100%, auto-rollback if error rate >1% or first-audio p95 >2s at any step.
4. Sign-off requires 4 named roles (Deployment Lead, Engineering Lead, Production Readiness Owner, On-Call Lead) — all currently `TBD`.
5. Per-sprint development/deploy loop (already in practice for Sprints 1–27): implement → lint/type/unit tests locally against fakes → deploy/apply against real CPU-node infra + run `scripts/sprintNNN_infra_validation.py` + full regression suite against real datastores → GPU node changes require **explicit per-instance user approval** every time (standing rule) → update `CURRENT_SPRINT.md`/`DONE.md`/`BACKLOG.md`/`CHANGELOG.md`/`PROJECT_STATUS.md` → stop (never auto-start the next sprint).

---

## 18. External Dependencies (complete, from `pyproject.toml`)

**Core runtime:**
| Package | Purpose |
|---|---|
| `pydantic>=2.7` | Typed contracts (`libs/contracts`) |
| `prometheus-client>=0.20` | Metrics |
| `numpy>=1.26`, `scipy>=1.13` | Audio DSP (NLMS filter, FFT, AGC, polyphase resampling) |
| `onnxruntime>=1.18` | Silero VAD v4 ONNX inference (CPU) |
| `httpx>=0.27` | Async HTTP + SSE streaming to vLLM/Veena adapters |
| `redis>=5.0` | EventBus, distributed lock, rate limiter |
| `psycopg2-binary>=2.9`, `alembic>=1.13`, `sqlalchemy>=2.0` | Postgres driver + migrations (SQLAlchemy used only as Alembic's connection layer — no ORM models) |
| `starlette>=0.37`, `uvicorn>=0.30` | ASGI health app + real service HTTP entrypoints |
| `opentelemetry-api/-sdk/-exporter-otlp-proto-http>=1.25` | Distributed tracing |
| `pyjwt>=2.8`, `cryptography>=42` | JWT validation, mTLS X.509 |
| `hvac>=2.1` | HashiCorp Vault client (lazy-imported) |
| `openpyxl>=3.1`, `reportlab>=4.2` | XLSX/PDF report export |
| `pyyaml>=6.0` | OpenAPI spec loading |

**Dev/test only:** `pytest>=8.3`, `pytest-cov`, `pytest-asyncio`, `ruff>=0.5`, `mypy>=1.11`, `pre-commit>=3.7`, `pymongo>=4.7` (integration test driver), `locust>=2.29` (load testing).

**GPU node (from `GPU_NODE_STATE.md`, not in root `pyproject.toml` — separate venv):** `torch==2.11.0+cu130`, `torchvision`, `torchaudio`, `vllm==0.24.0`, `faster-whisper==1.2.1`, `grpcio`/`grpcio-tools`, `fastapi==0.136.3` (GPU-side only — the CPU-side app has no FastAPI dependency), `uvicorn`, `prometheus-client`, `opentelemetry-sdk`/`-exporter-otlp`, `huggingface-hub`.

**Infra tooling (not Python):** Docker 29.x, kubeadm v1.36.2, Calico v3.28.0, Helm v3.16.3, Terraform (AWS provider), containerd.

---

## 19. Complete Port Table

**GPU node (bare processes):**
| Service | Port |
|---|---|
| STT (Whisper) | 8100 |
| LLM (vLLM/Qwen2.5) | 8000 |
| TTS (Veena+SNAC) | 8200 |

**Kubernetes services (Helm chart values):**
| Service | Port | Service | Port |
|---|---|---|---|
| media-gateway | 8010 | contact-center | 8034 |
| audio-preprocessing | 8011 | billing | 8035 |
| vad-endpointing | 8012 | metering | 8036 |
| gpu-scheduler | 8013 | analytics | 8037 |
| conversation-engine | 8014 | admin-portal | 8038 |
| dialogue-manager | 8015 | ai-config | 8039 |
| stt | 8100 | integration-platform | 8040 |
| llm-runtime | 8000 | api-platform | 8041 |
| tts | 8200 | saas-ops | 8042 |
| policy-engine | 8020 | tenant-management | 8030 |
| auth | 8021 | crm | 8031 |
| authz | 8022 | collections | 8032 |
| ai-governance | 8023 | campaign-management | 8033 |

**Observability (voiceos-ops):** Prometheus 9090 · Grafana 3000 · Alertmanager 9093 · Loki 3100 · FluentBit 2020 · OTel Collector 4317(gRPC)/4318(HTTP)/8889(Prom export) · Jaeger 16686(query)/4317/4318(OTLP).

**Data tier / local dev (`docker-compose.yml`):** Postgres 5432 · Redis 6379 · MongoDB 27017 · node-exporter 9100 · redis-exporter 9121 · postgres-exporter 9187.

**Health stub image** (`deployment/k8s/health_stub/`): 8080 (`/health/live`, `/health/ready`, `/metrics`).

---

## 20. Startup Dependency Order

**GPU node** (`deployment/gpu/systemd/`, per `model_manifest.yaml`):
1. NVIDIA driver verified (`nvidia-smi`)
2. Qwen2.5 LLM via vLLM — longest warmup, ~90s
3. Whisper STT — ~7s
4. Veena TTS + SNAC — ~28s
5. Verify all three `/health/ready`
6. Signal CPU node's GPU Scheduler

**CPU node** (`deployment/cpu/bootstrap.sh` → `restore.sh`):
1. OS packages, Python 3.12, Docker, kubectl, Helm
2. Native Postgres / Redis / MongoDB startup
3. `pip install -e .`, Alembic migrations to head
4. Mongo index creation
5. mTLS PKI provisioning (`scripts/pki/generate_mtls_certs.py`)
6. Vault bootstrap + datastore-auth provisioning
7. PII backfill
8. If a live K8s cluster is reachable: `helm upgrade --install voiceos-platform` + observability stack deploy

**Kubernetes cluster bootstrap** (`kubeadm init` sequence): swap off → kernel modules (`overlay`, `br_netfilter`) + sysctls → containerd CRI config → kubeadm/kubelet/kubectl install → `kubeadm init` → Calico CNI → untaint (single-node) → label `voiceos.io/node-pool=cpu` → apply `infra/k8s/{namespaces,priority-classes,resource-quotas}.yaml` + default-deny policy → `helm dependency update` + `helm upgrade --install`.

---

## 21. All APIs

No FastAPI anywhere; all real HTTP surfaces are Starlette ASGI apps.

**Public API** (`src/services/api_platform/api.py`, spec: `api-specs/voiceos-public-v1.yaml`, OpenAPI 3.1, `X-API-Key` auth):
| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/customers/{id}` | Fetch customer record |
| GET | `/v1/calls/{id}` | Fetch call disposition |
| POST | `/v1/campaigns` | Create dialler campaign (starts DRAFT) |
| GET | `/v1/campaigns/{id}/analytics` | Aggregated campaign analytics |
| POST | `/v1/webhooks` | Register a webhook |
| GET | `/v1/invoices` | List invoices |
| GET | `/v1/openapi.json` | Spec served verbatim (unprotected) |

**Admin Portal API** (`src/services/admin_portal/api.py`, `/admin/v1/*`, requires ADMIN/SUPERVISOR JWT role) — 21 routes covering: tenants (list/suspend/reactivate), users (list/invite/deactivate/SSO-config), campaigns (list/approve), billing (subscription/invoices/usage), audit (search/compliance-report/admin-actions), AI config (create/publish/edit prompt versions), API keys (list/issue/rotate/revoke).

**HITL Review API** (`src/services/hitl/review_api.py`): `GET /hitl/queue`, `POST /hitl/items/{item_id}/decision`.

**Health app** (`src/libs/health/aggregator.py`, mountable by any service): `GET /health/live`, `GET /health/ready`.

**Documented-but-not-yet-implemented public API surface** (Documentation Suite §04, broader than what's actually coded — describes the intended full public API): also lists `/v1/loans/{loan_id}/ptp`, `/v1/loans/{loan_id}/settlement`, `/v1/customers/{id}/history`, `/v1/calls` (list), `/v1/calls/{id}/transcript`, `/v1/analytics/kpis`, `/v1/reports`, `/v1/workflows`, plus a WebSocket stream `wss://.../v1/stream/calls/{call_id}` and outbound signed webhook events (`call.connected`, `ptp.captured`, `settlement.recorded`, etc.) — these are architecture-spec-level, not yet all present in `src/services/api_platform/`.

---

## 22. Event Bus Topics

**Physically, one Redis Stream**: `voiceos-events` (`src/libs/event_bus/bus.py`). Routing is logical, via each event's `event_type` string inside a shared `EventEnvelope`, dispatched through `EventRouter` + Redis consumer groups. DLQ is a second, derived stream.

| File | `event_type` values |
|---|---|
| `audio_events.py` | `audio.session.started`, `audio.frame.received`, `audio.barge_in.detected`, `audio.vad.speech_start`, `audio.vad.speech_end`, `audio.session.ended`, `audio.backchannel.detected` |
| `dialogue_events.py` | `dialogue.turn.started`, `dialogue.turn.completed`, `dialogue.response_plan.created`, `dialogue.playback.started`, `dialogue.playback.completed`, `dialogue.playback.flushed` |
| `intelligence_events.py` | `intelligence.intent.classified`, `intelligence.entity.extracted`, `intelligence.risk.flag_raised`, `intelligence.strategy.selected`, `intelligence.negotiation.move_proposed`, `intelligence.response_plan.assembled` |
| `saas_events.py` | `saas.tenant.provisioned`, `saas.tenant.suspended`, `saas.customer.created`, `saas.loan_account.updated`, `saas.ptp.created`, `saas.ptp.broken`, `saas.settlement.offered/accepted/authorized/disbursed`, `saas.callback.scheduled`, `saas.escalation.triggered`, `saas.campaign.started/completed`, `saas.call.transferred/dispositioned`, `saas.stt.transcribed`, `saas.llm.generated`, `saas.gpu.allocated`, `saas.usage.event_recorded`, `saas.billing.invoice_generated` |
| `reliability_events.py` | `reliability.snapshot.created`, `reliability.recovery.started/completed`, `reliability.idempotency.key_created`, `reliability.circuit_breaker.opened/closed`, `reliability.gpu.failover_started/completed` |
| `compliance_events.py` | `compliance.consent.recorded/revoked`, `compliance.policy.decision_made`, `compliance.audit.event_emitted`, `compliance.pii.redacted`, `compliance.data.erasure_requested`, `compliance.hitl.item_enqueued/sla_breached/decision_recorded` |

`WebhookService` (`src/services/integration_platform/`) is documented as **the first real EventBus consumer**, mapping `PTPBroken` and `CampaignCompleted` to outbound webhook deliveries.

---

## 23. Sprint History — where architecture changed

| Sprint | Date | Architecture-changing? | What changed |
|---|---|---|---|
| 001–008 | 2026-06-30 | — | Contracts/invariants, domain events, test infra, Media Gateway, ASM, audio preprocessing, VAD/endpointing, GPU Scheduler — all net-new, no prior architecture to change |
| 009 | 2026-07-03 | **Yes** | First real GPU model deployment (Whisper/vLLM-Qwen/Veena) and adapter contracts established |
| 012 Phase 3 | 2026-07-04 | **Yes — ADR-001** | TTS serving layer: HF batch generation → `vllm.AsyncLLMEngine` streaming (8,730ms→873ms TTFA p95) |
| 018 | 2026-07-04 | **Yes** | Auth/RBAC/mTLS PKI; AI Governance gate made mandatory (security posture change) |
| 019 | 2026-07-05 | **Yes** | Self-hosted Vault (KV+Transit) introduced — new external dependency, security posture change |
| 026 | 2026-07-07 | **Yes — ADR-003** | Full IaC (Terraform/Helm/K8s) + CPU node migration from a capability-restricted platform pod to a genuinely unrestricted VM; real kubeadm+Calico cluster stood up |
| 025 Part-3 | 2026-07-07 | Declined — **ADR-002** | A scope-expansion request (5 new tables, deep cross-subsystem wiring) was explicitly declined as scope creep; only in-spec additive pieces implemented |
| Post-Sprint-027 audit | 2026-07-08 | **Yes** | Production-readiness audit (TT-018…023): `metrics-server` installed (HPAs were all non-functional before this), real secret hygiene fix (private key removed from repo root), git/CI actually initialized for the first time |
| 028 | Not started | Pending | Performance validation, load testing, pen test, canary production-alpha deploy |

**All 3 ADRs:**
- **ADR-001** (Approved) — vLLM streaming TTS rewrite, model unchanged, serving layer only.
- **ADR-002** (Resolved) — Sprint-025 scope-expansion request declined; implemented only additive pieces.
- **ADR-003** (Approved/implemented) — CPU node migration to an unrestricted VM; real Kubernetes stood up; GPU-cluster-join attempted and reverted (became TT-015).

---

## 24. Known Backlog / Gaps (production-readiness relevant)

| ID | Status | Issue |
|---|---|---|
| TT-006 | Open | No service has a standalone HTTP listener except 3 — the rest are library classes behind a placeholder image |
| TT-010 | Open (Medium) | TTS time-to-first-chunk regression observed (914–2974ms vs. documented 873ms p95), not root-caused |
| TT-001-residual | Open (Medium) | End-to-end pipeline p95=6,048ms vs. 1.5s target — LLM and TTS run sequentially, not concurrently |
| TT-015 | Open (Medium) | GPU node cannot join the K8s cluster — no shared VPC between the two cloud providers |
| TT-016 | Open (Low) | Generated NetworkPolicy `egressPorts` never cover peer-service HTTP ports |
| TT-017 | Open (Low) | GPU node's 3 inference services never bind `/metrics` |
| TT-019 | Open (Medium-High) | Single-node, non-HA control plane; an unexplained ~10-minute multi-pod crash-loop window was observed with no historical metrics to diagnose it |
| TT-021 | Partially resolved | CI/CD had never actually executed once until git was initialized on 2026-07-08 |
| TT-023 | Open (Medium) | No real payment gateway integration — Stripe/Razorpay are honest always-succeed stubs; must resolve before Pilot processes real revenue |

---

## 25. Testing & Evaluation Framework (summary — see `tests/` and `evaluation/`)

| Layer | Coverage |
|---|---|
| Unit (124 files) | Engines, contracts, libs, repositories, services in isolation |
| Integration (30 files) | Real Postgres/Redis/MongoDB, tenant isolation, GPU scheduler, migrations |
| E2E | Walking Skeleton — 20-turn Hindi/Hinglish conversation, mocked AI, p95 ≤1500ms gate |
| Chaos (5 scenarios) | GPU/Redis/Postgres kill, 20% packet loss, pod kill — script exists, `--dry-run` by default, real execution not yet run |
| Compliance | RBI calling-window/frequency, DPDP consent/erasure — real `PolicyEngine`, no external backends |
| Load | Locust against real GPU inference endpoints — p95 ≤1650ms / error <0.1% gate |
| AI Eval | Intent accuracy (100-sample labeled Hindi/Hinglish set, ≥90% gate) |
| Invariants | Smoke-tests all 8 RI guard functions are wired |

**CI quality gates**: `check_boundaries.py` (module-boundary + Redis-TTL-discipline AST scan), `check_performance_regression.py` (stage-budget regression, 10% threshold), `check_pii_logs.py`, `check_secrets.py`, `validate_grafana_dashboards.py`, `validate_observability_configs.py`, `validate_openapi.py`.

**Production-alpha status**: every evaluation report (`evaluation/{latency-validation,load-testing,chaos,security,compliance}/`) is a real, detailed methodology document whose result cells are all `TBD` — Phase 2 execution against real production infrastructure has not started.

---

## 26. Glossary — key terms

- **Law of Authority (RI-5)** — the LLM never owns authoritative facts/money/state/policy.
- **ResponsePlan** / **DecisionEnvelope** / **EventEnvelope** — see §2.
- **Negotiation `Envelope`** — bounded settlement/PTP negotiation space; not the same as `DecisionEnvelope`.
- **OOM-by-construction (RI-8)** — GPU admission control makes VRAM overrun structurally impossible.
- **Deterministic prompt (RI-7)** — same sealed plan + version ⇒ byte-identical prompt.
- **PTP** — Promise To Pay, an authoritative, idempotently-captured borrower commitment.
- **DPD** — Days Past Due, drives campaign segment selection.
- **Crypto-shred + tombstone** — erasure method: destroy the per-record key, leave an immutable tombstone.
- **Drain-aware deploy** — scale-down/deploy finishes in-flight calls, drops nothing.
- **Founder validation** — human expert qualitative go/no-go gate, blocking for Pilot.
- **Error budget** — `1 − SLO`; exhausted budget freezes risky changes.

---

*Generated from a full read-through of the repository as it exists on branch `claude/ssh-gpu-cpu-servers-y99fib`. No source files were modified to produce this document.*
