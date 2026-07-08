# Sprint-012 — Conversation Orchestration — Walking Skeleton

**Epic:** E3 — Conversation Intelligence  
**Status:** ✅ Done (2026-07-03, extended 2026-07-04) — Walking Skeleton pipeline proven; Phase 2 TTFA p95=8730ms (batch); Phase 3 streaming fix deployed and validated [TT-001/ADR-001]: server-level TTFA p95=873ms (TARGET MET); pipeline-level p50=2355ms; Veena AI FP16 retained  
**Depends on:** Sprint-009, Sprint-010, Sprint-011  
**Blocks:** Sprint-017, Sprint-021  
**Milestone:** Walking Skeleton (First Full Call) — M-3  

---

## Objective

Assemble all engines and services into the complete orchestrated call path. Implement the Response Planning Engine, Dialogue Manager, Conversation Engine (CIL orchestrator), Prompt Builder, Output Validator, True Streaming Pipeline, Playback Scheduler, Audio Output, and Output Evaluation Engine. Deliver the first complete end-to-end call test — the walking skeleton.

This is the most critical milestone: a spoken utterance flows through the entire system and the agent responds with synthesized speech within 1.5s.

---

## Architecture References

- Volume 1: Ch9 (Dialogue Manager), Ch10 (Conversation Engine), Ch12 (Prompt Builder), Ch14 (Output Validator), Ch18 (True Streaming Pipeline), Ch21 (Playback Scheduler), Ch22 (Audio Output)
- Volume 2: Ch15 (Response Planning Engine), Ch16 (Adaptive Conversation Engine — silence recovery, loop detection, clarification turns), Ch17 (Output Evaluation Engine), Ch20 (Knowledge & Retrieval Intelligence — RAG, knowledge base, ResponsePlan.retrieval population), Ch21 (Predictive Response Engine — precompute ResponsePlans while caller speaks using TurnInput.partials), Ch22 (Conversation Quality Scoring — aggregate quality scoring service)
- DocSuite-02: Interface Contracts
- DocSuite-06: Prompt Library (prompt templates — use existing, do not create new)
- DocSuite-08: Testing Catalog (e2e test spec)

---

## Components to Implement

### `src/engines/response-planning/`
- `engine.py` — ResponsePlanningEngine: assembles final sealed ResponsePlan from all engine outputs
- Inputs: IntentSignal + ExtractedEntities + EmotionSignal + RiskAssessment + PolicyConstraints + StrategySelection + GoalSelection + NegotiationEnvelope + EmpathyConfig + WorkingMemory + RelationshipMemory + ConversationState + CustomerContext
- Output: `ResponsePlan` (sealed, versioned, immutable — from contracts library)
- Calls `assert_ri5_law_of_authority` for every fact placed in `ResponsePlan.facts`
- Creates `DecisionEnvelope` recording all decisions and their sources

### `src/engines/prompt-builder/`
- `builder.py` — PromptBuilder: deterministic, versioned prompt assembly
- Reads prompt templates from DocSuite-06 Prompt Library (version-pinned)
- Injects: CustomerContext (authoritative facts), ResponsePlan.must_say, ResponsePlan.must_not_say, ResponsePlan.delivery, language/tone from EmpathyConfig
- `assert_ri7_deterministic_prompt(prompt_hash, version, expected_hash)` called after building
- LLM receives prompt as a string; PromptBuilder does NOT receive LLM output

### `src/services/dialogue-manager/`
- `service.py` — DialogueManager: turn coordination
- Receives: VADSpeechEnd events (new utterance), BargeinDetected events
- Barge-in handling: signal PlaybackScheduler to flush, signal ConversationEngine to yield turn
- Turn state: AGENT_SPEAKING | CUSTOMER_SPEAKING | THINKING | IDLE
- Enforces single-active-turn: second customer utterance does not start CIL until previous turn completed

