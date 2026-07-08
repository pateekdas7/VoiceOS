"""Unit tests for monitoring/gpu_fleet/ (Sprint-027 GPU Fleet Management)."""

from __future__ import annotations

from monitoring.gpu_fleet.fleet_health import GPUFleetHealthMonitor, GPUNodeSnapshot
from monitoring.gpu_fleet.vram_budget import FleetVRAMBudget, NodeVRAMUsage
from monitoring.gpu_fleet.warmup import REQUIRED_MODEL_POOLS, ModelWarmupOrchestrator


class TestGPUFleetHealthMonitor:
    def test_fleet_health_score_full(self) -> None:
        """All 4 nodes healthy -> fleet_health_score() == 1.0 (Sprint-027.md AC)."""
        monitor = GPUFleetHealthMonitor()
        for i in range(4):
            monitor.report_node(
                GPUNodeSnapshot(node_id=f"gpu-{i}", healthy=True, vram_used_mb=1000, vram_total_mb=23034)
            )

        assert monitor.fleet_health_score() == 1.0
        assert not monitor.is_degraded()
        assert not monitor.is_severely_degraded()

    def test_fleet_health_score_degraded(self) -> None:
        """2/4 nodes down -> fleet_health_score() <= 0.5 (Sprint-027.md required test)."""
        monitor = GPUFleetHealthMonitor()
        for i in range(2):
            monitor.report_node(
                GPUNodeSnapshot(node_id=f"gpu-{i}", healthy=True, vram_used_mb=1000, vram_total_mb=23034)
            )
        for i in range(2, 4):
            monitor.report_node(GPUNodeSnapshot(node_id=f"gpu-{i}", healthy=False, vram_used_mb=0, vram_total_mb=23034))

        score = monitor.fleet_health_score()
        assert score <= 0.5
        assert monitor.is_degraded()

    def test_fleet_health_score_no_nodes_registered(self) -> None:
        """An empty fleet reports 1.0 -- nothing registered to be degraded."""
        monitor = GPUFleetHealthMonitor()
        assert monitor.fleet_health_score() == 1.0

    def test_severely_degraded_below_half(self) -> None:
        monitor = GPUFleetHealthMonitor()
        monitor.report_node(GPUNodeSnapshot(node_id="gpu-0", healthy=True, vram_used_mb=1000, vram_total_mb=23034))
        for i in range(1, 4):
            monitor.report_node(GPUNodeSnapshot(node_id=f"gpu-{i}", healthy=False, vram_used_mb=0, vram_total_mb=23034))

        assert monitor.fleet_health_score() == 0.25
        assert monitor.is_severely_degraded()

    def test_remove_node_updates_score(self) -> None:
        monitor = GPUFleetHealthMonitor()
        monitor.report_node(GPUNodeSnapshot(node_id="gpu-0", healthy=True, vram_used_mb=0, vram_total_mb=23034))
        monitor.report_node(GPUNodeSnapshot(node_id="gpu-1", healthy=False, vram_used_mb=0, vram_total_mb=23034))
        assert monitor.fleet_health_score() == 0.5

        monitor.remove_node("gpu-1")
        assert monitor.fleet_health_score() == 1.0
        assert len(monitor.node_snapshots()) == 1


class TestFleetVRAMBudget:
    def test_utilization_ratio(self) -> None:
        budget = FleetVRAMBudget()
        budget.record_node_usage(NodeVRAMUsage(node_id="gpu-0", used_mb=18427, total_mb=23034))  # 80%
        assert budget.utilization_ratio() == 18427 / 23034

    def test_aggregate_across_nodes(self) -> None:
        budget = FleetVRAMBudget()
        budget.record_node_usage(NodeVRAMUsage(node_id="gpu-0", used_mb=10000, total_mb=23034))
        budget.record_node_usage(NodeVRAMUsage(node_id="gpu-1", used_mb=5000, total_mb=23034))

        assert budget.total_used_mb() == 15000
        assert budget.total_budget_mb() == 46068
        assert budget.headroom_mb() == 46068 - 15000

    def test_empty_fleet_ratio_is_zero_not_error(self) -> None:
        budget = FleetVRAMBudget()
        assert budget.utilization_ratio() == 0.0
        assert budget.headroom_mb() == 0


class TestModelWarmupOrchestrator:
    def test_node_admitted_only_after_all_pools_warm(self) -> None:
        admitted: list[str] = []
        orchestrator = ModelWarmupOrchestrator(
            warmup_probe=lambda node_id, pool: True,
            on_node_admitted=admitted.append,
        )

        result = orchestrator.on_node_join("gpu-new")

        assert result is True
        assert admitted == ["gpu-new"]
        assert orchestrator.is_node_warm("gpu-new")

    def test_node_not_admitted_if_any_pool_fails_to_warm(self) -> None:
        admitted: list[str] = []

        def flaky_probe(node_id: str, pool: str) -> bool:
            return pool != "tts"  # TTS never warms

        orchestrator = ModelWarmupOrchestrator(warmup_probe=flaky_probe, on_node_admitted=admitted.append)

        result = orchestrator.on_node_join("gpu-new")

        assert result is False
        assert admitted == []
        assert not orchestrator.is_node_warm("gpu-new")

    def test_all_three_required_pools(self) -> None:
        assert set(REQUIRED_MODEL_POOLS) == {"stt", "llm", "tts"}

    def test_is_node_warm_false_before_join(self) -> None:
        orchestrator = ModelWarmupOrchestrator(warmup_probe=lambda node_id, pool: True)
        assert not orchestrator.is_node_warm("never-joined")
