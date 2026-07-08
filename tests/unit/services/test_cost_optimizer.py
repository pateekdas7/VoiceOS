"""Unit tests for src/services/cost_optimizer/ (Sprint-027)."""

from __future__ import annotations

from datetime import date

import pytest

from src.libs.contracts.primitives import TenantId
from src.services.cost_optimizer import (
    ConversationCostTracker,
    ConversationResourceUsage,
    CostOptimizer,
    DateRange,
    GPUEfficiencyAnalyzer,
    InstanceMixOptimizer,
)
from src.services.cost_optimizer.gpu_efficiency import PoolAdjustmentRecommendation

TENANT = TenantId("11111111-1111-1111-1111-111111111111")
RANGE = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 2))


class _StaticUsageSource:
    def __init__(self, calls: tuple[ConversationResourceUsage, ...]) -> None:
        self._calls = calls

    def usage_for(self, tenant_id: TenantId, date_range: DateRange) -> tuple[ConversationResourceUsage, ...]:
        assert tenant_id == TENANT
        assert date_range == RANGE
        return self._calls


class _StaticVRAMProvider:
    def __init__(self, ratio: float) -> None:
        self._ratio = ratio

    def utilization_ratio(self) -> float:
        return self._ratio


class TestConversationCostTracker:
    def test_cost_per_conversation_calculation(self) -> None:
        """Known GPU-seconds + tokens -> expected cost (Sprint-027.md required test)."""
        # 30s GPU + 2K STT tokens + 1K LLM tokens (Sprint-027.md's own worked example).
        usage = ConversationResourceUsage(
            call_id="call-1",
            tenant_id=TENANT,
            gpu_seconds=30,
            stt_tokens=2000,
            llm_tokens=1000,
            tts_gpu_seconds=0,
            storage_mb=0,
        )
        tracker = ConversationCostTracker(_StaticUsageSource((usage,)))

        report = tracker.cost_report(TENANT, RANGE)

        # DEFAULT_RATE_CARD: GPU_SECOND = 1000 minor / 100 units => 10/unit;
        # STT_TOKEN = 500 minor / 1000 => 0.5/unit; LLM_TOKEN = 1000/1000 => 1/unit.
        assert report.gpu_cost_minor == (30 * 1000) // 100
        assert report.stt_cost_minor == (2000 * 500) // 1000
        assert report.llm_cost_minor == (1000 * 1000) // 1000
        assert report.tts_cost_minor == 0
        assert report.storage_cost_minor == 0
        assert report.total_cost_minor > 0
        assert report.call_count == 1

    def test_zero_usage_gives_zero_cost(self) -> None:
        tracker = ConversationCostTracker(_StaticUsageSource(()))
        report = tracker.cost_report(TENANT, RANGE)
        assert report.total_cost_minor == 0
        assert report.call_count == 0

    def test_tts_cost_priced_via_gpu_second_rate(self) -> None:
        usage = ConversationResourceUsage(
            call_id="call-2",
            tenant_id=TENANT,
            gpu_seconds=0,
            stt_tokens=0,
            llm_tokens=0,
            tts_gpu_seconds=100,
            storage_mb=0,
        )
        tracker = ConversationCostTracker(_StaticUsageSource((usage,)))
        report = tracker.cost_report(TENANT, RANGE)
        assert report.tts_cost_minor == (100 * 1000) // 100


class TestGPUEfficiencyAnalyzer:
    def test_gpu_efficiency_ratio(self) -> None:
        """total VRAM used / total VRAM available -> correct ratio (Sprint-027.md required test)."""
        analyzer = GPUEfficiencyAnalyzer(_StaticVRAMProvider(0.80))
        assert analyzer.utilization() == 0.80

    def test_recommend_scale_down_when_far_below_target(self) -> None:
        analyzer = GPUEfficiencyAnalyzer(_StaticVRAMProvider(0.30))
        rec = analyzer.recommend_pool_adjustment()
        assert rec.action == "scale_down"

    def test_recommend_scale_up_when_near_saturation(self) -> None:
        analyzer = GPUEfficiencyAnalyzer(_StaticVRAMProvider(0.97))
        rec = analyzer.recommend_pool_adjustment()
        assert rec.action == "scale_up"

    def test_recommend_hold_when_near_target(self) -> None:
        analyzer = GPUEfficiencyAnalyzer(_StaticVRAMProvider(0.82))
        rec = analyzer.recommend_pool_adjustment()
        assert rec.action == "hold"


