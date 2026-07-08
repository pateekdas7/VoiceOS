"""GPUScheduler — central VRAM admission and allocation coordinator.

The GPUScheduler is the single entry point for all GPU resource requests.
It integrates:
- VRAMLedger (VRAM accounting per device)
- AdmissionController (APPROVE / REJECT decisions)
- ModelPool registry (STT / LLM / TTS pools)
- FailoverManager (device failure handling)
- PriorityQueue (request ordering)
- Prometheus metrics

Usage pattern:
    1. Call request_allocation() to get an AllocationToken (or REJECT).
    2. Use the token for inference.
    3. Call release_allocation() when inference is done.

Architecture: V1 Ch7 (GPU Scheduler); V7 Ch6 (GPU fleet management).
"""

from __future__ import annotations

from .admission import AdmissionController, AdmissionDecision
from .failover import FailoverManager
from .metrics import record_admission, update_vram_gauges
from .model_pool import ModelHandle, ModelPool, PoolType
from .priority_queue import PriorityQueue, RequestPriority, VRAMRequest
from .vram_ledger import AllocationToken, VRAMLedger


class GPUScheduler:
    """Coordinates VRAM allocation, model pools, and failover across all GPU devices.

    Thread-safe.  Intended to be a singleton per process — one instance serves
    all STT, LLM, and TTS adapter services.

    Architecture: V1 Ch7 (GPU Scheduler); V7 Ch6.
    """

    def __init__(
        self,
        ledger: VRAMLedger,
        pools: dict[PoolType, ModelPool] | None = None,
    ) -> None:
        self._ledger = ledger
        self._admission = AdmissionController(ledger)
        self._failover = FailoverManager(ledger)
        self._pools: dict[PoolType, ModelPool] = pools or {}
        self._priority_queue: PriorityQueue = PriorityQueue()

    # ------------------------------------------------------------------
    # VRAM allocation API
    # ------------------------------------------------------------------

    def request_allocation(
        self,
        service: str,
        model: str,
        required_vram_mb: int,
        priority: RequestPriority = RequestPriority.NORMAL,
    ) -> tuple[AdmissionDecision, AllocationToken | None]:
        """Request VRAM allocation for a model inference.

        Runs the admission control check.  On APPROVE, atomically allocates
        VRAM and returns an AllocationToken.  On REJECT, no VRAM is touched
        and the caller must handle the capacity-full condition.

        Args:
            service:          Requesting service name (for metrics/logs).
            model:            Model identifier.
            required_vram_mb: VRAM needed in MB.
            priority:         Request priority (affects queue ordering).

        Returns:
            ``(APPROVE, AllocationToken)`` or ``(REJECT, None)``.
        """
        decision, device_id = self._admission.decide(service, model, required_vram_mb)
        record_admission(service, approved=decision == AdmissionDecision.APPROVE)

        if decision == AdmissionDecision.REJECT or device_id is None:
            return AdmissionDecision.REJECT, None

        token = self._ledger.allocate(
            device_id=device_id,
            model_id=model,
            vram_mb=required_vram_mb,
            service_name=service,
        )
        self._refresh_metrics(device_id)
        return AdmissionDecision.APPROVE, token

    def release_allocation(self, token: AllocationToken) -> None:
        """Release a previously approved VRAM allocation."""
        device_id = token.device_id
        self._ledger.release(token)
        self._refresh_metrics(device_id)

    # ------------------------------------------------------------------
    # Model pool API
    # ------------------------------------------------------------------

    def register_pool(self, pool_type: PoolType, pool: ModelPool) -> None:
        """Register a ModelPool with the scheduler."""
        self._pools[pool_type] = pool

    def acquire_model(
        self,
        pool_type: PoolType,
        timeout_ms: int = 5000,
    ) -> ModelHandle:
        """Acquire a model instance from the given pool.

        Args:
            pool_type:  Which pool to acquire from (STT / LLM / TTS).
            timeout_ms: Maximum wait time in milliseconds.

        Returns:
            A ModelHandle for exclusive use during inference.

        Raises:
            KeyError: when pool_type is not registered.
            TimeoutError: when no instance is available within timeout_ms.
        """
        return self._pools[pool_type].acquire(timeout_ms=timeout_ms)

    def release_model(self, handle: ModelHandle) -> None:
        """Return a model handle to its pool."""
        pool = self._pools.get(handle.pool_type)
        if pool is not None:
            pool.release(handle)

    # ------------------------------------------------------------------
    # Priority queue (for ordered request processing)
    # ------------------------------------------------------------------

    def enqueue_request(self, request: VRAMRequest) -> None:
        """Add a VRAM request to the priority queue."""
        self._priority_queue.enqueue(request)

    def dequeue_request(self) -> VRAMRequest | None:
        """Dequeue the highest-priority pending request."""
        return self._priority_queue.dequeue()

    # ------------------------------------------------------------------
    # Failover
    # ------------------------------------------------------------------

    def handle_device_failure(self, failed_device_id: str) -> None:
        """Respond to a GPU device failure.

        Marks the device failed, drains its allocations, and updates metrics.
        The domain events are returned for callers that need to emit them.
        """
        surviving = [d for d in self._ledger.device_ids() if d != failed_device_id]
        self._failover.handle_device_failure(failed_device_id, surviving)
        # Update metrics for all surviving devices after failover.
        for device_id in surviving:
            self._refresh_metrics(device_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_metrics(self, device_id: str) -> None:
        """Push current VRAM gauge values for a device to Prometheus."""
        update_vram_gauges(
            device_id=device_id,
            used_mb=self._ledger.allocated(device_id),
            available_mb=self._ledger.available(device_id),
        )