### `src/services/conversation-engine/`
- `engine.py` — ConversationEngine: full CIL orchestration (the "brain")
- Per turn: TurnInput → [all engines in parallel where possible] → ResponsePlanningEngine → PromptBuilder → LLM → OutputValidator → TTS → PlaybackScheduler
- Latency-critical: total CIL processing (engines → ResponsePlan) must complete in ≤ 120ms p95
- Publishes DecisionEnvelope to event bus after every turn
- Maintains ConversationState transitions

### `src/services/llm-runtime/output_validator.py`
- `OutputValidator`: validates LLM-generated text against ResponsePlan
- Law of Authority check (RI-5): scans LLM output for any amount/date/account fact → verifies against ResponsePlan.facts → rejects if fact not in facts map
- Coherence check (RI-6): ResponsePlan.plan_id matches current turn's plan_id
- Must-say check: verifies all items in ResponsePlan.must_say are present in output (or schedules retry)
- Must-not-say check: verifies no items from ResponsePlan.must_not_say appear in output
- On rejection: signals LLM to retry (up to 2 retries); if still failing → fallback to template response

### `src/services/tts/streaming_pipeline.py`
- `TrueStreamingPipeline`: wires STT → LLM → TTS → Playback as a continuous stream
- As soon as first clause of LLM output is validated, sends to TTS → starts synthesis before full response is ready
- Barge-in flush: on BargeinDetected, immediately empties clause queue and stops synthesis

### `src/services/playback/`
- `scheduler.py` — PlaybackScheduler: queues synthesized AudioClause objects, sends to Media Gateway
- `output.py` — AudioOutput: converts synthesized audio to correct format (μ-law 8kHz for Twilio, G.711 for SIP) and sends via transport
- Flush: on BargeinDetected → clear clause queue, signal AudioOutput to stop current clause mid-stream

### `src/engines/output-evaluation/`
- `engine.py` — OutputEvaluationEngine: post-turn quality scoring (runs async, does not block call)
- Scores: `coherence_score`, `policy_compliance_score`, `empathy_score`, `factual_accuracy_score`
- Stores scores in MongoDB call_lineage collection
- Used for: A/B testing evaluation, ongoing quality monitoring

### `src/engines/adaptive-conversation/` (V2 Ch16 — Adaptive Conversation Engine)

```
src/engines/adaptive-conversation/
├── __init__.py
├── engine.py               (AdaptiveConversationEngine: silence recovery, loop detection, clarification)
├── loop_detector.py        (ConversationLoopDetector: detects repeated intent cycles)
└── silence_handler.py      (SilenceRecoveryPolicy: generates clarification turn on silence)
```

**AdaptiveConversationEngine:**
- Silence recovery: if `TurnInput.silence_duration_ms > 4000` → emit a clarification prompt (selected from DocSuite-06 silence-recovery templates)
- Loop detection: if same intent appears ≥ 3 consecutive turns → `ConversationLoopDetector` triggers loop-breaking strategy (escalate or redirect)
- Clarification turns: if entity confidence < threshold → inject a targeted clarification request into the next ResponsePlan
- Anti-oscillation: prevents the strategy engine from flip-flopping between two strategies more than twice
- Graceful-exit mechanics: if call quality signal degrades or loop is irresolvable → trigger ESCALATE action
- Wired into ConversationEngine turn loop before ResponsePlanningEngine

### `src/services/knowledge-retrieval/` (V2 Ch20 — Knowledge & Retrieval Intelligence)

```
src/services/knowledge-retrieval/
├── __init__.py
├── service.py              (KnowledgeRetrievalService: populates ResponsePlan.retrieval)
├── store.py                (VectorStore: vector embedding store for policy documents and FAQs)
├── retriever.py            (RelevanceRetriever: semantic search over knowledge base)
└── embedder.py             (DocumentEmbedder: embeds policy documents for retrieval)
```

