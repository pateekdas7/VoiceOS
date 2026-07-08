"""Integration tests for the GPU Scheduler service.

Tests concurrent allocation correctness under multi-threaded load and
validates that the full scheduler pipeline (VRAMLedger + Admission +
GPUScheduler) behaves atomically under race conditions.

Architecture: V1 Ch7 (GPU Scheduler); V1 Appendix E RI-8.
"""

from __future__ import annotations

import threading
from typing import NamedTuple

from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.model_pool import PoolType
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.gpu_scheduler.service import DeviceConfig, GPUSchedulerService, PoolConfig
from src.services.gpu_scheduler.vram_ledger import AllocationToken, VRAMLedger

_8G = 8192
_4G = 4096
_2G = 2048
_1G = 1024


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


class _AllocResult(NamedTuple):
    decision: AdmissionDecision
    token: AllocationToken | None


# ===========================================================================
# Concurrent allocation test
# ===========================================================================


class TestGPUSchedulerConcurrent:
    def test_gpu_scheduler_concurrent_requests(self) -> None:
        """10 concurrent allocation requests → exactly 8 approved, 2 rejected.

        A single GPU-0 has 8192 MB; each request asks for 1024 MB.
        8 requests fit exactly; the remaining 2 must be REJECTED cleanly
        (not silently dropped, not OOM).  Thread-safety of VRAMLedger is
        verified by asserting the final allocated total exactly matches
        8 x 1024 MB.
        """
        ledger = VRAMLedger()
        ledger.register_device("gpu-0", _8G)
        scheduler = GPUScheduler(ledger=ledger)

        results: list[_AllocResult] = []
        lock = threading.Lock()

        def worker() -> None:
            decision, token = scheduler.request_allocation(service="stt", model="whisper-large", required_vram_mb=_1G)
            with lock:
                results.append(_AllocResult(decision, token))

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert len(results) == 10, "All 10 threads must complete"

        approvals = [r for r in results if r.decision == AdmissionDecision.APPROVE]
        rejections = [r for r in results if r.decision == AdmissionDecision.REJECT]

        assert len(approvals) == 8, f"Expected 8 approvals, got {len(approvals)}"
        assert len(rejections) == 2, f"Expected 2 rejections, got {len(rejections)}"

        for r in approvals:
            assert r.token is not None, "Approved request must have a token"
        for r in rejections:
            assert r.token is None, "Rejected request must have no token"

        # Total allocated must exactly match 8 x 1024 MB (no double-counting)
        assert ledger.allocated("gpu-0") == 8 * _1G

        # Release all and verify full recovery
        for r in approvals:
            if r.token is not None:
                scheduler.release_allocation(r.token)

        assert ledger.available("gpu-0") == _8G

    def test_gpu_scheduler_concurrent_mixed_services(self) -> None:
        """Concurrent requests from STT, LLM, and TTS services are handled safely."""
        ledger = VRAMLedger()
        ledger.register_device("gpu-0", _8G)
        scheduler = GPUScheduler(ledger=ledger)

        results: list[_AllocResult] = []
        lock = threading.Lock()

        def worker(service: str, vram_mb: int) -> None:
            decision, token = scheduler.request_allocation(service, "model-v1", vram_mb)
            with lock:
                results.append(_AllocResult(decision, token))

        # 2 STT (1024 MB each) + 1 LLM (4096 MB) + 2 TTS (512 MB each) = 7168 MB total
        specs = [
            ("stt", _1G),
            ("stt", _1G),
            ("llm", _4G),
            ("tts", 512),
            ("tts", 512),
        ]
        threads = [threading.Thread(target=worker, args=(s, v), daemon=True) for s, v in specs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        approvals = [r for r in results if r.decision == AdmissionDecision.APPROVE]
        # With 8192 MB total and 7168 MB requested, all 5 must fit if serialised.
        # Under concurrency, all should fit (no single request exceeds 8192 MB).
        assert len(approvals) == 5, "All 5 requests fit within 8192 MB"

        for r in approvals:
            if r.token is not None:
                scheduler.release_allocation(r.token)
        assert ledger.available("gpu-0") == _8G

    def test_concurrent_release_then_reallocate(self) -> None:
        """Released VRAM is immediately available for re-allocation under load."""
        ledger = VRAMLedger()
        ledger.register_device("gpu-0", _4G)
        scheduler = GPUScheduler(ledger=ledger)

        # Fill GPU
        _, t1 = scheduler.request_allocation("stt", "whisper", _2G)
        _, t2 = scheduler.request_allocation("tts", "veena", _2G)
        assert ledger.available("gpu-0") == 0

        # Release one token
        if t1 is not None:
            scheduler.release_allocation(t1)

        # Now a new request should be approved
        decision, token = scheduler.request_allocation("stt", "whisper", _2G)
        assert decision == AdmissionDecision.APPROVE
        assert token is not None

        if token is not None:
            scheduler.release_allocation(token)
        if t2 is not None:
            scheduler.release_allocation(t2)

        assert ledger.available("gpu-0") == _4G


# ===========================================================================
# Multi-device integration
# ===========================================================================


class TestMultiDeviceScheduler:
    def test_scheduler_spreads_across_devices(self) -> None:
        """Large requests are routed to the GPU with the most available VRAM."""
        svc = GPUSchedulerService.create(
            device_configs=[
                DeviceConfig("gpu-0", _8G),
                DeviceConfig("gpu-1", _4G),
            ]
        )
        # First request goes to gpu-0 (most VRAM)
        decision, token = svc.scheduler.request_allocation("llm", "qwen", _8G - 1)
        assert decision == AdmissionDecision.APPROVE
        assert token is not None and token.device_id == "gpu-0"

        # Next large request goes to gpu-1 (gpu-0 is nearly full)
        decision2, token2 = svc.scheduler.request_allocation("stt", "whisper", _4G - 1)
        assert decision2 == AdmissionDecision.APPROVE
        assert token2 is not None and token2.device_id == "gpu-1"

        if token is not None:
            svc.scheduler.release_allocation(token)
        if token2 is not None:
            svc.scheduler.release_allocation(token2)

    def test_scheduler_failover_after_device_failure(self) -> None:
        """After GPU-0 fails, new requests are routed to GPU-1."""
        svc = GPUSchedulerService.create(
            device_configs=[
                DeviceConfig("gpu-0", _8G),
                DeviceConfig("gpu-1", _8G),
            ]
        )
        # Allocate on GPU-0
        decision, token = svc.scheduler.request_allocation("stt", "whisper", _4G)
        assert decision == AdmissionDecision.APPROVE
        assert token is not None and token.device_id == "gpu-0"

        # Simulate GPU-0 failure
        svc.scheduler.handle_device_failure("gpu-0")

        # New allocation must go to GPU-1
        decision2, token2 = svc.scheduler.request_allocation("stt", "whisper", _4G)
        assert decision2 == AdmissionDecision.APPROVE
        assert token2 is not None and token2.device_id == "gpu-1"

        if token2 is not None:
            svc.scheduler.release_allocation(token2)


# ===========================================================================
# Pool integration
# ===========================================================================


class TestModelPoolIntegration:
    def test_concurrent_pool_acquire_release(self) -> None:
        """Multiple threads acquire and release model handles without deadlock."""
        svc = GPUSchedulerService.create(
            device_configs=[DeviceConfig("gpu-0", _8G)],
            pool_configs=[PoolConfig(PoolType.STT_POOL, size=3, vram_per_instance_mb=_1G, devices=["gpu-0"])],
        )
        handles_acquired: list[object] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                h = svc.scheduler.acquire_model(PoolType.STT_POOL, timeout_ms=2000)
                with lock:
                    handles_acquired.append(h)
                threading.Event().wait(0.01)  # hold briefly
                svc.scheduler.release_model(h)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)

        assert not errors, f"Pool workers raised: {errors}"
        assert len(handles_acquired) == 6
