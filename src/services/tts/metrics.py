"""Prometheus metrics for the TTS service.

Architecture: V1 Ch17; V7 Ch7 (Observability).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

tts_first_clause_latency_ms = Histogram(
    "voiceos_tts_first_clause_latency_ms",
    "Latency to first synthesised AudioClause in milliseconds",
    buckets=[50, 100, 150, 200, 300, 500, 750, 1000],
)

tts_full_synthesis_latency_ms = Histogram(
    "voiceos_tts_full_synthesis_latency_ms",
    "Total synthesis latency from first text chunk to last AudioClause",
    buckets=[200, 500, 1000, 2000, 5000, 10000, 20000, 30000],
)

tts_clause_latency_ms = Histogram(
    "voiceos_tts_clause_latency_ms",
    "Per-clause synthesis latency in milliseconds",
    buckets=[50, 100, 200, 300, 500, 750, 1000, 1500],
)

tts_requests_total = Counter(
    "voiceos_tts_requests_total",
    "Total TTS synthesis requests",
    ["status"],  # 'success' | 'rejected' | 'error'
)

tts_clauses_total = Counter(
    "voiceos_tts_clauses_total",
    "Total clauses synthesised",
)

gpu_allocation_time_ms = Histogram(
    "voiceos_tts_gpu_allocation_time_ms",
    "Time to acquire GPU Scheduler VRAM allocation for TTS inference",
    buckets=[1, 5, 10, 20, 50, 100, 200],
)

tts_script_conversion_latency_ms = Histogram(
    "voiceos_tts_script_conversion_latency_ms",
    "Hindi Devanagari script conversion stage setup latency in milliseconds",
    buckets=[0.1, 0.5, 1, 2, 5, 10, 20],
)