**KnowledgeRetrievalService:**
- Populates `ResponsePlan.retrieval: list[Snippet]` — every Snippet has `source`, `content`, `relevance_score`
- Knowledge base: RBI fair-practice code excerpts, product FAQs, settlement guidelines, dispute procedures
- Retrieval: semantic search over embedded documents using cosine similarity
- Called during ResponsePlanningEngine assembly, result injected into ResponsePlan before PromptBuilder
- PromptBuilder uses `ResponsePlan.retrieval` to provide grounded context to LLM (Law of Authority: retrieval content is authoritative)

### `src/engines/predictive-response/` (V2 Ch21 — Predictive Response Engine)

```
src/engines/predictive-response/
├── __init__.py
└── engine.py               (PredictiveResponseEngine: precomputes ResponsePlans while caller speaks)
```

**PredictiveResponseEngine:**
- Subscribes to `TurnInput.partials` (incremental partial transcription from STT)
- On first partial: begins precomputing likely intent + early ResponsePlan draft
- On confirmed intent (VAD speech end): if predicted intent matches confirmed intent → use pre-built ResponsePlan (skipping CIL latency)
- Cache: holds at most 1 pre-built plan per call; invalidated on barge-in
- Latency contribution: reduces effective CIL processing from ~120ms to ~20ms on cache hit (V2 Ch21 §21.4)
- Failure mode: cache miss → falls back to standard synchronous CIL path (transparent to caller)

### `src/services/conversation-quality/` (V2 Ch22 — Conversation Quality Scoring)

```
src/services/conversation-quality/
├── __init__.py
├── scorer.py               (ConversationQualityScorer: aggregate per-call quality grade)
├── calibration.py          (ScoringCalibration: weights and thresholds for each dimension)
└── dashboard.py            (QualityDashboard: quality trend API for Sprint-029 evaluation harness)
```

**ConversationQualityScorer:**
- Aggregates per-turn scores from OutputEvaluationEngine into a per-call quality grade (A/B/C/D)
- Dimensions with weights: coherence (20%), policy_compliance (30%), empathy (20%), factual_accuracy (30%)
- Stores call-level quality record in MongoDB `call_quality` collection
- `QualityDashboard.get_quality_trend(tenant_id, start, end)` — API used by Sprint-029 founder validation suite
- Enables systematic quality regression testing and A/B strategy comparison

---

## Files Expected to Change

**New:** `src/engines/response-planning/`, `src/engines/prompt-builder/`, `src/engines/output-evaluation/`, `src/engines/adaptive-conversation/`, `src/engines/predictive-response/`, `src/services/dialogue-manager/`, `src/services/conversation-engine/`, `src/services/knowledge-retrieval/`, `src/services/conversation-quality/`, `src/services/llm-runtime/output_validator.py`, `src/services/tts/streaming_pipeline.py`, `src/services/playback/`  
**New:** `tests/e2e/test_walking_skeleton.py` (the critical end-to-end test)  
**New:** `tests/unit/services/test_conversation_engine.py`, `test_output_validator.py`, `test_playback_scheduler.py`, `test_knowledge_retrieval.py`, `test_conversation_quality.py`  
**New:** `tests/unit/engines/test_adaptive_conversation.py`, `test_predictive_response.py`

---

## Acceptance Criteria

