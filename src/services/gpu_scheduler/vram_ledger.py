"""VRAMLedger — per-GPU VRAM allocation tracking.

Maintains an atomic, thread-safe accounting of allocated vs. available VRAM
across all registered GPU devices. Every allocation calls assert_ri8_oom_by_construction
to enforce the OOM-by-construction guarantee before deducting VRAM.

Architecture: V1 Ch7 (GPU Scheduler — VRAM ledger); V1 Appendix E RI-8.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from src.libs.invariants.guards import assert_ri8_oom_by_construction

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllocationToken:
    """Opaque token returned by VRAMLedger.allocate().

    Hold this token to keep the VRAM reserved; pass it to release() to
    return the VRAM to the available pool.
    """

    token_id: str
    """Unique identifier for this allocation (UUID)."""
    device_id: str
    """The GPU device on which VRAM was allocated."""
    model_id: str
    """The model or service that holds this allocation."""
    vram_mb: int
    """Amount of VRAM reserved in megabytes."""


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _DeviceState:
    """Mutable per-device VRAM accounting. Not exposed outside this module."""

    device_id: str
    total_vram_mb: int
    allocated_vram_mb: int = field(default=0)
    healthy: bool = field(default=True)

    @property
    def available_vram_mb(self) -> int:
        """Available VRAM: 0 when the device is unhealthy (failed)."""
        if not self.healthy:
            return 0
        return max(0, self.total_vram_mb - self.allocated_vram_mb)


# ---------------------------------------------------------------------------
# VRAMLedger
# ---------------------------------------------------------------------------


class VRAMLedger:
    """Thread-safe VRAM accounting ledger for all registered GPU devices.

    Responsibilities:
    - Registers GPU devices with their total VRAM capacity.
    - Atomically allocates and releases VRAM, returning AllocationTokens.
    - Enforces RI-8 (OOM-by-construction) on every allocate() call.
    - Supports device failure marking and forced drain for failover.

    Note: In Sprint-008 state is in-memory only.  Redis hot-state and Postgres
    authoritative backup will be wired in Sprint-013/014 when those layers exist.

    Architecture: V1 Ch7 (VRAM ledger); V1 Appendix E RI-8.
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._devices: dict[str, _DeviceState] = {}
        self._tokens: dict[str, AllocationToken] = {}

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    def register_device(self, device_id: str, total_vram_mb: int) -> None:
        """Register a GPU device with its total VRAM capacity in MB."""
        with self._lock:
            self._devices[device_id] = _DeviceState(
                device_id=device_id,
                total_vram_mb=total_vram_mb,
            )

    def device_ids(self) -> list[str]:
        """Return all registered device IDs."""
        with self._lock:
            return list(self._devices.keys())

    def mark_device_failed(self, device_id: str) -> None:
        """Mark a device as failed; available() will return 0 for it."""
        with self._lock:
            if device_id in self._devices:
                self._devices[device_id].healthy = False

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    def allocate(
        self,
        device_id: str,
        model_id: str,
        vram_mb: int,
        service_name: str = "unknown",
    ) -> AllocationToken:
        """Allocate ``vram_mb`` on ``device_id``, returning an AllocationToken.

        RI-8 is checked *before* any deduction — if requested > available the
        invariant guard raises InvariantViolationError and no VRAM is deducted.

        Args:
            device_id:    Target GPU device.
            model_id:     Model or service identifier (for audit / logging).
            vram_mb:      Megabytes to reserve.
            service_name: Caller name passed to RI-8 context for alerts.

        Returns:
            AllocationToken holding the reservation.

        Raises:
            InvariantViolationError: RI-8 when requested_vram_mb > available.
            KeyError: when device_id is not registered.
        """
        with self._lock:
            device = self._devices[device_id]
            assert_ri8_oom_by_construction(vram_mb, device.available_vram_mb, service_name)
            device.allocated_vram_mb += vram_mb
            token = AllocationToken(
                token_id=str(uuid.uuid4()),
                device_id=device_id,
                model_id=model_id,
                vram_mb=vram_mb,
            )
            self._tokens[token.token_id] = token
            return token

    def release(self, token: AllocationToken) -> None:
        """Return ``token.vram_mb`` to the available pool.

        Idempotent — releasing an already-released token is a no-op.
        """
        with self._lock:
            if token.token_id not in self._tokens:
                return
            device = self._devices[token.device_id]
            device.allocated_vram_mb = max(0, device.allocated_vram_mb - token.vram_mb)
            del self._tokens[token.token_id]

    def drain_device(self, device_id: str) -> list[AllocationToken]:
        """Forcibly release all allocations on ``device_id`` (failover path).

        Returns the list of tokens that were drained so callers can notify
        affected services.
        """
        with self._lock:
            drained: list[AllocationToken] = []
            for token_id, token in list(self._tokens.items()):
                if token.device_id == device_id:
                    drained.append(token)
                    del self._tokens[token_id]
            if device_id in self._devices:
                self._devices[device_id].allocated_vram_mb = 0
            return drained

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def available(self, device_id: str) -> int:
        """Return currently available VRAM for ``device_id`` in MB."""
        with self._lock:
            return self._devices[device_id].available_vram_mb

    def allocated(self, device_id: str) -> int:
        """Return currently allocated VRAM for ``device_id`` in MB."""
        with self._lock:
            return self._devices[device_id].allocated_vram_mb

    def best_fit_device(self, required_vram_mb: int) -> str | None:
        """Return the healthy device with the most available VRAM that satisfies
        the request (best-fit selection policy per admission spec).

        Returns None when no device has enough free VRAM.
        """
        with self._lock:
            candidates = [d for d in self._devices.values() if d.healthy and d.available_vram_mb >= required_vram_mb]
            if not candidates:
                return None
            return max(candidates, key=lambda d: d.available_vram_mb).device_id
