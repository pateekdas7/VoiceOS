"""CostOptimizer -- façade over ConversationCostTracker/GPUEfficiencyAnalyzer/
InstanceMixOptimizer (V7 Ch15, Sprint-027).

Architecture: V7 Ch15 (Cost Optimization Service).
"""

from __future__ import annotations

from src.libs.contracts.primitives import TenantId

from .gpu_efficiency import GPUEfficiencyAnalyzer, PoolAdjustmentRecommendation
from .instance_mix import InstanceMixOptimizer
from .models import CostRecommendation, CostReport, DateRange, InstanceMixRecommendation
from .tracker import ConversationCostTracker

HIGH_TTS_COST_SHARE_THRESHOLD = 0.40
"""If TTS cost exceeds this share of total cost, flag it as an optimization target
(TTS is GPU-bound and the most latency-sensitive stage, TT-001-residual -- a
disproportionate cost share is a signal worth surfacing, not just tolerating)."""


class CostOptimizer:
    """Cost-per-conversation tracking, GPU efficiency, and instance-mix optimization."""

    def __init__(
        self,
        tracker: ConversationCostTracker,
        efficiency_analyzer: GPUEfficiencyAnalyzer,
        instance_mix_optimizer: InstanceMixOptimizer,
        *,
        daily_usage_history: tuple[float, ...] = (),
    ) -> None:
        self._tracker = tracker
        self._efficiency_analyzer = efficiency_analyzer
        self._instance_mix_optimizer = instance_mix_optimizer
        self._daily_usage_history = daily_usage_history

    def cost_per_conversation(self, tenant_id: TenantId, date_range: DateRange) -> CostReport:
        """Per-call cost breakdown (GPU, STT, LLM, TTS, storage) for ``tenant_id`` over ``date_range``."""
        return self._tracker.cost_report(tenant_id, date_range)

    def gpu_efficiency(self, date_range: DateRange) -> float:
        """Fleet-level GPU utilization ratio (target >= 0.80).

        ``date_range`` documents the intended query window; the injected
        ``GPUEfficiencyAnalyzer``'s provider is expected to already scope
        its own data to it (e.g. a Prometheus range query averaged over
        ``date_range`` in a real deployment).
        """
        del date_range
        return self._efficiency_analyzer.utilization()

    def pool_adjustment_recommendation(self) -> PoolAdjustmentRecommendation:
        return self._efficiency_analyzer.recommend_pool_adjustment()

    def optimize_instance_mix(self) -> InstanceMixRecommendation:
        """Recommended spot/reserved/on-demand ratio based on this fleet's historical usage pattern."""
        return self._instance_mix_optimizer.optimize_instance_mix(self._daily_usage_history)

    def recommend(self) -> list[CostRecommendation]:
        """Ranked list of actionable savings recommendations, most-impactful first."""
        recommendations: list[CostRecommendation] = []

        pool_rec = self.pool_adjustment_recommendation()
        if pool_rec.action == "scale_down":
            recommendations.append(
                CostRecommendation(
                    title="Scale down GPU pool",
                    estimated_savings_minor=0,  # actual figure requires per-node hourly cost, not available in this fixture-driven pass
                    rationale=pool_rec.rationale,
                    priority="high",
                )
            )
        elif pool_rec.action == "scale_up":
            recommendations.append(
                CostRecommendation(
                    title="Scale up GPU pool before saturation",
                    estimated_savings_minor=0,
                    rationale=pool_rec.rationale,
                    priority="high",
                )
            )

        if self._daily_usage_history:
            mix = self.optimize_instance_mix()
            if mix.spot_ratio > 0 or mix.reserved_ratio < 0.5:
                recommendations.append(
                    CostRecommendation(
                        title="Adjust spot/reserved/on-demand instance mix",
                        estimated_savings_minor=0,
                        rationale=mix.rationale,
                        priority="medium",
                    )
                )

        return recommendations


__all__ = ["HIGH_TTS_COST_SHARE_THRESHOLD", "CostOptimizer"]
