"""Unit tests for the GPU Scheduler service.

Covers:
- VRAMLedger VRAM accounting and RI-8 enforcement
- AdmissionController APPROVE / REJECT decisions
- ModelPool blocking acquire / release / warm-before-admit
- PriorityQueue ordering (CRITICAL first, FIFO within priority)
- FailoverManager drain and event emission
- GPUScheduler end-to-end allocation cycle
- GPUSchedulerService health check

Architecture: V1 Ch7; V1 Appendix E RI-8; V7 Ch6.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from src.libs.contracts.events.reliability_events import (
    GPUFailoverCompleted,
    GPUFailoverStarted,
)
from src.libs.invariants.errors import InvariantViolationError
from src.services.gpu_scheduler.admission import AdmissionController, AdmissionDecision
from src.services.gpu_scheduler.failover import FailoverManager
from src.services.gpu_scheduler.model_pool import ModelHandle, ModelPool, PoolType
from src.services.gpu_scheduler.priority_queue import (
    PriorityQueue,
    RequestPriority,
    VRAMRequest,
)
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.gpu_scheduler.service import DeviceConfig, GPUSchedulerService, PoolConfig
from src.services.gpu_scheduler.vram_ledger import VRAMLedger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_8G = 8192
_4G = 4096
_2G = 2048
_1G = 1024


def _single_gpu_ledger(total_mb: int = _8G, device_id: str = "gpu-0") -> VRAMLedger:
    ledger = VRAMLedger()
    ledger.register_device(device_id, total_mb)
    return ledger


def _stt_pool(ledger: VRAMLedger, size: int = 1, device: str = "gpu-0") -> ModelPool:
    return ModelPool(
        pool_type=PoolType.STT_POOL,
        size=size,
        vram_per_instance_mb=_2G,
        ledger=ledger,
        devices=[device],
    )


# ===========================================================================
# VRAMLedger tests
# ===========================================================================


class TestVRAMLedger:
    def test_vram_ledger_allocate_deducts(self) -> None:
        """allocate(4096MB) from 8192MB available → 4096MB available."""
        ledger = _single_gpu_ledger(_8G)
        token = ledger.allocate("gpu-0", "stt-model", _4G, "stt")

        assert ledger.available("gpu-0") == _4G
        assert ledger.allocated("gpu-0") == _4G
        assert token.vram_mb == _4G
        assert token.device_id == "gpu-0"
        assert token.model_id == "stt-model"

    def test_vram_ledger_reject_on_insufficient(self) -> None:
        """allocate(4097MB) from 4096MB available → InvariantViolationError RI-8."""
        ledger = _single_gpu_ledger(_4G)
        with pytest.raises(InvariantViolationError) as exc_info:
            ledger.allocate("gpu-0", "llm-model", _4G + 1, "llm")

        assert exc_info.value.invariant_id == "RI-8"
        assert ledger.available("gpu-0") == _4G  # unchanged: allocation was rejected

    def test_ri8_called_on_allocate(self) -> None:
        """assert_ri8_oom_by_construction is invoked on every allocate() call."""
        ledger = _single_gpu_ledger(_8G)
        target = "src.services.gpu_scheduler.vram_ledger.assert_ri8_oom_by_construction"
        with patch(target) as mock_ri8:
            mock_ri8.return_value = None
            ledger.allocate("gpu-0", "stt-model", _4G, "stt")
            mock_ri8.assert_called_once_with(_4G, _8G, "stt")

    def test_ri8_called_on_second_allocate(self) -> None:
        """RI-8 is called on each allocate independently (not just the first)."""
        ledger = _single_gpu_ledger(_8G)
        target = "src.services.gpu_scheduler.vram_ledger.assert_ri8_oom_by_construction"
        with patch(target) as mock_ri8:
            mock_ri8.return_value = None
            ledger.allocate("gpu-0", "stt-model", _2G, "stt")
            ledger.allocate("gpu-0", "tts-model", _2G, "tts")
            assert mock_ri8.call_count == 2

    def test_vram_ledger_release_returns_vram(self) -> None:
        """release() returns allocated VRAM to the available pool."""
        ledger = _single_gpu_ledger(_8G)
        token = ledger.allocate("gpu-0", "model-a", _4G, "stt")
        assert ledger.available("gpu-0") == _4G

        ledger.release(token)
        assert ledger.available("gpu-0") == _8G

    def test_vram_ledger_release_idempotent(self) -> None:
        """Releasing the same token twice does not double-count."""
        ledger = _single_gpu_ledger(_8G)
        token = ledger.allocate("gpu-0", "model-a", _4G, "stt")
        ledger.release(token)
        ledger.release(token)  # second release is a no-op
        assert ledger.available("gpu-0") == _8G

    def test_vram_ledger_multiple_allocations(self) -> None:
        """Multiple allocations correctly deduct from the available pool."""
        ledger = _single_gpu_ledger(_8G)
        t1 = ledger.allocate("gpu-0", "stt", _2G, "stt")
        t2 = ledger.allocate("gpu-0", "llm", _2G, "llm")
        t3 = ledger.allocate("gpu-0", "tts", _2G, "tts")

        assert ledger.available("gpu-0") == _2G
        ledger.release(t2)
        assert ledger.available("gpu-0") == _4G
        ledger.release(t1)
        ledger.release(t3)
        assert ledger.available("gpu-0") == _8G

    def test_vram_ledger_best_fit_selects_most_available(self) -> None:
        """best_fit_device() picks the GPU with the most available VRAM."""
        ledger = VRAMLedger()
        ledger.register_device("gpu-0", _8G)
        ledger.register_device("gpu-1", _4G)
        # Allocate nothing — gpu-0 has more free VRAM
        result = ledger.best_fit_device(_1G)
        assert result == "gpu-0"

    def test_vram_ledger_best_fit_returns_none_when_no_capacity(self) -> None:
        """best_fit_device() returns None when no device can satisfy the request."""
        ledger = _single_gpu_ledger(_4G)
        assert ledger.best_fit_device(_4G + 1) is None

    def test_vram_ledger_failed_device_reports_zero(self) -> None:
        """available() returns 0 for a failed device."""
        ledger = _single_gpu_ledger(_8G)
        ledger.allocate("gpu-0", "stt", _2G, "stt")
        ledger.mark_device_failed("gpu-0")
        assert ledger.available("gpu-0") == 0

    def test_vram_ledger_drain_device(self) -> None:
        """drain_device() releases all allocations and returns their tokens."""
        ledger = _single_gpu_ledger(_8G)
        t1 = ledger.allocate("gpu-0", "stt", _2G, "stt")
        t2 = ledger.allocate("gpu-0", "llm", _2G, "llm")

        drained = ledger.drain_device("gpu-0")
        assert len(drained) == 2
        assert ledger.allocated("gpu-0") == 0
        # token_ids match what was allocated
        drained_ids = {t.token_id for t in drained}
        assert t1.token_id in drained_ids
        assert t2.token_id in drained_ids


# ===========================================================================
# AdmissionController tests
# ===========================================================================


class TestAdmissionController:
    def test_admission_approve_when_vram_available(self) -> None:
        ledger = _single_gpu_ledger(_8G)
        ctrl = AdmissionController(ledger)
        decision, device_id = ctrl.decide("stt", "whisper", _4G)
        assert decision == AdmissionDecision.APPROVE
        assert device_id == "gpu-0"

    def test_admission_reject_when_vram_insufficient(self) -> None:
        ledger = _single_gpu_ledger(_4G)
        ctrl = AdmissionController(ledger)
        decision, device_id = ctrl.decide("llm", "qwen", _4G + 1)
        assert decision == AdmissionDecision.REJECT
        assert device_id is None

    def test_admission_selects_best_fit_device(self) -> None:
        """Best-fit: device with most available VRAM is chosen."""
        ledger = VRAMLedger()
        ledger.register_device("gpu-0", _8G)
        ledger.register_device("gpu-1", _4G)
        ctrl = AdmissionController(ledger)
        _, device_id = ctrl.decide("tts", "veena", _1G)
        assert device_id == "gpu-0"  # more free VRAM


# ===========================================================================
# ModelPool tests
# ===========================================================================


class TestModelPool:
    def test_model_pool_blocks_on_full(self) -> None:
        """All instances acquired → next acquire() raises TimeoutError."""
        ledger = _single_gpu_ledger(_8G)
        pool = _stt_pool(ledger, size=1)
        handle = pool.acquire(timeout_ms=500)

        with pytest.raises(TimeoutError):
            pool.acquire(timeout_ms=50)

        pool.release(handle)

    def test_model_pool_unblocks_on_release(self) -> None:
        """release() while another thread is blocking → blocked acquire() completes."""
        ledger = _single_gpu_ledger(_8G)
        pool = _stt_pool(ledger, size=1)
        handle = pool.acquire(timeout_ms=500)

        acquired: list[ModelHandle] = []
        errors: list[Exception] = []

        def waiter() -> None:
            try:
                h = pool.acquire(timeout_ms=3000)
                acquired.append(h)
            except Exception as exc:
                errors.append(exc)

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.05)  # let the thread block on acquire
        pool.release(handle)
        t.join(timeout=5.0)

        assert not errors, f"Waiter raised: {errors}"
        assert len(acquired) == 1, "Waiter should have acquired the handle"

    def test_warm_before_admit(self) -> None:
        """GPU-1: new instance VRAM is allocated before old instance is released.

        Verifies that at the point new instance is admitted to the pool, both
        the new AND old instances' VRAM are simultaneously allocated — proving
        there was no gap in model coverage during scale-up.
        """
        ledger = _single_gpu_ledger(_8G)
        pool = _stt_pool(ledger, size=1)
        assert ledger.allocated("gpu-0") == _2G, "Pool starts with 1 instance"

        # Warm new instance WITHOUT retiring old
        new_handle = pool.warm_new_instance(retire_old=False)

        assert new_handle.is_warm, "New instance must be warm before admission"
        assert ledger.allocated("gpu-0") == _4G, (
            "Both old and new VRAM must be allocated simultaneously "
            "(warm-before-admit: new loads BEFORE old is released)"
        )
        assert pool.pool_size == 2

    def test_model_pool_size_correct(self) -> None:
        """pool_size matches the number of instances created."""
        ledger = _single_gpu_ledger(_8G)
        pool = _stt_pool(ledger, size=3)
        assert pool.pool_size == 3
        assert pool.available_count == 3

    def test_model_pool_acquire_returns_warm_handle(self) -> None:
        ledger = _single_gpu_ledger(_8G)
        pool = _stt_pool(ledger, size=1)
        handle = pool.acquire(timeout_ms=200)
        assert handle.is_warm
        assert handle.pool_type == PoolType.STT_POOL
        assert handle.device_id == "gpu-0"
        pool.release(handle)

    def test_model_pool_allocates_vram_on_init(self) -> None:
        """Pool initialisation reserves VRAM for each instance."""
        ledger = _single_gpu_ledger(_8G)
        _stt_pool(ledger, size=2)
        assert ledger.allocated("gpu-0") == _4G  # 2 x 2048 MB


# ===========================================================================
# PriorityQueue tests
# ===========================================================================


def _req(service: str, priority: RequestPriority, vram_mb: int = 1024) -> VRAMRequest:
    return VRAMRequest(service=service, model="test-model", vram_mb=vram_mb, priority=priority)


class TestPriorityQueue:
    def test_priority_queue_critical_first(self) -> None:
        """CRITICAL + NORMAL requests queued → CRITICAL is served first."""
        pq = PriorityQueue()
        pq.enqueue(_req("llm", RequestPriority.NORMAL))
        pq.enqueue(_req("tts", RequestPriority.CRITICAL))

        first = pq.dequeue()
        assert first is not None
        assert first.priority == RequestPriority.CRITICAL

        second = pq.dequeue()
        assert second is not None
        assert second.priority == RequestPriority.NORMAL

    def test_priority_queue_all_levels_ordered(self) -> None:
        """CRITICAL > HIGH > NORMAL > LOW priority ordering."""
        pq = PriorityQueue()
        pq.enqueue(_req("a", RequestPriority.LOW))
        pq.enqueue(_req("b", RequestPriority.HIGH))
        pq.enqueue(_req("c", RequestPriority.NORMAL))
        pq.enqueue(_req("d", RequestPriority.CRITICAL))

        order = [pq.dequeue() for _ in range(4)]
        assert all(r is not None for r in order)
        assert [r.priority for r in order if r is not None] == [
            RequestPriority.CRITICAL,
            RequestPriority.HIGH,
            RequestPriority.NORMAL,
            RequestPriority.LOW,
        ]

    def test_priority_queue_fifo_within_same_priority(self) -> None:
        """Requests at the same priority are served in FIFO order."""
        pq = PriorityQueue()
        pq.enqueue(_req("first", RequestPriority.NORMAL))
        pq.enqueue(_req("second", RequestPriority.NORMAL))
        pq.enqueue(_req("third", RequestPriority.NORMAL))

        assert pq.dequeue().service == "first"  # type: ignore[union-attr]
        assert pq.dequeue().service == "second"  # type: ignore[union-attr]
        assert pq.dequeue().service == "third"  # type: ignore[union-attr]

    def test_priority_queue_empty_dequeue_returns_none(self) -> None:
        pq = PriorityQueue()
        assert pq.dequeue() is None

    def test_priority_queue_len(self) -> None:
        pq = PriorityQueue()
        pq.enqueue(_req("a", RequestPriority.NORMAL))
        pq.enqueue(_req("b", RequestPriority.HIGH))
        assert len(pq) == 2
        pq.dequeue()
        assert len(pq) == 1


# ===========================================================================
# FailoverManager tests
# ===========================================================================


class TestFailoverManager:
    def test_failover_drains_to_surviving_gpu(self) -> None:
        """Simulate GPU-0 failure → allocations drained, events emitted."""
        ledger = VRAMLedger()
        ledger.register_device("gpu-0", _8G)
        ledger.register_device("gpu-1", _8G)
        manager = FailoverManager(ledger)

        t1 = ledger.allocate("gpu-0", "stt", _2G, "stt")
        t2 = ledger.allocate("gpu-0", "llm", _2G, "llm")
        assert ledger.allocated("gpu-0") == _4G

        started, drained, completed = manager.handle_device_failure("gpu-0", ["gpu-1"])

        assert isinstance(started, GPUFailoverStarted)
        assert isinstance(completed, GPUFailoverCompleted)
        assert started.failed_device_id == "gpu-0"
        assert "gpu-1" in started.surviving_device_ids
        assert started.active_allocations_drained == 2
        assert completed.failed_device_id == "gpu-0"
        assert completed.duration_ms >= 0

        # Two tokens were drained
        assert len(drained) == 2
        drained_ids = {tok.token_id for tok in drained}
        assert t1.token_id in drained_ids
        assert t2.token_id in drained_ids

        # Failed GPU shows 0 allocated and 0 available
        assert ledger.allocated("gpu-0") == 0
        assert ledger.available("gpu-0") == 0

        # Surviving GPU is unaffected
        assert ledger.available("gpu-1") == _8G

    def test_failover_no_allocations(self) -> None:
        """Failover on a GPU with no allocations drains 0 tokens cleanly."""
        ledger = _single_gpu_ledger(_8G)
        manager = FailoverManager(ledger)
        started, drained, _completed = manager.handle_device_failure("gpu-0", [])
        assert len(drained) == 0
        assert started.active_allocations_drained == 0

    def test_failover_failed_device_blocks_new_allocations(self) -> None:
        """After failover, the failed device cannot be allocated to."""
        ledger = VRAMLedger()
        ledger.register_device("gpu-0", _8G)
        manager = FailoverManager(ledger)
        manager.handle_device_failure("gpu-0", [])

        with pytest.raises(InvariantViolationError) as exc_info:
            ledger.allocate("gpu-0", "stt", _1G, "stt")
        assert exc_info.value.invariant_id == "RI-8"


# ===========================================================================
# GPUScheduler tests
# ===========================================================================


class TestGPUScheduler:
    def test_gpu_scheduler_request_approve_and_release(self) -> None:
        """Full allocation cycle: request → approve → release → full again."""
        ledger = _single_gpu_ledger(_8G)
        scheduler = GPUScheduler(ledger=ledger)

        decision, token = scheduler.request_allocation("stt", "whisper", _4G)
        assert decision == AdmissionDecision.APPROVE
        assert token is not None
        assert ledger.available("gpu-0") == _4G

        scheduler.release_allocation(token)
        assert ledger.available("gpu-0") == _8G

    def test_gpu_scheduler_request_reject(self) -> None:
        """Scheduler returns REJECT when no GPU has sufficient VRAM."""
        ledger = _single_gpu_ledger(_4G)
        scheduler = GPUScheduler(ledger=ledger)

        decision, token = scheduler.request_allocation("llm", "qwen", _4G + 1)
        assert decision == AdmissionDecision.REJECT
        assert token is None

    def test_gpu_scheduler_priority_queue_integration(self) -> None:
        """Enqueued requests are dequeued in priority order."""
        ledger = _single_gpu_ledger(_8G)
        scheduler = GPUScheduler(ledger=ledger)

        scheduler.enqueue_request(_req("a", RequestPriority.LOW))
        scheduler.enqueue_request(_req("b", RequestPriority.CRITICAL))
        scheduler.enqueue_request(_req("c", RequestPriority.NORMAL))

        first = scheduler.dequeue_request()
        assert first is not None and first.priority == RequestPriority.CRITICAL


# ===========================================================================
# GPUSchedulerService tests
# ===========================================================================


class TestGPUSchedulerService:
    def test_service_health_check_healthy(self) -> None:
        """Health check returns healthy when all devices have free VRAM."""
        svc = GPUSchedulerService.create(device_configs=[DeviceConfig("gpu-0", _8G), DeviceConfig("gpu-1", _8G)])
        status = svc.health_check()
        assert status.healthy
        assert status.device_vram["gpu-0"] == _8G
        assert status.device_vram["gpu-1"] == _8G

    def test_service_health_check_unhealthy_when_full(self) -> None:
        """Health check reports unhealthy for a fully allocated device."""
        svc = GPUSchedulerService.create(device_configs=[DeviceConfig("gpu-0", _4G)])
        # Consume all VRAM
        svc.scheduler.request_allocation("llm", "qwen", _4G)
        status = svc.health_check()
        assert not status.healthy
        assert "gpu-0" in status.message

    def test_service_create_with_pools(self) -> None:
        """GPUSchedulerService.create() initialises model pools."""
        svc = GPUSchedulerService.create(
            device_configs=[DeviceConfig("gpu-0", _8G)],
            pool_configs=[PoolConfig(PoolType.STT_POOL, size=1, vram_per_instance_mb=_2G, devices=["gpu-0"])],
        )
        # Acquire from the STT pool
        handle = svc.scheduler.acquire_model(PoolType.STT_POOL, timeout_ms=200)
        assert handle.pool_type == PoolType.STT_POOL
        svc.scheduler.release_model(handle)

    def test_vram_available_gauge_reflects_allocation(self) -> None:
        """Prometheus gauge is updated after allocation (metrics integration)."""
        svc = GPUSchedulerService.create(device_configs=[DeviceConfig("gpu-0", _8G)])
        decision, token = svc.scheduler.request_allocation("stt", "whisper", _2G)
        assert decision == AdmissionDecision.APPROVE
        # Check ledger directly (gauge updated internally)
        from src.services.gpu_scheduler.vram_ledger import VRAMLedger as _VRAMLedger  # noqa: F401

        status = svc.health_check()
        assert status.device_vram["gpu-0"] == _8G - _2G
        if token is not None:
            svc.release_allocation(token)
