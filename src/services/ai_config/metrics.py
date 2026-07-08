"""Prometheus metrics for the AI Configuration Platform (V5 Ch14, Sprint-025)."""

from __future__ import annotations

from prometheus_client import Counter

PROMPT_VERSIONS_PUBLISHED_TOTAL: Counter = Counter(
    "voiceos_ai_config_prompt_versions_published_total",
    "Total prompt versions published, by tenant.",
    labelnames=["tenant_id"],
)

MODEL_CONFIG_RESOLUTIONS_TOTAL: Counter = Counter(
    "voiceos_ai_config_model_config_resolutions_total",
    "Total ModelConfigService.resolve() calls, by cache outcome.",
    labelnames=["cache_hit"],
)


def record_prompt_version_published(tenant_id: str) -> None:
    PROMPT_VERSIONS_PUBLISHED_TOTAL.labels(tenant_id=tenant_id).inc()


def record_model_config_resolution(cache_hit: bool) -> None:
    MODEL_CONFIG_RESOLUTIONS_TOTAL.labels(cache_hit=str(cache_hit)).inc()
