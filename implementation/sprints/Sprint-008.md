# Sprint-008 — GPU Scheduler

**Epic:** E2 — Core Voice Runtime  
**Status:** ✅ Complete (2026-06-30)  
**Depends on:** Sprint-001, Sprint-002, Sprint-003  
**Blocks:** Sprint-009  
**Milestone:** Core Runtime Complete (together with Sprint-007)  

---

## Objective

Implement the GPU Scheduler — the system-wide VRAM ledger with OOM-by-construction admission control, per-model GPU pools, warm-before-admit guarantee, and graceful failover. This is the central resource manager that all AI services (STT, LLM, TTS) must request from before loading models or running inference.

---

## Architecture References

- Volume 1: Ch7 (GPU Scheduler — VRAM ledger, admission, pools, warm-before-admit, graceful failover, priority)
- Volume 7: Ch6 (GPU Fleet Management — node pools, per-model pools, GPU-1 warm-before-admit, GPU-2 graceful failover)
- DocSuite-02: Interface Contracts (GPUScheduler API)

---

## Components to Implement

### `src/services/gpu-scheduler/`

```
src/services/gpu-scheduler/
├── __init__.py
├── service.py              (GPUSchedulerService: gRPC/REST API, health check, pool management)
├── scheduler.py            (GPUScheduler: main scheduler logic, admission decisions)
├── vram_ledger.py          (VRAMLedger: tracks allocated/available VRAM per GPU device)
├── model_pool.py           (ModelPool: manages instances of a specific model on a GPU)
├── admission.py            (AdmissionController: decides APPROVE/REJECT for VRAM requests)
├── priority_queue.py       (PriorityQueue: in-call requests > prefetch, FIFO within same priority)
├── failover.py             (FailoverManager: handles GPU device failure, drains to survivor)
└── metrics.py              (gpu_vram_used_mb, gpu_vram_available_mb, admission_rate, reject_rate)
```

**VRAMLedger:**
- Tracks per-GPU device: `total_vram_mb`, `allocated_vram_mb`, `available_vram_mb`
- `allocate(device_id, model_id, vram_mb) -> AllocationToken` — atomic, thread-safe
- `release(allocation_token)` — returns VRAM to available pool
- `available(device_id) -> int` — current available VRAM in MB
- State stored in Redis (hot) + Postgres (authoritative, for recovery)
- Invariant check: `assert_ri8_oom_by_construction(requested, available, service_name)` called on every allocate

**AdmissionController:**
- `decide(service: str, model: str, required_vram_mb: int) -> AdmissionDecision`
- `AdmissionDecision.APPROVE` — VRAM available; allocation proceeds
- `AdmissionDecision.REJECT` — insufficient VRAM; request refused (not queued-to-OOM per RI-8)
- Selection policy: prefer GPU with most available VRAM (best-fit)

**ModelPool:**
- Manages N instances of a specific model on specific GPU(s)
- Pool types: STT_POOL, LLM_POOL, TTS_POOL (configurable sizes)
- `acquire() -> ModelHandle` — blocks until a model instance is free (up to timeout_ms)
- `release(handle)` — returns model instance to pool
- Warm-before-admit (GPU-1): on scale-up, new instance warms (loads to GPU) before old is released

**FailoverManager:**
- Subscribes to GPU health events
- On GPU-N failure: `drain_to(surviving_gpus: list[str])` — gracefully migrates active sessions
- New session requests during failover are queued (up to 5s) then re-routed to surviving GPU
- Emits `GPUFailoverStarted`, `GPUFailoverCompleted` domain events

**Priority Queue:**
- CRITICAL: active in-call STT/TTS inference (lowest latency)
- HIGH: active in-call LLM inference
- NORMAL: background LLM prefill
- LOW: model preloading/warming

---

## Files Expected to Change

**New:** `src/services/gpu-scheduler/` (all files above)  
**New:** `tests/unit/services/test_gpu_scheduler.py`  
**New:** `tests/integration/services/test_gpu_scheduler_integration.py`

---

## Acceptance Criteria

- [ ] `VRAMLedger.allocate()` correctly deducts from available VRAM and returns an AllocationToken
- [ ] `AdmissionController.decide()` returns REJECT (not queue) when requested VRAM > available (RI-8 enforcement)
- [ ] `assert_ri8_oom_by_construction` is called on every `allocate()` call (verified by coverage + unit test)
- [ ] ModelPool correctly blocks `acquire()` when all instances are busy, unblocks when `release()` is called
- [ ] Warm-before-admit: scale-up test confirms new instance loads before old is unloaded
- [ ] FailoverManager correctly drains sessions from a failed GPU to surviving GPUs (unit test with fake GPU failure)
- [ ] `gpu_vram_available_mb` Prometheus gauge reflects actual available VRAM after allocations
- [ ] Priority queue: CRITICAL requests are served before NORMAL requests when queue is non-empty

---

## Required Tests

**Unit:**
- `test_vram_ledger_allocate_deducts` — allocate 4096MB from 8192MB available → 4096MB available
- `test_vram_ledger_reject_on_insufficient` — allocate 4097MB from 4096MB available → REJECT
- `test_ri8_called_on_allocate` — mock assert_ri8 and verify it is called on every allocate
- `test_model_pool_blocks_on_full` — all instances acquired → next acquire() blocks
- `test_model_pool_unblocks_on_release` — release() while another is waiting → waiting acquire() completes
- `test_warm_before_admit` — scale-up: new model loads before old model unloads (no VRAM gap)
- `test_priority_queue_critical_first` — CRITICAL + NORMAL requests queued → CRITICAL served first
- `test_failover_drains_to_surviving_gpu` — simulate GPU-0 failure → sessions migrate to GPU-1

**Integration:**
- `test_gpu_scheduler_concurrent_requests` — 10 concurrent allocate requests → all served correctly or rejected cleanly

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass
- [ ] RI-8 invariant called on every allocate (100% coverage)
- [ ] CI green
- [ ] **Milestone M-2 (Core Runtime Complete) criteria verified** (together with Sprint-007 media pipeline)
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-009
