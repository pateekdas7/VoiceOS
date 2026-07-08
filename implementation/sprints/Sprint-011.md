# Sprint-011 — Intelligence Engines — Decision Layer

**Epic:** E3 — Conversation Intelligence  
**Status:** ✅ Complete (2026-07-03)  
**Depends on:** Sprint-010  
**Blocks:** Sprint-012  

---

## Objective

Implement the decision-making engines of the CIL: Risk Engine, Dialogue Policy Engine, Strategy Engine, Goal Planner, Negotiation Engine, and Empathy Planner. These engines receive perception signals from Sprint-010 and produce a structured decision set that the Response Planning Engine (Sprint-012) assembles into a ResponsePlan.

---

## Architecture References

- Volume 2: Ch4 (Risk Engine), Ch5 (Negotiation Engine — envelope, moves, non-bypassable clamp), Ch6 (Strategy Engine — action selection), Ch7 (Goal Planner), Ch8 (Dialogue Policy Engine — compliance guardrails), Ch14 (Empathy Planner)
- DocSuite-03: Data Dictionary (strategy actions, negotiation moves, risk flags)

---

## Components to Implement

### `src/engines/risk/`
- `engine.py` — RiskEngine: evaluates TurnInput + ConversationState → set of RiskFlags
- `flags.py` — RiskFlag enum: `ESCALATION_TRIGGER | HARDSHIP_INDICATOR | ABUSE_DETECTED | LEGAL_THREAT | DISPUTE_CLAIM | ELDERLY_VULNERABLE | CONSENT_RISK | REGULATORY_RISK | THIRD_PARTY_ON_CALL | RECORDING_OBJECTION`
- `result.py` — RiskAssessment(flags: list[RiskFlag], escalation_required: bool, human_handoff_required: bool)
- Rules are deterministic (not LLM-based); ABUSE_DETECTED triggers immediate human_handoff_required = True

### `src/engines/dialogue-policy/`
- `engine.py` — DialoguePolicyEngine: applies hard compliance constraints to what the agent may say
- `constraints.py` — PolicyConstraint types: `MUST_DISCLOSE_RECORDING | MUST_NOT_THREATEN | MUST_NOT_HARASS | MUST_VERIFY_IDENTITY_BEFORE_DISCLOSURE | MUST_RESPECT_DND | MUST_REFERENCE_DPD_CORRECTLY | MUST_NOT_MISREPRESENT_AMOUNT`
- Output: `list[PolicyConstraint]` that populates `ResponsePlan.policy_constraints` and `ResponsePlan.must_say` / `ResponsePlan.must_not_say`
- Constraints are hard rules from RBI/DPDP (Policy Engine Sprint-017 will add runtime policy lookup; this sprint uses local rule set)

### `src/engines/strategy/`
- `engine.py` — StrategyEngine: constrained utility-maximizing action selection
- `actions.py` — StrategyAction enum: `ASK | VERIFY | NEGOTIATE | REASSURE | ESCALATE | TRANSFER | CLOSE | CONFIRM`
- Action selection is a lookup table + scoring function over (intent, conversation_state, risk_flags, emotion) — NOT an LLM call
- Output: `StrategySelection(action: StrategyAction, confidence: float, rationale: str)`

### `src/engines/goal-planner/`
- `engine.py` — GoalPlanner: determines the primary goal for this turn
- `goals.py` — Goal types: `COLLECT_FULL_PAYMENT | COLLECT_PARTIAL_PAYMENT | SECURE_PTP | VERIFY_IDENTITY | HANDLE_DISPUTE | DE_ESCALATE | END_CALL | TRANSFER_AGENT`
- Goal is constrained by CustomerContext (outstanding, DPD) and campaign settings (from CustomerContext)
- One primary goal per turn

### `src/engines/negotiation/`
- `engine.py` — NegotiationEngine: computes NegotiationEnvelope and selects NegotiationMove
- `envelope.py` — NegotiationEnvelope(floor_amount: Money, ceiling_amount: Money, floor_date: date, ceiling_date: date, allowed_settlement_pct: float)
- `moves.py` — NegotiationMove enum: `OFFER | COUNTER | ACCEPT | HOLD | DECLINE | PROPOSE_PTP`
- **Critical invariant:** `envelope.floor_amount ≤ any_offer ≤ envelope.ceiling_amount` MUST ALWAYS HOLD. The clamp is non-bypassable — the engine raises `NegotiationBoundaryViolationError` if a proposed offer is outside the envelope before emitting it.
- Envelope is derived from CustomerContext (outstanding, campaign settings) — not from LLM
- `assert_ri5_law_of_authority` called when deriving envelope from CustomerContext

### `src/engines/empathy/`
- `engine.py` — EmpathyPlanner: adapts tone, pacing, and language based on EmotionSignal
- `result.py` — EmpathyConfig(tone: Tone, pacing: Pacing, language_register: LanguageRegister, acknowledgment_phrase: str | None)
- `labels.py` — Tone: FORMAL | EMPATHETIC | REASSURING | FIRM | NEUTRAL; Pacing: SLOW | NORMAL | FAST; LanguageRegister: HINDI | HINGLISH | ENGLISH
- Rules: HIGH stress → EMPATHETIC + SLOW + acknowledgment phrase required

