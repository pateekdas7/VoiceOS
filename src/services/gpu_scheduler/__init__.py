"""GPU Scheduler service — system-wide VRAM ledger and admission control.

Provides:
- VRAMLedger: per-GPU VRAM accounting with RI-8 enforcement.
- AdmissionController: APPROVE/REJECT decisions (best-fit device selection).
- ModelPool: leased model instances with warm-before-admit guarantee.
- PriorityQueue: CRITICAL > HIGH > NORMAL > LOW request ordering.
- FailoverManager: GPU device failure drain and event emission.
- GPUScheduler: central coordinator.
- GPUSchedulerService: lifecycle façade with health-check.

Architecture: V1 Ch7 (GPU Scheduler); V7 Ch6 (GPU Fleet Management).
"""

from .admission import AdmissionController, AdmissionDecision
from .failover import FailoverManager
from .model_pool import ModelHandle, ModelPool, PoolType
from .priority_queue import PriorityQueue, RequestPriority, VRAMRequest
from .scheduler import GPUScheduler
from .service import DeviceConfig, GPUSchedulerService, HealthStatus, PoolConfig
from .vram_ledger import AllocationToken, VRAMLedger

__all__ = [
    "AdmissionController",
    "AdmissionDecision",
    "AllocationToken",
    "DeviceConfig",
    "FailoverManager",
    "GPUScheduler",
    "GPUSchedulerService",
    "HealthStatus",
    "ModelHandle",
    "ModelPool",
    "PoolConfig",
    "PoolType",
    "PriorityQueue",
    "RequestPriority",
    "VRAMLedger",
    "VRAMRequest",
]
