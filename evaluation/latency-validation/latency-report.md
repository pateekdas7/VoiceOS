# Latency Validation Report

**Sprint:** Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy
**Deliverable:** `evaluation/latency-validation/` (Sprint-028 §1 — Latency Validation)
**Architecture Reference:** Volume 1 Ch23 (Latency Budget — per-stage targets); Volume 3 Ch19 (Performance Engineering)

---

## Report Metadata

| Field | Value |
|---|---|
| Date | _(to be filled after Phase 2 execution)_ |
| Environment | Production infrastructure (warm GPU, real AI models) |
| Test window | 30 minutes |
| Call volume | 100 test calls |
| Instrumentation | OpenTelemetry spans, per stage |
| Status | **PENDING PHASE 2 EXECUTION** |
| Executed by | TBD |
| Report author | TBD |

---

## Methodology

Per Sprint-028 §1 procedure:

1. Use production infrastructure (warm GPU, real AI models) — no synthetic/mocked stages.
2. Inject 100 test calls over 30 minutes.
3. Instrument every stage with OpenTelemetry spans.
4. Record per-stage p50/p95/p99 latency for the following pipeline stages, in order:
   Media GW → ASM → Preprocessing → VAD → STT → CIL (Conversation Intelligence Layer) → LLM (TTFT) → TTS (first clause) → Playback start.
5. Assert first-audio p95 ≤ 1.5s. If exceeded: FAIL the run, identify the bottleneck stage, optimize (model size, batching, caching), and re-run. Do NOT proceed to canary deploy until first-audio p95 ≤ 1.5s.
6. Compare observed per-stage p50 figures against the architecture's per-stage budget (Volume 1 Ch23):
   endpoint 120ms + STT 300ms + context+prompt 90ms + LLM TTFT 350ms + validate 40ms + TTS 250ms + resample 30ms = **~1060ms p50 headroom** (total budget across the accounted stages).

This report captures the template/shell for that execution. All data cells below are placeholders pending Phase 2 (real GPU infrastructure) execution — Phase 1 of Sprint-028 explicitly excludes latency validation, which "requires real GPU" and is Phase 2-only.

---

## Results

### Top-Line Gate

| Metric | Threshold | Observed | Pass/Fail |
|---|---|---|---|
| First-audio p95 | ≤ 1.5s (1500ms) | TBD | TBD |

### Per-Stage Latency (ms)

| Stage | p50 (ms) | p95 (ms) | p99 (ms) | Budget (ms) | Pass/Fail |
|---|---|---|---|---|---|
| Media GW | TBD | TBD | TBD | _(endpoint budget, see note)_ 120 | TBD |
| ASM | TBD | TBD | TBD | _(included in endpoint 120ms)_ | TBD |
| Preprocessing | TBD | TBD | TBD | _(included in endpoint 120ms)_ | TBD |
| VAD | TBD | TBD | TBD | _(included in endpoint 120ms)_ | TBD |
| STT | TBD | TBD | TBD | 300 | TBD |
| CIL (Conversation Intelligence Layer) | TBD | TBD | TBD | 90 (context+prompt) | TBD |
| LLM (TTFT) | TBD | TBD | TBD | 350 | TBD |
| TTS (first clause) | TBD | TBD | TBD | 250 | TBD |
| Playback start | TBD | TBD | TBD | 30 (resample) + 40 (validate) | TBD |

**Total p50 budget headroom (V1 Ch23):** endpoint 120ms + STT 300ms + context+prompt 90ms + LLM TTFT 350ms + validate 40ms + TTS 250ms + resample 30ms = **~1060ms**

> Note: Media GW / ASM / Preprocessing / VAD are sub-stages within the architecture's "endpoint" budget line (120ms) and "validate" budget line (40ms); STT, CIL, LLM, TTS, and resample map 1:1 to their respective budget lines above. Observed sub-stage breakdowns will be recorded individually in Phase 2 even though the architecture's budget is expressed at the aggregate level.

---

## Bottleneck Analysis

_(to be filled after Phase 2 execution)_

If first-audio p95 > 1.5s: identify the stage(s) exceeding budget, root cause, and remediation applied (model size / batching / caching), followed by re-run results.

---

## Acceptance Criteria

- [ ] First-audio p95 ≤ 1.5s on latency validation run (100 calls, production AI models)
- [ ] Per-stage p50/p95/p99 recorded for all stages: Media GW, ASM, Preprocessing, VAD, STT, CIL, LLM (TTFT), TTS (first clause), Playback start
- [ ] STT TTFW ≤ 500ms (GPU baseline validation)
- [ ] LLM TTFT ≤ 500ms (GPU baseline validation)
- [ ] TTS first-clause ≤ 300ms (GPU baseline validation)
- [ ] No stage exceeds its Volume 1 Ch23 budget at p50
- [ ] Re-run confirms fix if initial run failed the p95 ≤ 1.5s gate

---

## Sign-off

| Role | Name | Date | Signature/Approval |
|---|---|---|---|
| Test Executor | TBD | TBD | PENDING |
| Engineering Lead | TBD | TBD | PENDING |
| Production Readiness Owner | TBD | TBD | PENDING |

**Overall Status:** PENDING PHASE 2 EXECUTION — do not proceed to canary deploy until this report is completed with first-audio p95 ≤ 1.5s confirmed.
