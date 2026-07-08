"""Prometheus metrics for the VAD & Endpointing service.

Exposes the following metrics per V1 Ch6 (observability):

    voiceos_vad_speech_ratio        Gauge   (label: call_id)
        Fraction of VAD windows classified as speech in the current call.

    voiceos_vad_endpoint_latency_ms Gauge   (label: call_id)
        Most recent endpoint-detection latency in milliseconds (time from
        speech end to VADSpeechEnd event emission).

    voiceos_vad_bargein_total       Counter (label: call_id)
        Total barge-in events emitted for the call.

    voiceos_vad_backchannel_total   Counter (label: call_id)
        Total backchannel events emitted for the call (backchannels suppressed
        from full barge-in).

Architecture: V1 Ch6 (observability); DocSuite-08 (metrics naming conventions).
"""

from __future__ import annotations

import prometheus_client as prom

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

SPEECH_RATIO: prom.Gauge = prom.Gauge(
    "voiceos_vad_speech_ratio",
    "Fraction of VAD windows classified as speech for the given call",
    ["call_id"],
)

ENDPOINT_LATENCY_MS: prom.Gauge = prom.Gauge(
    "voiceos_vad_endpoint_latency_ms",
    "Most recent endpoint-detection latency (ms) per call",
    ["call_id"],
)

BARGEIN_TOTAL: prom.Counter = prom.Counter(
    "voiceos_vad_bargein_total",
    "Total barge-in events emitted per call",
    ["call_id"],
)

BACKCHANNEL_TOTAL: prom.Counter = prom.Counter(
    "voiceos_vad_backchannel_total",
    "Total backchannel events emitted per call (barge-in suppressed)",
    ["call_id"],
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def update_speech_ratio(call_id: str, speech_frames: int, total_frames: int) -> None:
    """Update the speech ratio gauge for a call.

    Args:
        call_id:       Call identifier used as Prometheus label.
        speech_frames: Number of frames classified as speech.
        total_frames:  Total frames processed (must be > 0).
    """
    if total_frames > 0:
        SPEECH_RATIO.labels(call_id=call_id).set(speech_frames / total_frames)


def update_endpoint_latency(call_id: str, latency_ms: float) -> None:
    """Update the endpoint detection latency gauge.

    Args:
        call_id:    Call identifier used as Prometheus label.
        latency_ms: Latency in milliseconds from speech end to event emission.
    """
    ENDPOINT_LATENCY_MS.labels(call_id=call_id).set(latency_ms)


def increment_bargein(call_id: str) -> None:
    """Increment the barge-in counter for a call.

    Args:
        call_id: Call identifier used as Prometheus label.
    """
    BARGEIN_TOTAL.labels(call_id=call_id).inc()


def increment_backchannel(call_id: str) -> None:
    """Increment the backchannel counter for a call.

    Args:
        call_id: Call identifier used as Prometheus label.
    """
    BACKCHANNEL_TOTAL.labels(call_id=call_id).inc()
