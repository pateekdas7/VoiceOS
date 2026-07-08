"""FailoverManager — GPU device failure detection and graceful drain.

When a GPU device fails the FailoverManager:
1. Marks the device as failed in the VRAMLedger (no new allocations).
2. Drains all active VRAM allocations from the failed device.
3. Emits GPUFailoverStarted and GPUFailoverCompleted domain events.

Active sessions affected by the failure are expected to be re-routed by the
services that hold the drained tokens — the FailoverManager does not own
session state. New allocation requests will be directed to surviving GPUs by
the AdmissionController (via best_fit_device) automatically, since the failed
device's available VRAM is 0 after mark_device_failed().

Architecture: V1 Ch7 (GPU Scheduler GPU-2 graceful failover); V7 Ch6.
"""

from __future__ import annotations

import time

from src.libs.contracts.events.reliability_events import (
    GPUFailoverCompleted,
    GPUFailoverStarted,
)
from src.libs.contracts.primitives import TenantId

from .vram_ledger import AllocationToken, VRAMLedger


class FailoverManager:
    """Handles GPU device failures by draining sessions to surviving GPUs.

    Architecture: V1 Ch7 (GPU-2 graceful failover); V7 Ch6.
    """

    def __init__(self, ledger: VRAMLedger, tenant_id: str = "system") -> None:
        self._ledger = ledger
        self._tenant_id: TenantId = TenantId(tenant_id)

    def handle_device_failure(
        self,
        failed_device_id: str,
        surviving_gpus: list[str],
    ) -> tuple[GPUFailoverStarted, list[AllocationToken], GPUFailoverCompleted]:
        """Respond to a GPU device failure.

        Marks the device failed, drains its allocations, and returns the two
        domain events that callers should emit to the event bus.

        Args:
            failed_device_id: The device ID of the failed GPU.
            surviving_gpus:   Device IDs that will absorb future requests.

        Returns:
            A tuple of:
            - GPUFailoverStarted event
            - list of AllocationTokens that were drained (for caller notification)
            - GPUFailoverCompleted event
        """
        start_ms = int(time.monotonic() * 1000)

        # Step 1 — mark device failed (new allocations on it will be rejected).
        self._ledger.mark_device_failed(failed_device_id)

        # Step 2 — drain all active allocations from the failed device.
        drained_tokens = self._ledger.drain_device(failed_device_id)

        started_event = GPUFailoverStarted(
            tenant_id=self._tenant_id,
            failed_device_id=failed_device_id,
            surviving_device_ids=list(surviving_gpus),
            active_allocations_drained=len(drained_tokens),
        )

        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        completed_event = GPUFailoverCompleted(
            tenant_id=self._tenant_id,
            failed_device_id=failed_device_id,
            surviving_device_ids=list(surviving_gpus),
            duration_ms=elapsed_ms,
        )

        return started_event, drained_tokens, completed_event
