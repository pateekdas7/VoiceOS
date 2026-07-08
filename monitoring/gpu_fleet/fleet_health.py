"""GPUFleetHealthMonitor -- fleet-level GPU pool health aggregation (V7 Ch6).

Extends the per-device VRAMLedger (Sprint-008, single GPU node) with a
fleet-level view spanning multiple GPU nodes. Each node reports its own
health/VRAM snapshot via :meth:`report_node`; this monitor aggregates
those into the single ``fleet_health_score()`` the
``GPUFleetDegraded``/``GPUFleetSevereDegradation`` Prometheus alerts
(``monitoring/prometheus/alert_rules/infrastructure.yml``) and the
``gpu-fleet.json`` Grafana dashboard both read.

Today's actual fleet is a single GPU node (``GPU_NODE_STATE.md``, not a
cluster member per TT-015) -- this monitor's multi-node aggregation is
validated here against simulated fixtures (Sprint-027.md's own Phase 1
scope: "GPU node state: in-memory fixture dict, simulates a 4-node
fleet"), ready for when a second GPU node is provisioned.

Architecture: V7 Ch6 (GPU Fleet Management).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .metrics import set_fleet_health_score, set_node_healthy


@dataclass(frozen=True)
class GPUNodeSnapshot:
    """One GPU node's most recently reported health/VRAM state."""

    node_id: str
    healthy: bool
    vram_used_mb: int
    vram_total_mb: int


class GPUFleetHealthMonitor:
    """Aggregates per-node health reports into one fleet-level score.

    ``fleet_health_score()`` is the fraction of registered nodes currently
    reporting ``healthy=True`` -- 1.0 when every node is healthy, <= 0.5
    once a majority have failed (Sprint-027.md's own AC thresholds:
    CRITICAL alert below 0.8, page on-call below 0.5).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nodes: dict[str, GPUNodeSnapshot] = {}

    def report_node(self, snapshot: GPUNodeSnapshot) -> None:
        """Register or update a node's latest health/VRAM snapshot."""
        with self._lock:
            self._nodes[snapshot.node_id] = snapshot
        set_node_healthy(snapshot.node_id, snapshot.healthy)

    def remove_node(self, node_id: str) -> None:
        """Deregister a node entirely (e.g. decommissioned, not just failed)."""
        with self._lock:
            self._nodes.pop(node_id, None)

    def node_snapshots(self) -> tuple[GPUNodeSnapshot, ...]:
        with self._lock:
            return tuple(self._nodes.values())

    def fleet_health_score(self) -> float:
        """Fraction of registered nodes currently healthy.

        Returns 1.0 when no nodes are registered yet (nothing to report as
        degraded) or when every registered node is healthy; degrades
        linearly as nodes report unhealthy.
        """
        with self._lock:
            nodes = list(self._nodes.values())
        if not nodes:
            score = 1.0
        else:
            healthy_count = sum(1 for n in nodes if n.healthy)
            score = healthy_count / len(nodes)
        set_fleet_health_score(score)
        return score

    def is_degraded(self) -> bool:
        """True once the fleet health score drops below 0.8 (CRITICAL alert threshold)."""
        return self.fleet_health_score() < 0.8

    def is_severely_degraded(self) -> bool:
        """True once the fleet health score drops below 0.5 (page on-call threshold)."""
        return self.fleet_health_score() < 0.5


__all__ = ["GPUFleetHealthMonitor", "GPUNodeSnapshot"]
