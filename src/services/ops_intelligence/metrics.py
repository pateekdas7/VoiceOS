"""Prometheus metrics for ops_intelligence's top-level facade (package root, outside reasoning/).

Deliberately NOT under ``reasoning/`` -- Rule 7 (``scripts/check_boundaries.py``)
bans telemetry-emission imports there. This is the one legitimate exception
the ADR anticipates (Sec 13.10): platform-wide (non-tenant-billable)
reasoning-model token spend still needs to be visible somewhere, so it's
recorded here and surfaced on the existing cost dashboard alongside
GPU/STT/LLM/TTS costs, instead of going untracked.
"""

from __future__ import annotations

from prometheus_client import Counter

platform_wide_analysis_tokens_total = Counter(
    "voiceos_ops_intelligence_platform_wide_tokens_total",
    "Reasoning-model tokens consumed by platform-wide (non-tenant-billable) analysis runs.",
)


class PrometheusCostObservability:
    """Concrete ``CostObservabilityPort`` (see reasoning/insight_service.py) backed by prometheus_client."""

    def record_platform_wide_tokens(self, token_count: int) -> None:
        platform_wide_analysis_tokens_total.inc(token_count)


__all__ = ["PrometheusCostObservability", "platform_wide_analysis_tokens_total"]
