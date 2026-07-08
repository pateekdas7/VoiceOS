"""Prometheus metrics for the STT service.

Architecture: V1 Ch8; V7 Ch7 (Observability).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

stt_latency_ms = Histogram(
    "voiceos_stt_latency_ms",
    "STT transcription latency in milliseconds (from first audio frame to last WordHypothesis)",
    buckets=[50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000],
)

stt_first_word_latency_ms = Histogram(
    "voiceos_stt_first_word_latency_ms",
    "Latency to first WordHypothesis in milliseconds (streaming TTFW)",
    buckets=[50, 100, 150, 200, 300, 500, 750, 1000],
)

word_error_rate_gauge = Gauge(
    "voiceos_stt_word_error_rate",
    "Estimated word error rate (0.0-1.0) from confidence scores",
)

gpu_allocation_time_ms = Histogram(
    "voiceos_stt_gpu_allocation_time_ms",
    "Time to acquire GPU Scheduler VRAM allocation for STT inference",
    buckets=[1, 5, 10, 20, 50, 100, 200],
)

stt_requests_total = Counter(
    "voiceos_stt_requests_total",
    "Total STT transcription requests",
    ["status"],  # 'success' | 'rejected' | 'error'
)
