"""GPUSchedulerService — service façade with health-check and lifecycle management.

Wraps GPUScheduler with factory helpers for building production and test
configurations, and exposes a health_check() method for readiness probes.

Architecture: V1 Ch7 (GPU Scheduler service API); V7 Ch6.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model_pool import ModelPool, PoolType
from .scheduler import GPUScheduler
from .vram_ledger import AllocationToken, VRAMLedger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class DeviceConfig:
    """Configuration for a single GPU device."""

    device_id: str
    """Unique identifier (e.g. 'gpu-0', 'cuda:0')."""
    total_vram_mb: int
    """Total VRAM in MB (e.g. 24576 for an A100-24G)."""


@dataclass
class PoolConfig:
    """Configuration for a model pool."""

    pool_type: PoolType
    """Which pool (STT / LLM / TTS)."""
    size: int
    """Number of pre-warmed model instances."""
    vram_per_instance_mb: int
    """VRAM cost per instance in MB."""
    devices: list[str]
    """Device IDs the pool should spread instances across."""


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@dataclass
class HealthStatus:
    """Result of GPUSchedulerService.health_check()."""

    healthy: bool
    """True when all registered devices have > 0 available VRAM."""
    device_vram: dict[str, int]
    """Mapping of device_id → available_vram_mb for each registered device."""
    message: str
    """Human-readable summary."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class GPUSchedulerService:
    """Lifecycle-managed façade around GPUScheduler.

    Build with GPUSchedulerService.create() supplying device and pool
    configurations.  Call health_check() for readiness probes.

    Architecture: V1 Ch7; V7 Ch6.
    """

    def __init__(self, scheduler: GPUScheduler, ledger: VRAMLedger) -> None:
        self._scheduler = scheduler
        self._ledger = ledger

    @classmethod
    def create(
        cls,
        device_configs: list[DeviceConfig],
        pool_configs: list[PoolConfig] | None = None,
    ) -> GPUSchedulerService:
        """Factory: build a fully configured GPUSchedulerService.

        Args:
            device_configs: List of GPU devices to register.
            pool_configs:   Optional list of model pool configurations.
                            Pass None to create a scheduler without pools.

        Returns:
            A ready-to-use GPUSchedulerService.
        """
        ledger = VRAMLedger()
        for dc in device_configs:
            ledger.register_device(dc.device_id, dc.total_vram_mb)

        pools: dict[PoolType, ModelPool] = {}
        for pc in pool_configs or []:
            pools[pc.pool_type] = ModelPool(
                pool_type=pc.pool_type,
                size=pc.size,
                vram_per_instance_mb=pc.vram_per_instance_mb,
                ledger=ledger,
                devices=pc.devices,
            )

        scheduler = GPUScheduler(ledger=ledger, pools=pools)
        return cls(scheduler=scheduler, ledger=ledger)

    # ------------------------------------------------------------------
    # Delegation to scheduler
    # ------------------------------------------------------------------

    @property
    def scheduler(self) -> GPUScheduler:
        """The underlying GPUScheduler instance."""
        return self._scheduler

    def release_allocation(self, token: AllocationToken) -> None:
        """Release a VRAM allocation back to the pool."""
        self._scheduler.release_allocation(token)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_check(self) -> HealthStatus:
        """Return the current health of all registered GPU devices.

        A device is considered healthy when it has > 0 MB available VRAM.
        The overall status is healthy only when ALL devices are healthy.
        """
        device_vram: dict[str, int] = {}
        for device_id in self._ledger.device_ids():
            device_vram[device_id] = self._ledger.available(device_id)

        all_healthy = all(v > 0 for v in device_vram.values()) if device_vram else False
        if all_healthy:
            msg = f"All {len(device_vram)} GPU device(s) healthy"
        else:
            unhealthy = [d for d, v in device_vram.items() if v == 0]
            msg = f"Unhealthy device(s): {', '.join(unhealthy)}"

        return HealthStatus(
            healthy=all_healthy,
            device_vram=device_vram,
            message=msg,
        )
