# Load Test Report — Sprint-028 Phase 2

**Sprint:** Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy
**Deliverable:** `evaluation/load-testing/` (Sprint-028 §2 — Load Testing)
**Architecture Reference:** Volume 1 Ch23 (Latency Budget); Volume 7 Ch15 (Performance Validation)
**Executed:** 2026-07-11

---

## Report Metadata

| Field | Value |
|---|---|
| Date | 2026-07-11 |
| Environment | CPU node (101.53.137.131) → GPU node (217.18.55.78) — intra-DC path |
| GPU hardware | NVIDIA L4 24GB (single node — production requires fleet per V7 Ch6) |
| Test script | `scripts/validate/load_test_concurrent.py` (threading-based; locust unavailable on Android/restricted nodes) |
| Concurrency tested | **10 concurrent users** (spec: 500) |
| Duration | 179 seconds (2× drain wait per thread stop) |
| Status | **PARTIAL — 10-user test executed; 500-user test not possible on single L4** |

---

## Infrastructure Gap Note

Sprint-028 §2 specifies ramp to **500 concurrent calls** over 10 minutes. This requires a GPU fleet (V7 Ch6). The single L4 GPU at 217.18.55.78 cannot sustain 500 concurrent Veena 3B synthesis threads — the model is single-threaded per synthesis request.

**10-concurrent test was run** to characterize single-GPU behavior under concurrent load and document the bottleneck pattern. 500-concurrent test remains BLOCKED until GPU fleet is provisioned.

---

## Single-GPU Concurrency Analysis

The TTS server (`deployment/gpu/services/tts/server.py`) processes Veena 3B synthesis requests serially — one at a time. With N concurrent users each submitting a synthesis request:
- User 1 waits ~700ms (TTFA baseline)
- User 2 waits for User 1 to complete full synthesis (~1.2s) + own TTFA (~700ms) ≈ ~1.9s
- User N waits for N-1 completions: N × ~1.2s per synthesis

For N=10: expected wait for last user = 9 × 1.2s + 0.7s ≈ 11.5s → matches observed p50=12,589ms.

---

## 10-User Concurrent Load Test Results

**Test configuration:**
```
--users 10 --duration 120 --gpu-host 217.18.55.78
```

**Results:**

| Metric | Value |
|---|---|
| Total calls completed | 20 |
| Total duration | 179s |
| Throughput | 0.112 calls/sec |
| Error count | 0 |
| Error rate | 0.00% |

| Stage | p50 (ms) | p95 (ms) |
|---|---|---|
| STT (Whisper) | 323 | 3,659 |
| LLM TTFT (Qwen2.5-7B) | 80 | 229 |
| TTS TTFA (Veena 3B) | 12,589 | 13,345 |
| **FIRST-AUDIO (gate)** | **13,539** | **16,524** |

**Gate (first-audio p95 ≤ 1,650ms): FAIL — measured 16,524ms (10.0× over gate)**

**Gate (error rate < 0.1%): PASS — 0.00%**

---

## Root Cause Analysis

### TTS serialization bottleneck

TTS TTFA p50 = 12,589ms at 10 concurrent users. Cause: Veena 3B synthesis is single-threaded on the GPU. 10 concurrent requests serialize behind a single synthesis queue. Each synthesis takes ~1.2s (baseline drain) × 10 users = ~12s average wait for the last queued user.

This is not a bug — it is a correct consequence of running a single-GPU node that cannot parallelize 10 independent Veena synthesis sessions simultaneously.

### STT p95 = 3,659ms

High p95 (vs p50=323ms): STT spikes under concurrent load as Whisper batch inference queue fills when multiple concurrent clients submit simultaneously. HTTP connection reuse within the CPU→GPU path reduces reconnect overhead compared to the Termux→GPU path, but concurrent submissions still create inference queuing.

### LLM remains fast

vLLM batches concurrent requests effectively: p50=80ms, p95=229ms under 10 concurrent users. The LLM is not the bottleneck.

---

## 500-Concurrent Load Test Status

| Requirement | Status |
|---|---|
| 500-concurrent test script ready | **READY** (`tests/load/locustfile.py` is complete and fixed) |
| Locust available on test nodes | **BLOCKED** — psutil build fails on Android (Termux), pypi unreachable from CPU node |
| GPU fleet provisioned (V7 Ch6) | **BLOCKED** — single L4 only |
| GPU scheduler active (RI-8) | **BLOCKED** — TT-015 |
| 500-concurrent test executed | **NO** |

**Resolution path:** Provision second GPU node → install locust on a separate test runner → run 45-minute load test with `--users 500 --spawn-rate 0.83 --run-time 45m` per `tests/load/locustfile.py` docstring.

---

## Acceptance Criteria Status

| AC | Requirement | Result | Status |
|---|---|---|---|
| AC-1 | First-audio p95 ≤ 1.65s at concurrent load | 16,524ms at 10 users | **FAIL** |
| AC-2 | GPU utilization ≤ 0.80 fleet average | Single node at thermal TDP limit (71.08W/72W) | **FAIL (single node throttled)** |
| AC-3 | Error rate < 0.1% | 0.00% at 10 users | **PASS** |
| AC-4 | No OOM events | None observed | **PASS** |
| AC-5 | 500-concurrent test executed | NOT EXECUTED | **FAIL (BLOCKED)** |

---

## Sign-off

| Role | Status | Notes |
|---|---|---|
| Test Executor | **COMPLETE (partial)** | CPU node, 2026-07-11 |
| 10-user concurrent test | **FAIL on latency gate** | TTS serialization bottleneck |
| 500-user test | **NOT EXECUTED** | Single L4 + locust unavailable |
| Production Readiness | **NOT READY** | GPU fleet required before load test can pass |
