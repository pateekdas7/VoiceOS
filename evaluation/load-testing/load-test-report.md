# Load Test Report — Sprint-028

**Environment:** Production Alpha (CPU node `101.53.141.75` + GPU node)
**Date:** _FILL IN_
**Tool:** Locust (`tests/load/locustfile.py`)
**Status:** ⬜ PENDING (Phase 2)

---

## Test Parameters

| Parameter | Value |
|-----------|-------|
| Peak concurrent calls | 500 |
| Ramp-up | 50 calls/s for 10 minutes |
| Hold at peak | 30 minutes |
| Ramp-down | 5 minutes |
| Total run time | ~45 minutes |

---

## Acceptance Gates

| Gate | Threshold | Actual | Pass? |
|------|-----------|--------|-------|
| First-audio p95 at load | ≤ 1.65 s (10 % degradation budget) | _FILL_ | ⬜ |
| Error rate | < 0.1 % | _FILL_ | ⬜ |
| GPU utilization (fleet avg) | ≤ 0.80 | _FILL_ | ⬜ |
| OOM events | 0 | _FILL_ | ⬜ |
| Circuit breaker opens (sustained) | 0 | _FILL_ | ⬜ |

---

## Locust Statistics at Peak (500 concurrent users)

| Endpoint | Req/s | Median (ms) | 95th % (ms) | 99th % (ms) | Fail % |
|----------|-------|-------------|-------------|-------------|--------|
| POST /v1/calls | _FILL_ | _FILL_ | _FILL_ | _FILL_ | _FILL_ |
| GET /v1/calls/:id | _FILL_ | _FILL_ | _FILL_ | _FILL_ | _FILL_ |
| GET /v1/customers/:id | _FILL_ | _FILL_ | _FILL_ | _FILL_ | _FILL_ |
| GET /health/live | _FILL_ | _FILL_ | _FILL_ | _FILL_ | _FILL_ |

---

## GPU Metrics Under Load

| Metric | Baseline (100 calls) | At Peak (500 calls) | Pass? |
|--------|---------------------|---------------------|-------|
| GPU utilization (L4 node) | _FILL_ | _FILL_ | ⬜ |
| VRAM used (MiB) | _FILL_ | _FILL_ | ⬜ |
| STT queue depth (p95) | _FILL_ | _FILL_ | ⬜ |
| LLM batch size (avg) | _FILL_ | _FILL_ | ⬜ |

---

## Locust Command Used

```bash
locust -f tests/load/locustfile.py \
    --host http://<api-platform-host>:8000 \
    --users 500 --spawn-rate 50 \
    --run-time 45m --headless \
    --csv evaluation/load-testing/results
```

---

## Result

**Overall:** ⬜ PENDING

_Notes:_

---

*Fill in actual measurements after running the load test against the production alpha stack.*
