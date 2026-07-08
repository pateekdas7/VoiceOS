"""Prometheus metrics for fleet-level GPU operational tooling (Sprint-027).

Distinct from ``src.services.gpu_scheduler.metrics`` (per-device VRAM
ledger metrics, ``device_id``-labeled, single node) -- these are
fleet-wide aggregates across every GPU node, consumed by
``monitoring/grafana/dashboards/gpu-fleet.json`` and the
``GPUFleetDegraded``/``GPUFleetSevereDegradation`` alerts in
``monitoring/prometheus/alert_rules/infrastructure.yml``.

Architecture: V7 Ch6 (GPU Fleet Management).
"""

from __future__ import annotations

import prometheus_client as prom

FLEET_HEALTH_SCORE: prom.Gauge = prom.Gauge(
    "voiceos_gpu_fleet_health_score",
    "Fleet-level GPU health score: 1.0 = all nodes healthy, < 0.5 = majority down.",
)

NODE_HEALTHY: prom.Gauge = prom.Gauge(
    "voiceos_gpu_fleet_node_healthy",
    "Per-node health flag (1 = healthy, 0 = unhealthy).",
    ["node_id"],
)

FLEET_VRAM_USED_MB: prom.Gauge = prom.Gauge(
    "voiceos_gpu_fleet_vram_used_mb",
    "Aggregate VRAM used across the entire GPU fleet (MB).",
)

FLEET_VRAM_BUDGET_MB: prom.Gauge = prom.Gauge(
    "voiceos_gpu_fleet_vram_budget_mb",
    "Aggregate VRAM budget (total capacity) across the entire GPU fleet (MB).",
)

MODEL_POOL_OCCUPANCY_RATIO: prom.Gauge = prom.Gauge(
    "voiceos_gpu_model_pool_occupancy_ratio",
    "Fraction of fleet nodes with a given model pool currently warm.",
    ["pool"],
)


def set_fleet_health_score(score: float) -> None:
    FLEET_HEALTH_SCORE.set(score)


def set_node_healthy(node_id: str, healthy: bool) -> None:
    NODE_HEALTHY.labels(node_id=node_id).set(1 if healthy else 0)


def update_fleet_vram_gauges(used_mb: int, budget_mb: int) -> None:
    FLEET_VRAM_USED_MB.set(used_mb)
    FLEET_VRAM_BUDGET_MB.set(budget_mb)


def set_model_pool_occupancy(pool: str, ratio: float) -> None:
    MODEL_POOL_OCCUPANCY_RATIO.labels(pool=pool).set(ratio)
