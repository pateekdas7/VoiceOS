# Load Test Report

**Sprint:** Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy
**Deliverable:** `evaluation/load-testing/` (Sprint-028 §2 — Load Testing)
**Architecture Reference:** Volume 1 Ch23 (Latency Budget); Volume 7 Ch15 (Performance Validation)
**Tool:** Locust or k6

---

## Report Metadata

| Field | Value |
|---|---|
| Date | _(to be filled after Phase 2 execution)_ |
| Environment | Production infrastructure (warm GPU, real AI models, 500-concurrent-call target) |
| Tool used | TBD (Locust or k6) |
| Test script | `tests/load/locustfile.py` (or `tests/load/k6-script.js`) |
| Status | **PENDING PHASE 2 EXECUTION** |
| Executed by | TBD |
| Report author | TBD |

---

## Methodology

Per Sprint-028 §2 scenario:

1. Ramp to 500 concurrent calls over 10 minutes.
2. Hold at 500 concurrent calls for 30 minutes.
3. Ramp down over 5 minutes.
4. Assert throughout hold phase:
   - First-audio p95 ≤ 1.65s (10% degradation budget at load, relative to the 1.5s baseline gate)
   - GPU utilization ≤ 0.80 (fleet average; no node at 100%)
   - Error rate < 0.1%
   - No OOM events
   - No circuit breaker opens (sustained)

This report captures the template/shell for that execution. Load testing requires real GPU infrastructure and is a Phase 2-only activity; Phase 1 only produces and syntactically validates the load test scripts.

---

## Test Timeline

| Phase | Duration | Target Concurrency | Status |
|---|---|---|---|
| Ramp-up | 10 minutes | 0 → 500 concurrent calls | TBD |
| Hold | 30 minutes | 500 concurrent calls (steady state) | TBD |
| Ramp-down | 5 minutes | 500 → 0 concurrent calls | TBD |

---

## Results

### Assertions

| Assertion | Threshold | Observed | Pass/Fail |
|---|---|---|---|
| First-audio p95 (at 500 concurrent) | ≤ 1.65s (10% degradation budget) | TBD | TBD |
| GPU utilization (fleet average) | ≤ 0.80 | TBD | TBD |
| GPU utilization (any single node) | < 1.00 (no node at 100%) | TBD | TBD |
| Error rate | < 0.1% | TBD | TBD |
| OOM events | 0 | TBD | TBD |
| Circuit breaker opens (sustained) | 0 | TBD | TBD |

### Latency Distribution Under Load

| Percentile | First-Audio Latency (ms) |
|---|---|
| p50 | TBD |
| p95 | TBD |
| p99 | TBD |

### GPU Utilization Samples (logged every 60s during hold phase)

| Timestamp | Node | Utilization | VRAM Used |
|---|---|---|---|
| TBD | TBD | TBD | TBD |

_(to be filled after Phase 2 execution — full 60s-interval sample series appended as a linked artifact or additional table rows)_

### Errors / Circuit Breaker Events

_(to be filled after Phase 2 execution — none expected per exit criteria)_

---

## Acceptance Criteria

- [ ] Load test: p95 ≤ 1.65s at 500 concurrent calls
- [ ] GPU utilization ≤ 0.80 (fleet average; no node at 100%) at 500 concurrent calls
- [ ] Error rate < 0.1%
- [ ] No OOM events recorded in Kubernetes events during load test
- [ ] No sustained circuit breaker opens
- [ ] `BenchmarkSuite.run_benchmarks()` passes for all stages (all p95 values within budget) during/after load

---

## Sign-off

| Role | Name | Date | Signature/Approval |
|---|---|---|---|
| Test Executor | TBD | TBD | PENDING |
| Engineering Lead | TBD | TBD | PENDING |
| Production Readiness Owner | TBD | TBD | PENDING |

**Overall Status:** PENDING PHASE 2 EXECUTION — do not proceed to canary deploy until all assertions pass.
