"""GPUEfficiencyAnalyzer -- fleet GPU utilization ratio + pool-size recommendations (V7 Ch15).

Deliberately takes a small injected ``VRAMUsageProvider`` Protocol rather
than importing ``monitoring.gpu_fleet`` directly: ``src/services/`` stays
independent of the ``monitoring/`` operational-config tree (the same
"communicate through a narrow port, not a concrete cross-tree import"
discipline this codebase already applies to every other service
boundary, Volume 6 Ch2). A real deployment wires this port to
``monitoring.gpu_fleet.vram_budget.FleetVRAMBudget.utilization_ratio()``
at the composition root; unit tests inject a static fixture.

Architecture: V7 Ch15 (Cost Optimization) -- target >= 80% utilization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

TARGET_UTILIZATION = 0.80


class VRAMUsageProvider(Protocol):
    def utilization_ratio(self) -> float: ...


@dataclass(frozen=True)
class PoolAdjustmentRecommendation:
    action: str
    """'scale_down' | 'scale_up' | 'hold'."""
    rationale: str


class GPUEfficiencyAnalyzer:
    """Computes fleet-wide GPU VRAM utilization and recommends pool-size adjustments."""

    def __init__(
        self, vram_usage_provider: VRAMUsageProvider, *, target_utilization: float = TARGET_UTILIZATION
    ) -> None:
        self._vram_usage_provider = vram_usage_provider
        self._target_utilization = target_utilization

    def utilization(self) -> float:
        """Fleet-level GPU utilization ratio: total VRAM used / total VRAM available."""
        return self._vram_usage_provider.utilization_ratio()

    def recommend_pool_adjustment(self) -> PoolAdjustmentRecommendation:
        """Recommend scaling the GPU pool up/down based on the utilization gap to target."""
        ratio = self.utilization()
        if ratio < self._target_utilization - 0.15:
            return PoolAdjustmentRecommendation(
                action="scale_down",
                rationale=(
                    f"Fleet utilization {ratio:.0%} is well below the {self._target_utilization:.0%} target -- "
                    "the pool is over-provisioned; consider draining a node."
                ),
            )
        if ratio > 0.95:
            return PoolAdjustmentRecommendation(
                action="scale_up",
                rationale=(
                    f"Fleet utilization {ratio:.0%} is near saturation -- admission rejections are likely; "
                    "consider adding a node before the next capacity review."
                ),
            )
        return PoolAdjustmentRecommendation(
            action="hold",
            rationale=f"Fleet utilization {ratio:.0%} is within the acceptable band around the {self._target_utilization:.0%} target.",
        )


__all__ = ["TARGET_UTILIZATION", "GPUEfficiencyAnalyzer", "PoolAdjustmentRecommendation", "VRAMUsageProvider"]
