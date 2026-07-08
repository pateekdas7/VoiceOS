"""Prometheus metrics for the Audio Preprocessing Pipeline.

Exposes the following metrics per V1 Ch5 (Audio Preprocessing — observability):

    voiceos_pp_latency_ms        Gauge   (label: call_id)
        Most recent preprocessing latency in milliseconds for the given call.

    voiceos_pp_erle_db           Gauge   (label: call_id)
        Echo Return Loss Enhancement in dB for the given call.

    voiceos_pp_snr_db            Gauge   (label: call_id)
        Estimated Signal-to-Noise Ratio in dB for the given call.

    voiceos_pp_frames_total      Counter (label: call_id)
        Total frames processed by the preprocessing pipeline for the call.

    voiceos_pp_active_pipelines  Gauge   (no label)
        Number of pipelines currently instantiated (one per active call).

Architecture: V1 Ch5 (observability); DocSuite-08 (metrics naming conventions).
"""

from __future__ import annotations

import prometheus_client as prom

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

LATENCY_MS: prom.Gauge = prom.Gauge(
    "voiceos_pp_latency_ms",
    "Most recent preprocessing pipeline latency (ms) per call",
    ["call_id"],
)

ERLE_DB: prom.Gauge = prom.Gauge(
    "voiceos_pp_erle_db",
    "Echo Return Loss Enhancement (dB) per call",
    ["call_id"],
)

SNR_DB: prom.Gauge = prom.Gauge(
    "voiceos_pp_snr_db",
    "Estimated Signal-to-Noise Ratio (dB) per call",
    ["call_id"],
)

FRAMES_TOTAL: prom.Counter = prom.Counter(
    "voiceos_pp_frames_total",
    "Total audio frames processed by the preprocessing pipeline per call",
    ["call_id"],
)

ACTIVE_PIPELINES: prom.Gauge = prom.Gauge(
    "voiceos_pp_active_pipelines",
    "Number of active AudioPreprocessorService instances",
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def update_pipeline_metrics(call_id: str, latency_ms: float, erle_db: float, snr_db: float) -> None:
    """Update per-call latency, ERLE, and SNR gauges.

    Args:
        call_id:    Call identifier used as Prometheus label.
        latency_ms: Most recent frame processing latency in milliseconds.
        erle_db:    Current ERLE estimate in dB (0.0 when AEC disabled).
        snr_db:     Current SNR estimate in dB (0.0 when not measured).
    """
    LATENCY_MS.labels(call_id=call_id).set(latency_ms)
    ERLE_DB.labels(call_id=call_id).set(erle_db)
    SNR_DB.labels(call_id=call_id).set(snr_db)


def increment_frames(call_id: str) -> None:
    """Increment the frame counter for a call.

    Args:
        call_id: Call identifier used as Prometheus label.
    """
    FRAMES_TOTAL.labels(call_id=call_id).inc()


def set_active_pipelines(count: int) -> None:
    """Set the active-pipelines gauge.

    Args:
        count: Current number of active AudioPreprocessorService instances.
    """
    ACTIVE_PIPELINES.set(count)
