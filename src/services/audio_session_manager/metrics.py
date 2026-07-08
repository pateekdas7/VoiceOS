"""Prometheus metrics for the Audio Session Manager.

Exposes per-session and aggregate gauges consumed by operations dashboards
and alerting rules (V7 Ch4 Monitoring & Alerting).

Metrics:
  voiceos_asm_jitter_ms          (Gauge, label: call_id)
      Adaptive jitter buffer delay estimate.  High values indicate network
      instability requiring PLC or session degradation handling.

  voiceos_asm_loss_rate          (Gauge, label: call_id)
      Fraction of frames that required PLC replacement in the last window.
      Values > 0.05 indicate significant packet loss.

  voiceos_asm_buffer_depth       (Gauge, label: call_id)
      Current jitter buffer depth in frames.  Approaching max_depth triggers
      the RI-3 overflow drop path.

  voiceos_asm_active_sessions    (Gauge, no label)
      Total number of sessions currently in ACTIVE or BARGE_IN state.

Architecture: V1 Ch4 (ASM outputs); V3 Ch17 (Observability); V7 Ch4.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

# ---------------------------------------------------------------------------
# Per-session gauges
# ---------------------------------------------------------------------------

JITTER_MS: Gauge = Gauge(
    "voiceos_asm_jitter_ms",
    "Adaptive jitter buffer delay estimate in milliseconds.",
    labelnames=["call_id"],
)
"""Gauge: current jitter estimate per call."""

LOSS_RATE: Gauge = Gauge(
    "voiceos_asm_loss_rate",
    "Packet loss rate for the audio session (fraction of frames requiring PLC).",
    labelnames=["call_id"],
)
"""Gauge: fraction of frames replaced by PLC per call."""

BUFFER_DEPTH: Gauge = Gauge(
    "voiceos_asm_buffer_depth",
    "Current jitter buffer depth in frames.",
    labelnames=["call_id"],
)
"""Gauge: buffer occupancy per call."""

# ---------------------------------------------------------------------------
# Aggregate counters / gauges
# ---------------------------------------------------------------------------

ACTIVE_SESSIONS: Gauge = Gauge(
    "voiceos_asm_active_sessions",
    "Total number of audio sessions in ACTIVE or BARGE_IN state.",
)
"""Gauge: aggregate active session count."""

PLC_FRAMES_TOTAL: Counter = Counter(
    "voiceos_asm_plc_frames_total",
    "Total number of PLC-synthesised frames emitted.",
    labelnames=["call_id"],
)
"""Counter: cumulative PLC frames per call."""


# ---------------------------------------------------------------------------
# Convenience update helpers
# ---------------------------------------------------------------------------


def update_session_metrics(
    call_id: str,
    jitter_ms: float,
    loss_rate: float,
    buffer_depth: int,
) -> None:
    """Push current per-session metrics to Prometheus.

    Intended to be called once per 100 ms window by the session manager.

    Args:
        call_id:      Call identifier label.
        jitter_ms:    Current jitter estimate from AdaptiveJitterBuffer.
        loss_rate:    Fraction of frames replaced by PLC this window.
        buffer_depth: Current jitter buffer depth in frames.
    """
    JITTER_MS.labels(call_id=call_id).set(jitter_ms)
    LOSS_RATE.labels(call_id=call_id).set(loss_rate)
    BUFFER_DEPTH.labels(call_id=call_id).set(buffer_depth)


def record_plc_frames(call_id: str, count: int) -> None:
    """Increment the PLC frames counter.

    Args:
        call_id: Call identifier label.
        count:   Number of PLC frames emitted in this batch.
    """
    PLC_FRAMES_TOTAL.labels(call_id=call_id).inc(count)


def set_active_sessions(count: int) -> None:
    """Set the aggregate active session gauge.

    Args:
        count: Current number of ACTIVE/BARGE_IN sessions.
    """
    ACTIVE_SESSIONS.set(count)