---

## Files Expected to Change

**New:** `src/engines/risk/`, `src/engines/dialogue-policy/`, `src/engines/strategy/`, `src/engines/goal-planner/`, `src/engines/negotiation/`, `src/engines/empathy/`  
**New:** `tests/unit/engines/test_risk_engine.py`, `test_dialogue_policy.py`, `test_strategy_engine.py`, `test_goal_planner.py`, `test_negotiation_engine.py`, `test_empathy_planner.py`

---

## Acceptance Criteria

- [ ] RiskEngine sets `human_handoff_required = True` when ABUSE_DETECTED flag is raised
- [ ] DialoguePolicyEngine adds `MUST_NOT_THREATEN` constraint for all calls (mandatory)
- [ ] StrategyEngine is a lookup/scoring function — zero LLM calls (verified by coverage: no LLM import)
- [ ] NegotiationEngine NEVER produces an offer outside the configured floor/ceiling (invariant test)
- [ ] `NegotiationBoundaryViolationError` is raised if attempted offer is outside envelope
- [ ] EmpathyPlanner sets EMPATHETIC tone + SLOW pacing when StressLevel is HIGH
- [ ] GoalPlanner returns exactly one primary goal per turn
- [ ] All engines are deterministic given the same inputs (unit tests verify this)

---

## Required Tests

**Unit:**
- `test_risk_abuse_triggers_handoff` — ABUSE_DETECTED → human_handoff_required=True
- `test_risk_hardship_no_escalation` — HARDSHIP_INDICATOR only → escalation_required=False
- `test_strategy_payment_intent_ask_action` — intent=PAYMENT, state=DEBT_DISCUSSION → action=ASK
- `test_strategy_dispute_triggers_verify` — intent=DISPUTE → action=VERIFY
- `test_negotiation_boundary_clamp_floor` — offer below floor → NegotiationBoundaryViolationError
- `test_negotiation_boundary_clamp_ceiling` — offer above ceiling → NegotiationBoundaryViolationError
- `test_negotiation_valid_offer_within_envelope` — offer within floor/ceiling → passes
- `test_empathy_high_stress_slow_pacing` — StressLevel.HIGH → pacing=SLOW, tone=EMPATHETIC
- `test_goal_exactly_one_goal` — GoalPlanner always returns exactly 1 goal
- `test_strategy_is_deterministic` — same inputs twice → same StrategyAction

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass
- [ ] Negotiation boundary invariant test: 100% of generated offers are within envelope
- [ ] Zero LLM calls from any decision engine (verified by import analysis in CI)
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-012

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** All decision engines are deterministic, CPU-only, and stateless — no external dependencies.

### Files Created

- `src/engines/risk/__init__.py`, `engine.py`, `flags.py`, `result.py`
- `src/engines/dialogue-policy/__init__.py`, `engine.py`, `constraints.py`
- `src/engines/strategy/__init__.py`, `engine.py`, `actions.py`
- `src/engines/goal-planner/__init__.py`, `engine.py`, `goals.py`
- `src/engines/negotiation/__init__.py`, `engine.py`, `envelope.py`, `moves.py`
- `src/engines/empathy/__init__.py`, `engine.py`, `result.py`, `labels.py`
- `tests/unit/engines/test_risk_engine.py`
- `tests/unit/engines/test_dialogue_policy.py`
- `tests/unit/engines/test_strategy_engine.py`
- `tests/unit/engines/test_goal_planner.py`
- `tests/unit/engines/test_negotiation_engine.py`
- `tests/unit/engines/test_empathy_planner.py`

### Mock Backends Used

None required — all six decision engines are fully deterministic, in-process, and stateless. No external I/O.

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations; zero LLM imports in engine modules |
| Unit tests | `pytest tests/unit/engines/` | All pass |
| Invariant test | `pytest tests/unit/engines/test_negotiation_engine.py -k boundary` | 100% within envelope |
| Determinism test | `pytest tests/unit/engines/test_strategy_engine.py::test_strategy_is_deterministic` | Passes |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `RiskEngine`: `ABUSE_DETECTED` → `human_handoff_required=True`; `HARDSHIP_INDICATOR` alone → `escalation_required=False`
- `NegotiationEngine`: any offer below floor → `NegotiationBoundaryViolationError`; any offer above ceiling → `NegotiationBoundaryViolationError`
- `StrategyEngine`: same inputs twice → same `StrategyAction` (determinism)
- `EmpathyPlanner`: `StressLevel.HIGH` → `tone=EMPATHETIC, pacing=SLOW`
- `GoalPlanner`: exactly 1 goal per turn
- Zero LLM imports in `src/engines/` (verified by `check_boundaries.py`)

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| RiskEngine | K8s Deployment — `voiceos-runtime` namespace | Decision engine; CPU-only, stateless |
| DialoguePolicyEngine | K8s Deployment — `voiceos-runtime` namespace | Compliance constraint generator; CPU-only |
| StrategyEngine | K8s Deployment — `voiceos-runtime` namespace | Action selector; CPU-only, zero LLM calls |
| GoalPlanner | K8s Deployment — `voiceos-runtime` namespace | Goal determination; CPU-only |
| NegotiationEngine | K8s Deployment — `voiceos-runtime` namespace | Envelope enforcement; CPU-only with critical invariant |
| EmpathyPlanner | K8s Deployment — `voiceos-runtime` namespace | Tone/pacing adaptation; CPU-only |