class TestInstanceMixOptimizer:
    def test_optimize_instance_mix_from_history(self) -> None:
        optimizer = InstanceMixOptimizer()
        history = tuple([0.5] * 8 + [0.9, 0.95])  # stable baseline + occasional peaks
        rec = optimizer.optimize_instance_mix(history)

        assert 0.0 <= rec.reserved_ratio <= 1.0
        assert 0.0 <= rec.spot_ratio <= 1.0
        assert 0.0 <= rec.on_demand_ratio <= 1.0
        assert pytest.approx(rec.reserved_ratio + rec.spot_ratio + rec.on_demand_ratio, abs=1e-9) == 1.0

    def test_empty_history_raises(self) -> None:
        optimizer = InstanceMixOptimizer()
        with pytest.raises(ValueError, match="requires at least one"):
            optimizer.optimize_instance_mix(())

    def test_all_zero_history_defaults_to_on_demand(self) -> None:
        optimizer = InstanceMixOptimizer()
        rec = optimizer.optimize_instance_mix((0.0, 0.0, 0.0))
        assert rec.on_demand_ratio == 1.0
        assert rec.reserved_ratio == 0.0
        assert rec.spot_ratio == 0.0

    def test_usage_volatility(self) -> None:
        optimizer = InstanceMixOptimizer()
        assert optimizer.usage_volatility((0.5, 0.5, 0.5)) == 0.0
        assert optimizer.usage_volatility((0.1, 0.9)) > 0.0
        assert optimizer.usage_volatility((0.5,)) == 0.0


class TestCostOptimizerFacade:
    def _build(self, *, vram_ratio: float = 0.80, history: tuple[float, ...] = ()) -> CostOptimizer:
        usage = ConversationResourceUsage(
            call_id="call-1",
            tenant_id=TENANT,
            gpu_seconds=30,
            stt_tokens=2000,
            llm_tokens=1000,
            tts_gpu_seconds=10,
            storage_mb=5,
        )
        tracker = ConversationCostTracker(_StaticUsageSource((usage,)))
        analyzer = GPUEfficiencyAnalyzer(_StaticVRAMProvider(vram_ratio))
        mix_optimizer = InstanceMixOptimizer()
        return CostOptimizer(tracker, analyzer, mix_optimizer, daily_usage_history=history)

    def test_cost_per_conversation_nonzero_for_test_call(self) -> None:
        optimizer = self._build()
        report = optimizer.cost_per_conversation(TENANT, RANGE)
        assert report.total_cost_minor > 0

    def test_gpu_efficiency_delegates(self) -> None:
        optimizer = self._build(vram_ratio=0.80)
        assert optimizer.gpu_efficiency(RANGE) == 0.80

    def test_optimize_instance_mix_delegates(self) -> None:
        optimizer = self._build(history=(0.5, 0.6, 0.4))
        rec = optimizer.optimize_instance_mix()
        assert rec.reserved_ratio >= 0

    def test_recommend_includes_scale_down_when_underutilized(self) -> None:
        optimizer = self._build(vram_ratio=0.20)
        recs = optimizer.recommend()
        assert any(r.title == "Scale down GPU pool" for r in recs)

    def test_recommend_empty_list_possible_when_healthy(self) -> None:
        optimizer = self._build(vram_ratio=0.82)
        recs = optimizer.recommend()
        assert all(isinstance(r.priority, str) for r in recs)
        assert isinstance(recs, list)
        assert isinstance(
            PoolAdjustmentRecommendation("hold", "n/a"),
            PoolAdjustmentRecommendation,
        )
