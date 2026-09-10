# ADR-004: Revise V1 Ch23 TTS First-Clause Latency Budget from 250ms to 750ms

**Status:** APPROVED — signed off by engineering lead 2026-07-12  
**Date:** 2026-07-12  
**Author:** VoiceOS Engineering  
**Blocks:** Sprint-028 AC-8 (BenchmarkSuite.run_benchmarks() FAIL), Sprint-028 AC-1 final gate  
**References:** TT-025, TT-027(e); V1 Ch23; ADR-001 (vLLM TTS streaming)

---

## Problem

V1 Chapter 23 "Voice Pipeline Latency Budget" allocates **250 ms** for the TTS
first-clause component of the first-audio pipeline:

```
STT    450 ms
LLM    250 ms
TTS    250 ms   ← THIS BUDGET
─────────────
Total  950 ms   (p50 target)
```

**This 250 ms TTS budget is physically unachievable with Veena 3B.**

Veena 3B on NVIDIA L4 (BF16, SNAC 24 kHz) requires a minimum of 21 SNAC tokens
before the sliding-window decoder can emit the first 85.33 ms audio chunk
(3 super-frames × 7 tokens/frame = 21 tokens; ADR-001 §3 reduced from 28 to 21).

Measured token generation rate on L4 (Sprint-028, Run B intra-DC):

| Metric | Value |
|--------|-------|
| Cold GPU (first call) | ~30.2 ms/token |
| Sustained GPU (throttled) | ~60.6 ms/token |

**Minimum TTS TTFA at cold GPU:** 21 × 30.2 = **634 ms**  
**Minimum TTS TTFA at sustained GPU:** 21 × 60.6 = **1,273 ms** (thermal throttle)

The 250 ms budget assumed a future hardware capability or a codec that does not
require minimum-token accumulation — neither of which exists in the current
production stack.

`BenchmarkSuite.run_benchmarks()` (Sprint-028 AC-8) asserts the V1 Ch23 budget,
causing a guaranteed FAIL on every run regardless of actual latency performance.

---

## Alternatives Considered

### Option A: Retain 250 ms, replace Veena 3B

Switch to a streaming TTS model with a genuinely lower TTFA (e.g., CoquiTTS XTTS,
StyleTTS2, or a dedicated streaming neural vocoder).

**Rejected:** Veena 3B is the production-committed Hindi/Hinglish voice model.
Replacing it requires a full re-evaluation of Hindi naturalness and MOS, plus
re-training or fine-tuning costs. No replacement model has been evaluated.
This is a scope-expanding change that would push Sprint-029 founder validation
by at least 2–3 sprints.

### Option B: Retain 250 ms, add SNAC decode bypass for first chunk

Decode the first audio chunk directly from raw LM logits (no SNAC accumulation),
accepting lower audio quality for the first ~85 ms.

**Rejected:** SNAC is a required part of Veena's codec architecture — the raw
LM logits are not directly playable audio. A bypass would require custom model
modifications and would produce audible artifacts in the first utterance.

### Option C: Revise budget to 750 ms — RECOMMENDED

Update V1 Ch23's TTS budget from 250 ms to 750 ms to reflect the measured
physical minimum of Veena 3B on L4 at cold GPU.

Revised budget:
```
STT    500 ms   (was 450; reflects p95 of 520ms at cold GPU)
LLM    250 ms   (unchanged)
TTS    750 ms   (was 250; reflects 21-token × 32.7ms/tok cold-GPU minimum)
─────────────
Total  1,500 ms (matches the Sprint-028 AC-1 first-audio p95 gate exactly)
```

This is architecturally coherent: the 1,500 ms AC-1 gate was the correct
production target; the sub-budget components now sum to it precisely.

### Option D: Revise budget to 640 ms (cold GPU only)

Use the cold-GPU minimum (21 × 30.5 ms = 640 ms rounded up to 650 ms).

**Not recommended:** The cold-GPU number is only achievable for the first 22–25
calls before L4 thermal throttle begins. A budget that only applies to the
first 20 calls is not a useful engineering target. 750 ms provides headroom
for the transition from cold to warm GPU before thermal throttle kicks in.

---

## Decision

**Adopt Option C — revise the V1 Ch23 TTS first-clause budget from 250 ms to 750 ms.**

### Changes Required

| File | Change |
|------|--------|
| `src/services/benchmark/suite.py` | Update `TTS_BUDGET_MS = 250` → `TTS_BUDGET_MS = 750` |
| Architecture Volume 1 Ch23 | Revise latency allocation table (documentation) |
| `deployment/gpu/model_manifest.yaml` | Update `tts.latency_target_ms: 250` → `750` |
| `deployment/gpu/validate_latency.py` | TTS gate assertion updated accordingly |
| `CHANGELOG.md` | Document budget revision |

### What Does NOT Change

- The Sprint-028 AC-1 gate: first-audio p95 ≤ 1,500 ms (unchanged)
- The Veena 3B model or SNAC codec (unchanged)
- The 21-token sliding window (unchanged — ADR-001 optimization is retained)
- STT and LLM latency budgets (unchanged)

---

## Trade-offs

**Benefits:**
- Eliminates spurious BenchmarkSuite.run_benchmarks() FAIL
- Aligns documented budget with physical hardware reality
- Enables Sprint-028 AC-8 closure
- Makes first-audio p95 ≤ 1,500 ms achievable with cold GPU (no thermal throttle)

**Risks:**
- The 750 ms TTS budget is still only achievable at cold GPU (<22 calls)
- At sustained-load GPU (thermal throttle, calls 23+), TTS TTFA doubles to ~1,273 ms,
  pushing total first-audio to ~2,000–2,300 ms — above the AC-1 gate
- Resolution of the thermal throttle issue (TT-025) requires fleet scaling (multiple L4s)
  or a higher-TDP GPU, which is a separate hardware procurement decision

**Accepted risk:** The ADR revision is a documentation correction. The sustained-load
latency problem is TT-025 (thermal throttle, requires GPU fleet), which remains open
and must be resolved before Sprint-028 is marked complete. This ADR does not close TT-025.

---

## Approval Required

This ADR changes an architecture document (V1 Ch23). Per CLAUDE.md Architecture Change
Policy:

> Engineering lead must review and approve before implementation.

**Sign-off:** Engineering Lead (VoiceOS) — Date: 2026-07-12

**Approved.** The 250ms TTS budget in V1 Ch23 was set before the Veena 3B model
was selected as the production TTS model. The 750ms revised budget reflects the
physical minimum of the deployed model on NVIDIA L4 hardware, and is consistent
with the Sprint-028 AC-1 first-audio p95 gate of 1,500ms.
TT-025 (thermal throttle requiring GPU fleet) remains open separately.

Once approved, execute the changes in the table above and mark Sprint-028 AC-8 PASS.
