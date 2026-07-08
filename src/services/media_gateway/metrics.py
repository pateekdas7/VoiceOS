"""Prometheus metrics for the Media Gateway service.

Exposes three core gauges/counters that operations and alerting consume:

- voiceos_media_gateway_active_sessions (Gauge)
    Number of currently admitted call sessions.  Reaches zero when all calls
    disconnect; a sudden drop indicates a service restart or carrier disconnect.

- voiceos_media_gateway_admission_rejections_total (Counter)
    Incremented on every auth failure or SessionGate.reject() call.  High rates
    indicate carrier misconfiguration, token rotation failures, or attack traffic.

- voiceos_media_gateway_bytes_received_total (Counter)
    Total PCM audio bytes received from carrier transports.  Used for bitrate
    monitoring and capacity planning (V7 Ch5).

Architecture: V1 Ch3 (Media Gateway outputs); V3 Ch17 (Observability);
              V7 Ch4 (Monitoring & Alerting).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

# ---------------------------------------------------------------------------
# Metrics registry
# ---------------------------------------------------------------------------

ACTIVE_SESSIONS: Gauge = Gauge(
    "voiceos_media_gateway_active_sessions",
    "Number of currently admitted call sessions in the Media Gateway.",
    labelnames=["adapter_type"],
)
"""Gauge: current active sessions per adapter type ('twilio' | 'sip_rtp')."""

ADMISSION_REJECTIONS: Counter = Counter(
    "voiceos_media_gateway_admission_rejections_total",
    "Total number of connection attempts rejected by the Media Gateway.",
    labelnames=["reason", "adapter_type"],
)
"""Counter: rejected connections labelled by reason code and adapter type."""

BYTES_RECEIVED: Counter = Counter(
    "voiceos_media_gateway_bytes_received_total",
    "Total audio bytes received from carrier transport adapters.",
    labelnames=["adapter_type"],
)
"""Counter: cumulative bytes received, labelled by adapter type."""


# ---------------------------------------------------------------------------
# Convenience wrappers (thin, no coupling to specific adapter internals)
# ---------------------------------------------------------------------------


def record_session_admitted(adapter_type: str) -> None:
    """Increment active sessions gauge for ``adapter_type``."""
    ACTIVE_SESSIONS.labels(adapter_type=adapter_type).inc()


def record_session_released(adapter_type: str) -> None:
    """Decrement active sessions gauge for ``adapter_type``."""
    ACTIVE_SESSIONS.labels(adapter_type=adapter_type).dec()


def record_admission_rejection(reason: str, adapter_type: str) -> None:
    """Increment rejection counter with the given reason code."""
    ADMISSION_REJECTIONS.labels(reason=reason, adapter_type=adapter_type).inc()


def record_bytes_received(byte_count: int, adapter_type: str) -> None:
    """Add ``byte_count`` to the bytes_received counter for ``adapter_type``."""
    BYTES_RECEIVED.labels(adapter_type=adapter_type).inc(byte_count)
