"""FleetVRAMBudget -- fleet-level VRAM allocation accounting (V7 Ch6).

Each GPU node runs its own Sprint-008 ``VRAMLedger`` for local admission
decisions; ``FleetVRAMBudget`` aggregates each node's reported usage into
one fleet-wide budget view for the ``gpu-fleet.json`` Grafana dashboard
and for ``CostOptimizer.gpu_efficiency()`` (Sprint-027's cost-optimizer
service, which reads the same fleet-wide ratio for its own utilization
target).

Architecture: V7 Ch6 (GPU Fleet Management); V7 Ch15 (Cost Optimization).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .metrics import update_fleet_vram_gauges


@dataclass(frozen=True)
class NodeVRAMUsage:
    """One GPU node's VRAM usage snapshot, in megabytes."""

    node_id: str
    used_mb: int
    total_mb: int


class FleetVRAMBudget:
    """Aggregates per-node VRAM usage into a fleet-wide budget."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nodes: dict[str, NodeVRAMUsage] = {}

    def record_node_usage(self, usage: NodeVRAMUsage) -> None:
        with self._lock:
            self._nodes[usage.node_id] = usage

    def total_used_mb(self) -> int:
        with self._lock:
            return sum(n.used_mb for n in self._nodes.values())

    def total_budget_mb(self) -> int:
        with self._lock:
            return sum(n.total_mb for n in self._nodes.values())

    def utilization_ratio(self) -> float:
        """Fleet-wide VRAM utilization: total used / total budget.

        Returns 0.0 when no nodes have reported usage yet (budget of 0),
        rather than raising a ZeroDivisionError -- an empty fleet has
        nothing to be "over-utilized" relative to.
        """
        used = self.total_used_mb()
        budget = self.total_budget_mb()
        update_fleet_vram_gauges(used, budget)
        if budget == 0:
            return 0.0
        return used / budget

    def headroom_mb(self) -> int:
        """Remaining fleet-wide VRAM capacity, never negative."""
        return max(0, self.total_budget_mb() - self.total_used_mb())


__all__ = ["FleetVRAMBudget", "NodeVRAMUsage"]
