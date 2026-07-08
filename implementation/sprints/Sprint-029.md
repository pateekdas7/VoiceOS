# Sprint-029 — Founder Validation

**Epic:** E8 — Founder Validation → Pilot → Production Release  
**Status:** ⬜ Pending  
**Depends on:** Sprint-028  
**Blocks:** Sprint-030  
**Milestone:** Founder Validation — M-8  

---

## Objective

Structured expert review of real AI conversations for collections appropriateness, tone, compliance, factual grounding (Law of Authority), and negotiation quality. This sprint is a formal quality gate — it must produce a signed sign-off document before Sprint-030 (Pilot) can begin.

---

## Architecture References

- Volume 2: All chapters (quality review of conversation intelligence)
- DocSuite-07: Voice Style Guide (tone, pronunciation, prosody criteria)
- DocSuite-10: AI Evaluation Handbook (evaluation rubric, scoring methodology, golden sets, benchmark procedure)

---

## Evaluation Procedure

### 1. Call Execution (automated)
- Run 50+ calls against test accounts in the production alpha environment
- Use production AI models, production prompts, real CustomerContext (from test loan data)
- Scenarios to cover: payment intent, PTP negotiation, dispute handling, hardship case, callback scheduling, identity verification
- All calls recorded and transcribed

### 2. Structured Review (human + automated)

**Reviewers:** Founding team members + at least 1 collections domain expert

**Rubric (per DocSuite-10):**

| Dimension | Measurement | Pass Threshold |
|---|---|---|
| Law of Authority | % of calls with zero invented facts (automated: LawOfAuthorityChecker replay on transcripts) | 100% (zero violations) |
| RBI Compliance | % calls following all RBI fair-practice rules | 100% |
| Negotiation Envelope | % offers within configured floor/ceiling | 100% |
| Intent Recognition | % turns with correct intent classification (human-labeled) | ≥ 90% |
| Tone & Empathy | Score 1–5 per call (human reviewer rubric) | Average ≥ 3.5/5 |
| Language Naturalness | Hindi/Hinglish/English fluency score (human) | Average ≥ 3.5/5 |
| Audio Quality (MOS) | Mean Opinion Score (crowdsourced or expert) | ≥ 3.5/5 |
| First-Audio Latency | p95 first audio ≤ 1.5s (from production traces) | ≤ 1.5s |
| Call Completion | % calls reaching natural conclusion (not aborted) | ≥ 90% |

### 3. Remediation Loop

If ANY dimension fails:
1. Identify root cause (prompt issue, model issue, engine bug, architecture issue)
2. Fix → re-evaluate the failed dimension (subset of calls)
3. Re-sign-off on that dimension

Sprint is NOT complete until ALL dimensions pass simultaneously.

### 4. Sign-Off Document

**`evaluation/founder-validation-report.md`** must include:
- Date of evaluation
- Evaluators (named)
- Number of calls reviewed
- Score per dimension
- Pass/fail verdict per dimension
- Overall verdict (PASS / FAIL)
- Any noted limitations or known issues
- Named sign-off from founder

---

## Files Expected to Change

**New:** `evaluation/founder-validation-report.md`  
**New:** `tests/ai_eval/founder_validation_suite.py` (automated Law-of-Authority + intent replay)  
**New:** `evaluation/call-samples/` (anonymized call transcripts from validation run — no real PII)

---

## Acceptance Criteria

- [ ] ≥ 50 calls executed in production alpha and reviewed
- [ ] Law of Authority: 100% of calls reviewed — zero invented facts confirmed
- [ ] RBI Compliance: 100% of calls pass all RBI rule checks
- [ ] Negotiation: 100% of negotiation turns within configured floor/ceiling
- [ ] Intent Recognition: ≥ 90% on human-labeled turn subset
- [ ] Tone & Empathy: average ≥ 3.5/5
- [ ] Audio Quality (MOS): ≥ 3.5/5
- [ ] First-audio p95: ≤ 1.5s (confirmed from production traces)
- [ ] `evaluation/founder-validation-report.md` exists with named sign-off
- [ ] Any identified issues remediated and re-evaluated before sign-off

