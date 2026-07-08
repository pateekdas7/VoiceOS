"""InstanceMixOptimizer -- spot/reserved/on-demand ratio recommendations (V7 Ch15).

Heuristic (documented proxy, same "concrete rule this sprint picks, not
sourced from the architecture volumes" precedent as Sprint-024's
``ForecastingEngine``/``RateCard``): given a history of daily GPU-second
usage, the *baseline* (a low percentile of the history -- capacity that is
essentially always needed) should be covered by **reserved** capacity
(cheapest for guaranteed load), the *volatile excess* above baseline by
**spot** (cheapest for interruptible load), and a small **on-demand**
margin covers the gap between spot availability and true peak (spot
capacity is never guaranteed).

Architecture: V7 Ch15 (Cost Optimization).
"""

from __future__ import annotations

import statistics

from .models import InstanceMixRecommendation

BASELINE_PERCENTILE = 0.20
"""Reserved capacity covers usage at or below this percentile of history."""

ON_DEMAND_MARGIN = 0.10
"""Fraction of the volatile (above-baseline) range reserved for on-demand, as a safety margin above spot."""


class InstanceMixOptimizer:
    """Recommends a spot/reserved/on-demand VRAM-hour purchase ratio from historical usage."""

    def optimize_instance_mix(self, daily_usage_ratios: tuple[float, ...]) -> InstanceMixRecommendation:
        """Compute a recommended ratio from a history of daily utilization ratios (0.0-1.0 each).

        Raises ``ValueError`` if given no history -- there is nothing to
        recommend a mix from.
        """
        if not daily_usage_ratios:
            raise ValueError("optimize_instance_mix() requires at least one historical data point")

        sorted_usage = sorted(daily_usage_ratios)
        baseline_index = max(0, int(len(sorted_usage) * BASELINE_PERCENTILE) - 1)
        baseline = sorted_usage[baseline_index]
        peak = max(sorted_usage)

        if peak <= 0:
            return InstanceMixRecommendation(
                spot_ratio=0.0,
                reserved_ratio=0.0,
                on_demand_ratio=1.0,
                rationale="No historical usage recorded -- default to on-demand until a usage pattern is established.",
            )

        reserved_ratio = min(1.0, baseline / peak)
        volatile_ratio = max(0.0, 1.0 - reserved_ratio)
        on_demand_ratio = min(volatile_ratio, ON_DEMAND_MARGIN)
        spot_ratio = max(0.0, volatile_ratio - on_demand_ratio)

        rationale = (
            f"Baseline (p{int(BASELINE_PERCENTILE * 100)}) utilization {baseline:.0%} of peak {peak:.0%} covered by "
            f"reserved ({reserved_ratio:.0%}); volatile excess covered by spot ({spot_ratio:.0%}) with a "
            f"{on_demand_ratio:.0%} on-demand safety margin for spot-unavailability gaps."
        )
        return InstanceMixRecommendation(
            spot_ratio=spot_ratio,
            reserved_ratio=reserved_ratio,
            on_demand_ratio=on_demand_ratio,
            rationale=rationale,
        )

    def usage_volatility(self, daily_usage_ratios: tuple[float, ...]) -> float:
        """Standard deviation of daily utilization -- higher means spot capacity is riskier to rely on."""
        if len(daily_usage_ratios) < 2:
            return 0.0
        return statistics.stdev(daily_usage_ratios)


__all__ = ["BASELINE_PERCENTILE", "ON_DEMAND_MARGIN", "InstanceMixOptimizer"]
