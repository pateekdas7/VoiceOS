"""Default MetricCheckSpecs (ADR-006 Sec 3.4/9 Phase 5) -- real signals, real PromQL.

Every query here targets a metric or recording rule this repo already
emits (``monitoring/prometheus/recording_rules.yml``, and the per-adapter
histograms in ``src/services/{stt,tts,llm_runtime}/metrics.py`` /
``monitoring/gpu_fleet/metrics.py``) -- no new instrumentation is
introduced by this module, satisfying ADR-006 Sec 3.2.1's single-source-
of-truth rule. The 5m-vs-6h window comparison reuses the SAME multi-window
recording rules Vol.3 Ch.15's SLO burn-rate alerts already compute.
"""

from __future__ import annotations

from src.services.ops_intelligence.models import InsightCategory
from src.services.ops_intelligence.reasoning.evidence_bundler import MetricCheckSpec

DEFAULT_METRIC_CHECK_SPECS: tuple[MetricCheckSpec, ...] = (
    MetricCheckSpec(
        name="First-audio latency p95 (Vol.1 Ch.23 SLO)",
        query="voiceos:first_audio_seconds:p95_5m",
        baseline_query="voiceos:first_audio_seconds:p95_6h",
        comparison="higher_is_worse",
        affected_component="conversation_engine",
        category=InsightCategory.REGRESSION,
    ),
    MetricCheckSpec(
        name="Platform availability",
        query="voiceos:availability:ratio_5m",
        baseline_query="voiceos:availability:ratio_6h",
        comparison="lower_is_worse",
        affected_component="platform",
        category=InsightCategory.ANOMALY,
    ),
    MetricCheckSpec(
        name="STT word error rate",
        query="avg_over_time(voiceos_stt_word_error_rate[5m])",
        baseline_query="avg_over_time(voiceos_stt_word_error_rate[6h])",
        comparison="higher_is_worse",
        affected_component="stt",
        category=InsightCategory.DOMAIN_QUALITY,
    ),
    MetricCheckSpec(
        name="TTS first-clause latency (ADR-004 750ms budget)",
        query="histogram_quantile(0.95, rate(voiceos_tts_first_clause_latency_ms_bucket[5m]))",
        baseline_query="histogram_quantile(0.95, rate(voiceos_tts_first_clause_latency_ms_bucket[6h]))",
        comparison="higher_is_worse",
        affected_component="tts",
        category=InsightCategory.DOMAIN_QUALITY,
    ),
    MetricCheckSpec(
        name="LLM time-to-first-token",
        query="histogram_quantile(0.95, rate(voiceos_llm_ttft_ms_bucket[5m]))",
        baseline_query="histogram_quantile(0.95, rate(voiceos_llm_ttft_ms_bucket[6h]))",
        comparison="higher_is_worse",
        affected_component="llm_runtime",
        category=InsightCategory.DOMAIN_QUALITY,
    ),
    MetricCheckSpec(
        name="GPU fleet health score",
        query="voiceos_gpu_fleet_health_score",
        baseline_query="avg_over_time(voiceos_gpu_fleet_health_score[6h])",
        comparison="lower_is_worse",
        affected_component="gpu_fleet",
        category=InsightCategory.ANOMALY,
    ),
)

__all__ = ["DEFAULT_METRIC_CHECK_SPECS"]