---

## Required Tests

**Automated AI Eval:**
- `tests/ai_eval/founder_validation_suite.py`:
  - Replay all transcripts through LawOfAuthorityChecker (zero violations)
  - Replay all transcripts through IntentEngine (compute accuracy vs. human labels)
  - Run RBI compliance rule checks on all transcripts
  - Check all negotiation moves against recorded NegotiationEnvelope

---

## Definition of Done

- [ ] All 9 rubric dimensions: PASS
- [ ] Founder sign-off document committed
- [ ] Automated evaluation suite runs clean (zero Law-of-Authority violations, ≥ 90% intent accuracy)
- [ ] **Milestone M-8 (Founder Validation) criteria verified**
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-030

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** The automated evaluation suite (LawOfAuthorityChecker replay, IntentEngine replay, RBI rule checks) is implemented and unit-tested against synthetic transcripts that include known violations and known-good patterns.

### Files Created

- `tests/ai_eval/founder_validation_suite.py` — automated replay suite
- `evaluation/founder-validation-report.md` (template, not yet filled)
- `evaluation/call-samples/` (synthetic transcript fixtures for unit testing the eval suite)

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| LawOfAuthorityChecker | Real implementation | Replays on synthetic transcripts with known invented facts |
| IntentEngine | Real implementation (CPU ONNX) | Replays on synthetic transcripts with human labels |
| RBI rules | `FakePolicyEngine` | Verifies compliance rule checks fire on synthetic outside-hours transcripts |
| NegotiationEnvelope | Real implementation | Checks synthetic negotiation transcripts against envelope |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check tests/ai_eval/` | 0 errors |
| Eval suite (synthetic) | `pytest tests/ai_eval/founder_validation_suite.py --transcripts=tests/fixtures/synthetic/` | Known violations detected; known-good transcripts pass |
| LawOfAuthority replay | Subset: inject known invented fact | Violation detected |
| RBI compliance check | Synthetic outside-hours transcript | `DENY` rule fires |
| Negotiation envelope check | Synthetic offer outside floor | Violation detected |

### Expected Outputs

- Automated eval suite correctly identifies the 3 planted violations in synthetic transcripts
- All known-good synthetic transcripts pass all 5 automated checks
- Evaluation report template is complete with rubric and scoring methodology documented

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely. All call execution uses the production alpha environment (CPU node + GPU node + real AI models).

### CPU Node

**Services active this sprint (already deployed):**
- All Sprint-004–028 services remain running unchanged

**Deployment procedure:**
1. Execute ≥ 50 calls in production alpha using test loan accounts
2. Record all calls; export transcripts and traces
3. Run automated eval suite on production transcripts: `python tests/ai_eval/founder_validation_suite.py --transcripts=evaluation/call-samples/`
4. Founders + collections domain expert review human-scored dimensions (Tone, Language, Audio)
5. For each failed dimension: identify root cause → fix → re-run subset → re-score
6. Commit `evaluation/founder-validation-report.md` with named sign-off

**Health checks:**
- Production alpha fully operational (Sprint-028 baseline maintained)
- All 50 calls complete without infrastructure errors
- First-audio p95 ≤ 1.5s confirmed from production traces during call execution

### GPU Node

**GPU active this sprint:**

All Sprint-009 GPU services remain running for validation calls:

| Model | VRAM | Validation Use |
|---|---|---|
| Whisper Large-v3 | 6,144 MB | STT transcription for all 50 validation calls |
| Qwen2.5-7B via vLLM | 16,384 MB | LLM response generation for all validation calls |
| Veena TTS | 2,048 MB | TTS synthesis; MOS scoring requires real Veena audio output |

GPU-specific validation: MOS score ≥ 3.5/5 requires Veena TTS to be active; audio quality is a pass/fail gate.

### Infrastructure Validation

**CPU Validation:**
- Automated eval suite run on production transcripts: 0 Law-of-Authority violations
- IntentEngine replay: ≥ 90% accuracy on human-labeled turn subset
- RBI compliance check: 100% of production transcripts pass

**GPU Validation:**
- First-audio p95 ≤ 1.5s: confirmed from production OTel traces for all 50 calls
- MOS scoring: expert or crowdsourced audio quality evaluation on Veena output → ≥ 3.5/5

**Networking Validation:**
> No new networking changes this sprint; existing infrastructure validated through call execution.

### Regression Validation

- All existing regression suites pass (Sprint-028 baseline maintained)
- No production incidents during 50-call validation window
- Remediation loop: any fixes applied pass the failing dimension on re-evaluation before sign-off

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] Automated evaluation suite (LawOfAuthority, IntentEngine, RBI, NegotiationEnvelope replay) implemented
- [ ] Evaluation suite correctly detects planted violations in synthetic transcripts
- [ ] Evaluation report template complete with rubric and scoring methodology
- [ ] `ruff check`: passes on eval suite code
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] ≥ 50 calls executed in production alpha environment
- [ ] Automated eval suite passes on all production transcripts (0 LoA violations, ≥ 90% intent accuracy)
- [ ] All 9 rubric dimensions scored: Tone ≥ 3.5, Language ≥ 3.5, MOS ≥ 3.5 (human/expert review)
- [ ] First-audio p95 ≤ 1.5s confirmed from production traces
- [ ] `evaluation/founder-validation-report.md` committed with named founder sign-off
- [ ] Milestone M-8 (Founder Validation) verified
- [ ] Deployment remains active as baseline for Sprint-030

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state. Sprint-029 is Founder Validation — GPU services carry production traffic for real borrower calls.

### CPU_NODE_STATE.md — Updates This Sprint

- Update §8.1: mark all services as `production-alpha` validated (Founder Validation completed)
- Add `evaluation/` directory reference to §10 Volumes: evaluation reports committed to Git
- Update §14 Verification Commands: add `AutomatedEvalSuite.run_production_transcripts()` validation command
- Note: first-audio p95 ≤ 1.5s confirmed from production traces in Jaeger — add to §14

### GPU_NODE_STATE.md — Updates This Sprint

- Update §8 Deployed AI Models: update `veena-fp16` entry with confirmed real-call MOS ≥ 3.5 result
- Update §15 Latency Validation Commands: add MOS scoring validation command used in founder validation
- Update §16 Latency Targets: mark p95 ≤ 1.5s as production-validated (not just load-test result)
- Update `GPU_NODE_STATE.md` last_updated: Sprint-029

### Scripts to Update

| File | Change |
|---|---|
| `deployment/gpu/model_manifest.yaml` | Update `post_restore_validation.commands`: add MOS scoring check command |
| `deployment/gpu/healthcheck.sh` | Add: check Veena TTS endpoint can generate audio (not just `/health/ready`) |

### DR Validation

**GPU node rebuild with real-call validation:**
```bash
# Full GPU node rebuild
sudo bash deployment/gpu/bootstrap.sh
bash deployment/gpu/restore.sh
# Expected: all three models loaded; VRAM ≤ 24,576MB; latency targets met

# Validate Veena TTS can generate audio (MOS-ready output)
curl -sf -X POST http://localhost:8200/v1/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Namaste, main aapko yaad dilana chahta hun", "voice": "veena"}' \
  --output /tmp/test_audio.wav
python3 scripts/validate/mos_check.py /tmp/test_audio.wav
# Expected: audio generated; MOS scoring pipeline accepts output
```

**End-to-end validation after rebuild:**
```bash
python3 scripts/validate/walking_skeleton.py --calls 5
# Expected: first-audio p95 ≤ 1.5s; 0 LoA violations on test script
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
pytest tests/evaluation/ -v
# Expected: all tests pass; eval suite confirms 0 LoA violations
```