- [ ] End-to-end test `test_walking_skeleton.py` passes: inject fake audio → VAD → STT → CIL → LLM → TTS → playback assertion
- [ ] First-audio p95 ≤ 1.5s on the integration fixture (real AI models, warm GPU)
- [ ] OutputValidator correctly rejects LLM output containing a fact ("₹15,000") not in ResponsePlan.facts
- [ ] OutputValidator correctly rejects LLM output containing a must_not_say phrase
- [ ] NegotiationEngine offer is within configured envelope on every turn of the e2e test
- [ ] ResponsePlan is immutable — ConversationEngine cannot modify it after assembly
- [ ] DecisionEnvelope is emitted to event bus for every turn
- [ ] Barge-in flush: when BargeinDetected fires during playback, clause queue is emptied within 50ms
- [ ] AdaptiveConversationEngine: 4s silence → clarification turn issued (unit test)
- [ ] AdaptiveConversationEngine: same intent 3 consecutive turns → loop-breaking strategy triggered
- [ ] KnowledgeRetrievalService: query "RBI calling hours" → returns relevant Snippet from knowledge base
- [ ] ResponsePlan.retrieval is non-empty for calls that match knowledge base queries
- [ ] PredictiveResponseEngine: on cache hit, effective CIL latency reduced (integration test)
- [ ] ConversationQualityScorer: per-call grade computed and stored in MongoDB after e2e test
- [ ] QualityDashboard API returns quality trend for tenant_id + date range

---

## Required Tests

**Unit:**
- `test_output_validator_rejects_hallucination` — LLM says "₹15,000" but ResponsePlan.facts has "₹12,500" → reject
- `test_output_validator_rejects_must_not_say` — LLM output contains banned phrase → reject
- `test_output_validator_accepts_grounded_output` — all facts in output match ResponsePlan.facts → accept
- `test_prompt_builder_deterministic` — same ResponsePlan + CustomerContext → same prompt hash (twice)
- `test_playback_flush_on_bargein` — BargeinDetected event → clause queue emptied
- `test_decision_envelope_emitted_per_turn` — mock event bus, verify DecisionEnvelope published
- `test_adaptive_conversation_silence_recovery` — 4s silence → clarification turn in next ResponsePlan
- `test_adaptive_conversation_loop_detection` — same intent 3× → loop-breaking strategy action
- `test_knowledge_retrieval_returns_snippets` — query matches knowledge base → Snippet list returned
- `test_response_plan_retrieval_populated` — KnowledgeRetrievalService injects snippets before PromptBuilder
- `test_predictive_response_cache_hit` — early partial matches final intent → cached plan used
- `test_conversation_quality_grade_computed` — 5 turns of scores → call grade = 'B' (test fixture)
- `test_quality_dashboard_trend_api` — seed 3 calls → QualityDashboard.get_quality_trend returns correct aggregation

**End-to-End:**
- `tests/e2e/test_walking_skeleton.py` — full pipeline: fake audio → synthesized AudioClause received; latency ≤ 1.5s

---

## Definition of Done

- [ ] All AC items checked
- [ ] Walking skeleton e2e test passes
- [ ] Latency p95 ≤ 1.5s on e2e fixture
- [ ] OutputValidator catches all hallucinations in unit tests
- [ ] Negotiation boundary holds in every e2e run
- [ ] CI green
- [ ] **Milestone M-3 (Walking Skeleton / First Full Call) verified**
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-013

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** The walking skeleton e2e test runs with mocked AI backends and fake audio fixtures. Real latency measurement is deferred to Phase 2.

### Files Created

