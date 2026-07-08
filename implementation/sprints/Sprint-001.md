# Sprint-001 — Repository Scaffolding, Contracts & Invariants

**Epic:** E1 — Foundation & Engineering Infrastructure  
**Status:** ✅ Complete (2026-06-30)  
**Depends on:** None (first sprint)  
**Blocks:** Sprint-002, Sprint-003, Sprint-004, Sprint-008, Sprint-013, Sprint-017  

---

## Objective

Initialize the complete monorepo layout per Volume 6 Ch2. Create the full `src/libs/contracts` package with all core typed data models and the `src/libs/invariants` package with all RI-1 through RI-8 runtime guards. Stand up the CI skeleton.

This is the only sprint with no dependencies. It is the foundation everything else builds on.

---

## Architecture References

- Volume 6: Ch2 (Repository Structure), Ch3 (Coding Standards CS-1–CS-10), Ch4 (Architecture Rules AR-1–AR-20)
- Volume 1: Appendix A (Data Contracts), Appendix E (Runtime Invariants RI-1–RI-8)
- DocSuite-02: Interface Contracts
- DocSuite-03: Data Dictionary
- DocSuite-12: Engineering Templates

---

## Components to Implement

1. **Monorepo layout** — `pyproject.toml`, `ruff.toml`, `mypy.ini`, `.pre-commit-config.yaml`, package hierarchy (`src/services/`, `src/engines/`, `src/libs/`)
2. **`src/libs/contracts/`** — ResponsePlan (with `retrieval: list[Snippet] = []` field), DecisionEnvelope, EventEnvelope, TurnInput, CustomerContext, AudioFrame, DomainEvent base, all primitive types (Money, TenantId, CallId, etc.), plus all cross-service AI adapter contracts:
   - `EmpathyConfig(tone: Tone, pacing: Pacing, language_register: LanguageRegister, acknowledgment_phrase: str | None)` — produced by EmpathyPlanner (Sprint-011), consumed by AdaptiveProsodyEngine (Sprint-009); must be in contracts so Sprint-009 can import it without depending on Sprint-011
   - `VoiceConfig(pitch_shift: float, rate_scale: float, energy_scale: float, pause_ms_after_clause: int, language: str)` — output of AdaptiveProsodyEngine, input to TTSAdapter
   - `Snippet(source: str, content: str, relevance_score: float)` — element of `ResponsePlan.retrieval`; populated by KnowledgeRetrievalService (Sprint-012)
   - `WordHypothesis(word: str, confidence: float, start_ms: int, end_ms: int, is_final: bool)` — STT streaming output
   - `TokenChunk(text: str, token_id: int, finish_reason: str | None)` — LLM streaming output
   - `AudioClause(audio_data: bytes, sample_rate: int, text: str, clause_index: int, is_final: bool)` — TTS streaming output
   - `Tone`, `Pacing`, `LanguageRegister`, `Sentiment`, `StressLevel` enums (shared by CIL engines)
3. **`src/libs/invariants/`** — InvariantViolationError + assert_ri1 through assert_ri8
4. **CI skeleton** — `.github/workflows/ci.yml`: ruff → mypy --strict → pytest with coverage gates

---

## Files Expected to Change

**New:** `pyproject.toml`, `ruff.toml`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `src/libs/contracts/` (all files), `src/libs/invariants/` (all files), `tests/unit/contracts/` (all test files), `tests/unit/invariants/` (all test files), `tests/invariants/test_invariant_suite.py`

**Modified:** None (first sprint)

---

## Acceptance Criteria

- [ ] All contracts exported from `src/libs/contracts/__init__.py` pass `mypy --strict` with zero errors — including EmpathyConfig, VoiceConfig, Snippet, WordHypothesis, TokenChunk, AudioClause, and all shared enums
- [ ] All 8 invariant guards callable and raise `InvariantViolationError` on violation
- [ ] Every invariant has a passing test AND a failing test
- [ ] CI pipeline runs green: ruff → mypy → pytest (coverage ≥90% contracts, 100% invariants)
- [ ] Repository layout matches V6 Ch2 exactly
- [ ] ResponsePlan is immutable (frozen) and includes `retrieval: list[Snippet] = []` field
- [ ] No `Any` in contracts or invariants (except `payload: dict[str, Any]` in EventEnvelope, documented)

---

## Required Tests

- `tests/unit/contracts/test_response_plan.py` — creation, immutability, serialization
- `tests/unit/contracts/test_decision_envelope.py` — creation, GovernanceVerdict enum
- `tests/unit/contracts/test_customer_context.py` — creation, field access
- `tests/unit/contracts/test_event_envelope.py` — creation, UUID uniqueness, serialization
- `tests/unit/contracts/test_primitives.py` — Money arithmetic, ID validation
- `tests/unit/invariants/test_ri1_realtime_purity.py` through `test_ri8_oom_by_construction.py` — each with pass + fail case
- `tests/invariants/test_invariant_suite.py` — import all guards, verify callable

---

## Definition of Done

- [ ] All AC items checked
- [ ] All required tests pass
- [ ] Coverage gates met
- [ ] CI green end-to-end
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-002
