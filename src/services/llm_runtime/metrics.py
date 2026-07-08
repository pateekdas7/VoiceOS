"""Prometheus metrics for the LLM runtime service.

Architecture: V1 Ch13; V7 Ch7 (Observability).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

llm_ttft_ms = Histogram(
    "voiceos_llm_ttft_ms",
    "LLM time-to-first-token in milliseconds",
    buckets=[50, 100, 200, 300, 500, 750, 1000, 1500, 2000],
)

llm_tokens_per_second = Histogram(
    "voiceos_llm_tokens_per_second",
    "LLM generation throughput in tokens per second",
    buckets=[5, 10, 20, 30, 50, 75, 100, 150, 200],
)

llm_completion_latency_ms = Histogram(
    "voiceos_llm_completion_latency_ms",
    "Total LLM generation latency from first prompt token to stop token",
    buckets=[200, 500, 1000, 1500, 2000, 3000, 5000],
)

llm_requests_total = Counter(
    "voiceos_llm_requests_total",
    "Total LLM generation requests",
    ["status"],  # 'success' | 'rejected' | 'error' | 'ri7_violation'
)

llm_prompt_hash_validations_total = Counter(
    "voiceos_llm_prompt_hash_validations_total",
    "Total RI-7 prompt hash validation calls",
    ["outcome"],  # 'pass' | 'fail'
)