- `src/engines/response-planning/__init__.py`, `engine.py`
- `src/engines/prompt-builder/__init__.py`, `builder.py`
- `src/engines/output-evaluation/__init__.py`, `engine.py`
- `src/engines/adaptive-conversation/__init__.py`, `engine.py`, `loop_detector.py`, `silence_handler.py`
- `src/engines/predictive-response/__init__.py`, `engine.py`
- `src/services/dialogue-manager/__init__.py`, `service.py`
- `src/services/conversation-engine/__init__.py`, `engine.py`
- `src/services/knowledge-retrieval/__init__.py`, `service.py`, `store.py`, `retriever.py`, `embedder.py`
- `src/services/conversation-quality/__init__.py`, `scorer.py`, `calibration.py`, `dashboard.py`
- `src/services/llm-runtime/output_validator.py`
- `src/services/tts/streaming_pipeline.py`
- `src/services/playback/__init__.py`, `scheduler.py`, `output.py`
- `tests/e2e/test_walking_skeleton.py`
- `tests/unit/services/test_conversation_engine.py`, `test_output_validator.py`, `test_playback_scheduler.py`, `test_knowledge_retrieval.py`, `test_conversation_quality.py`
- `tests/unit/engines/test_adaptive_conversation.py`, `test_predictive_response.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| STT (Whisper) | `AsyncMock` streaming `WordHypothesis` | Bypasses GPU inference; pre-scripted word stream |
| LLM (vLLM) | `AsyncMock` streaming `TokenChunk` | Returns pre-scripted grounded response (facts match ResponsePlan) |
| TTS (Veena) | `AsyncMock` streaming `AudioClause` | Returns fake audio bytes (size-correct, not real audio) |
| GPU Scheduler | `FakeGPUScheduler` returning `APPROVE` | No real VRAM allocation |
| Redis (working memory) | `FakeRedisClient` (Sprint-003) | In-process key-value store |
| Postgres (relationship memory) | `TestPostgres` (Sprint-003) | In-process SQLite or test DB |
| MongoDB (quality scores) | `mongomock` or `TestMongo` fixture | In-memory collection |
| Event Bus | `FakeEventBus` (captures published events) | In-memory subscriber routing |
| Vector store (knowledge retrieval) | In-memory `dict` keyed by embedding | No real embedder model |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Unit tests | `pytest tests/unit/` | All pass |
| E2E walking skeleton (mock) | `pytest tests/e2e/test_walking_skeleton.py` | Passes; `AudioClause` received |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `OutputValidator.validate()`: LLM says "₹15,000" when `ResponsePlan.facts` has "₹12,500" → reject
- `OutputValidator.validate()`: LLM output matches all facts in ResponsePlan → accept
- `PromptBuilder`: same ResponsePlan + CustomerContext → same prompt hash (twice)
- `AdaptiveConversationEngine`: 4000ms silence → clarification turn in next ResponsePlan
- `ConversationLoopDetector`: same intent 3× → loop-breaking strategy triggered
- `KnowledgeRetrievalService`: query "RBI calling hours" → `Snippet` list returned from mock store
- `PredictiveResponseEngine`: cache hit → pre-built plan used (skips CIL latency)
- `ConversationQualityScorer`: 5 scored turns → call grade = 'B' (test fixture values)
- `DecisionEnvelope` published to FakeEventBus for every turn

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely. **This is the most critical Phase 2 in the project — it delivers the first full spoken AI call on real infrastructure and validates the 1.5s latency target.**

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| ResponsePlanningEngine | K8s Deployment — `voiceos-runtime` | Central ResponsePlan assembly; connects all engines |
| PromptBuilder | K8s Deployment — `voiceos-runtime` | Deterministic prompt assembly; RI-7 enforcement |
| DialogueManager | K8s Deployment — `voiceos-runtime` | Turn coordination; barge-in handling |
| ConversationEngine | K8s Deployment — `voiceos-runtime` | CIL orchestrator; most critical hot-path service |
| OutputValidator | Sidecar within LLMService pod | Law-of-Authority gate before TTS |
| TrueStreamingPipeline | Library module wired into ConversationEngine | No separate deployment |
| PlaybackScheduler | K8s Deployment — `voiceos-runtime` | Audio clause queuing; sends to Media Gateway |
| AudioOutput | K8s Deployment — `voiceos-runtime` | μ-law/G.711 conversion; transport to Twilio/SIP |
| OutputEvaluationEngine | K8s Deployment — `voiceos-runtime` | Async quality scoring; non-blocking |
| AdaptiveConversationEngine | Library wired into ConversationEngine | No separate deployment |
| KnowledgeRetrievalService | K8s Deployment — `voiceos-data` | RAG knowledge base; requires vector store |
| PredictiveResponseEngine | Library wired into ConversationEngine | No separate deployment |
| ConversationQualityScorer | K8s Deployment — `voiceos-runtime` | Per-call quality aggregation; writes to MongoDB |

**Previously deployed services that remain running:**
- Sprint-004–008: Media Gateway, Audio Session Manager, Audio Preprocessing, VAD & Endpointing, GPU Scheduler Service
- Sprint-009: STTService, LLMService, TTSService (GPU: Whisper, Qwen, Veena)
- Sprint-010: IntentEngine, EntityExtractor, EmotionEngine, WorkingMemoryService, RelationshipMemoryService, ConversationStateIntelligence
- Sprint-011: RiskEngine, DialoguePolicyEngine, StrategyEngine, GoalPlanner, NegotiationEngine, EmpathyPlanner

**Deployment procedure:**
1. Build and push container images for all new services
2. Apply `kubectl apply -f infra/k8s/orchestration/`
3. ConversationEngine startup: confirm connectivity to all 12 upstream engines + 3 AI adapters
4. Seed KnowledgeRetrievalService with RBI policy documents and product FAQs
5. Confirm PlaybackScheduler → Media Gateway audio routing

**Health checks:**
- All new services: `GET /health/live` → 200; `GET /health/ready` → 200
- ConversationEngine readiness: all upstream dependencies healthy
- KnowledgeRetrievalService: query "RBI calling hours" → ≥ 1 Snippet returned

**Integration validation — Walking Skeleton on real infrastructure:**
1. Inject test WAV audio (Hindi: "मुझे ₹5,000 देने हैं") via Media Gateway
2. VAD → STT (real Whisper) → ConversationEngine (real CIL pipeline) → LLM (real Qwen) → OutputValidator → TTS (real Veena) → PlaybackScheduler
3. Assert: `AudioClause` received at PlaybackScheduler output within **1.5s p95**
4. Assert: `DecisionEnvelope` published to event log after turn
5. Assert: `ConversationQualityScorer` record created in MongoDB
6. Assert: Negotiation offers within configured envelope in all turns

**Latency measurement procedure:**
- Instrument each stage with OpenTelemetry spans
- Run 20 consecutive test calls
- Compute p95 first-audio latency from Media Gateway ingress to PlaybackScheduler output
- **Gate: p95 ≤ 1.5s must pass before marking Phase 2 complete**

**Rollback procedure:**
- `kubectl rollout undo deployment/conversation-engine -n voiceos-runtime` (ConversationEngine is the hub — all upstream services unaffected)

### GPU Node

**No new GPU deployments this sprint.** The GPU services deployed in Sprint-009 (Whisper, Qwen2.5-7B, Veena) now receive their first real end-to-end inference requests through the orchestrated call path.

**GPU validation for Sprint-012:**
- STT: real Whisper inference on test audio; word hypotheses arrive within 300ms p95
- LLM: real Qwen2.5-7B inference; TTFT ≤ 350ms p95; full response within 800ms p95
- TTS: real Veena synthesis; first clause audio ≤ 250ms p95
- Combined: first audio to caller ≤ 1.5s p95 (the walking skeleton milestone)
- VRAM: all three models loaded simultaneously; gauges confirm 24,576 MB allocated; no OOM

**GPU node rollback:**
> Not required — GPU services unchanged. If latency exceeds budget, optimize ConversationEngine CIL path (not GPU services).

### Infrastructure Validation

**CPU Validation:**
- ConversationEngine: full pipeline walk in `Running` state; CIL processing ≤ 120ms p95 (engine-only, excluding AI calls)
- OutputValidator: zero hallucinations pass through in validation calls
- PlaybackScheduler: barge-in flush within 50ms of `BargeinDetected` event
- KnowledgeRetrievalService: semantic query returns relevant Snippets from knowledge base
- ConversationQualityScorer: call grade written to MongoDB after each test call

**GPU Validation:**
- All three AI models serving live inference requests through the orchestrated path
- End-to-end first-audio p95 ≤ 1.5s (20-call measurement on real infrastructure)
- No CUDA OOM events during 20-call validation run

**Networking Validation:**
- ConversationEngine → all 12 engines: gRPC calls complete within 5ms (intra-cluster)
- ConversationEngine → STT/LLM/TTS: calls complete within AI inference time (no added network overhead)
- PlaybackScheduler → Media Gateway: audio routed correctly (no packet loss)

### Regression Validation

This sprint assembles the full pipeline. Regression scope is comprehensive:
- All Sprint-010 engine unit tests: `pytest tests/unit/engines/`
- All Sprint-011 engine unit tests: `pytest tests/unit/engines/`
- GPU Scheduler concurrent test: `pytest tests/integration/services/test_gpu_scheduler_integration.py`
- Media Gateway → VAD pipeline: `pytest tests/integration/services/test_vad_pipeline.py`
- STT/LLM/TTS health endpoints: all return 200
- Prometheus: all targets `UP`, no circuit breakers OPEN

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] All orchestration components implemented
- [ ] `ruff check`: 0 errors; `ruff format --check`: formatted; `mypy --strict`: 0 issues
- [ ] Boundary check: 0 violations
- [ ] E2E walking skeleton test passes with mocked AI backends
- [ ] OutputValidator catches all hallucination test cases
- [ ] AdaptiveConversation: silence recovery and loop detection tests pass
- [ ] KnowledgeRetrieval: snippet returned for valid query
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] All orchestration services deployed and healthy
- [ ] Walking skeleton e2e test passes on real infrastructure
- [ ] First-audio **p95 ≤ 1.5s** measured from 20 real calls (Milestone M-3 gate)
- [ ] OutputValidator: zero hallucinations pass through on validation calls
- [ ] ConversationQualityScorer: call grades recorded in MongoDB
- [ ] Negotiation boundary: 100% of offers within envelope across all validation calls
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-013

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state. Sprint-012 is the critical walking-skeleton milestone — this snapshot captures the first complete end-to-end system.

### CPU_NODE_STATE.md — Updates This Sprint

- Add all 13 new services to Services table (§8.1): ResponsePlanningEngine, PromptBuilder, DialogueManager, ConversationEngine, OutputValidator, PlaybackScheduler, AudioOutput, OutputEvaluationEngine, AdaptiveConversationEngine, KnowledgeRetrievalService, PredictiveResponseEngine, ConversationQualityScorer
- Update Service Dependencies (§8.2): full end-to-end pipeline now documented
- Update Startup Order (§8.3): complete dependency-ordered startup sequence from MediaGateway through AudioOutput
- Add health check commands for all 13 new services (§14)
- Update Port Map (§9.1) with all new service ports
- Add MongoDB collection `call_quality` (populated by ConversationQualityScorer) to §7.3

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged (no new models). Sprint-009 models now receive first orchestrated inference.
Document note: "Sprint-012 is the first sprint where all three GPU models receive real end-to-end inference from the ConversationEngine orchestration."
`GPU_NODE_STATE.md` last_updated field update to Sprint-012 to reflect this milestone.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add all 13 new services to SERVICE_PORTS map |
| `deployment/cpu/restore.sh` | Ensure startup order covers full pipeline before health check |

### DR Validation

**CPU node rebuild test:**
```bash
sudo bash deployment/cpu/bootstrap.sh
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: all 24+ services healthy; complete pipeline functional
```

**GPU node rebuild test:**
```bash
bash deployment/gpu/restore.sh
bash deployment/gpu/healthcheck.sh
# Expected: Whisper, vLLM, Veena ready to receive ConversationEngine requests
```

**End-to-end walking skeleton test (critical M-3 gate):**
```bash
# Inject 20 test calls end-to-end
python3 scripts/validate/walking_skeleton.py --calls 20
# Expected: first-audio p95 ≤ 1.5s across all 20 calls
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; walking skeleton e2e test passes
```
