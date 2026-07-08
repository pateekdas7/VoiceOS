"""GPU Fleet Management operational layer (Sprint-027, V7 Ch6).

Extends the GPU Scheduler (Sprint-008, single-node VRAM ledger + admission
control) with fleet-level operational tooling: cross-node health
aggregation, warm-before-admit node onboarding, and fleet-wide VRAM budget
accounting.

Deviation from Sprint-027.md's literal path: the spec names this
directory ``monitoring/gpu-fleet/`` (hyphen), which is not a valid Python
package name. Named ``monitoring/gpu_fleet/`` (underscore) instead, same
precedent as every prior sprint's dashed-path-to-underscore-package fix
(``admin-portal``->``admin_portal``, ``bi-platform``->``bi_platform``, etc).
"""

from __future__ import annotations

from .fleet_health import GPUFleetHealthMonitor, GPUNodeSnapshot
from .vram_budget import FleetVRAMBudget, NodeVRAMUsage
from .warmup import REQUIRED_MODEL_POOLS, ModelWarmupOrchestrator

__all__ = [
    "REQUIRED_MODEL_POOLS",
    "FleetVRAMBudget",
    "GPUFleetHealthMonitor",
    "GPUNodeSnapshot",
    "ModelWarmupOrchestrator",
    "NodeVRAMUsage",
]