**Previously deployed services that remain running:**
- Sprint-004–008: Media Gateway, Audio Session Manager, Audio Preprocessing, VAD & Endpointing, GPU Scheduler Service
- Sprint-009: STTService, LLMService, TTSService (GPU: Whisper, Qwen, Veena)
- Sprint-010: IntentEngine, EntityExtractor, EmotionEngine, WorkingMemoryService, RelationshipMemoryService, ConversationStateIntelligence

**Deployment procedure:**
1. Build and push container images for all 6 decision engines
2. Apply `kubectl apply -f infra/k8s/decision/`
3. Verify all pods reach `Running` in < 30s (no external readiness dependencies)

**Health checks:**
- All 6 services: `GET /health/live` → 200 (no external deps → immediate ready)
- NegotiationEngine: submit test envelope + offer within bounds → `OFFER` move returned
- NegotiationEngine: submit offer outside bounds → `NegotiationBoundaryViolationError` (400 response)

**Integration validation:**
- Risk + Strategy pipeline: `ABUSE_DETECTED` flag from RiskEngine → StrategyEngine selects `ESCALATE` action
- Empathy → Prosody: `StressLevel.HIGH` → `EmpathyConfig` produces `pacing=SLOW` → AdaptiveProsodyEngine (Sprint-009) accepts VoiceConfig
- Negotiation: all test offers submitted via API are within configured envelope (100% pass)

**Rollback procedure:**
- `kubectl rollout undo deployment/<engine-name> -n voiceos-runtime` per engine (all stateless, instant rollback)

### GPU Node

> **GPU node is not required during this sprint.** All decision engines are deterministic CPU processes with zero model inference. Previously deployed GPU services (Whisper, Qwen, Veena) remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- All 6 decision engines in `Running` state, 0 restarts
- No ERROR-level logs from any decision engine
- NegotiationEngine boundary invariant: run 1000 test offers via deployed API → 100% within envelope (zero violations)
- StrategyEngine: same input submitted 10 times → same action returned every time

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- Decision engines are CPU-resident with no inter-service calls this sprint (they receive inputs inline)
- Verify health endpoints reachable from ConversationEngine namespace (Sprint-012 preparation)

### Regression Validation

- All Sprint-010 perception services: `pytest tests/unit/engines/` — all pass
- GPU services: health endpoints still 200; Prometheus gauges unchanged
- Media Gateway → VAD pipeline: `pytest tests/integration/services/test_vad_pipeline.py`

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] All 6 decision engines implemented
- [ ] `ruff check`: 0 errors
- [ ] `ruff format --check`: all files formatted
- [ ] `mypy --strict`: 0 issues
- [ ] Zero LLM imports in engine modules (boundary check)
- [ ] All unit tests pass
- [ ] Negotiation boundary: 100% within envelope
- [ ] Strategy determinism test passes
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] All 6 decision engines deployed and healthy on CPU node
- [ ] NegotiationBoundaryViolationError raised correctly on deployed API
- [ ] 1000 offer test: 100% within envelope on deployed service
- [ ] GPU node services unchanged
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-012

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `StrategyEngine`, `NegotiationEngine`, `GoalPlanner`, `PolicyEngineCore`, `RiskEngine`, `EmotionIntelligenceEngine` to Services table (§8.1)
- Update Service Dependencies (§8.2): decision engines consume IntentEngine output; feed ConversationEngine (Sprint-012)
- Add health check commands for all 6 new services (§14)
- Update Port Map (§9.1) with new service ports
- Add boundary check note: `check_boundaries.py` confirms zero LLM imports in decision engines

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged this sprint. `GPU_NODE_STATE.md` last_updated remains: Sprint-009.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/healthcheck.sh` | Add all 6 decision engine services to SERVICE_PORTS map |

### DR Validation

**CPU node rebuild test:**
```bash
sudo bash deployment/cpu/bootstrap.sh
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: all 11+ services healthy (Sprint-008 runtime + Sprint-009 AI + Sprint-010 perception + Sprint-011 decision)
```

**GPU node rebuild test:**
```bash
bash deployment/gpu/healthcheck.sh
# Expected: Whisper, vLLM, Veena still healthy; no changes
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; NegotiationEngine 1000-offer test 100% within envelope
```
