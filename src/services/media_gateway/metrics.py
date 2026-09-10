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

CALLS_BLOCKED_CONSENT_REVOKED: Counter = Counter(
    "voiceos_media_gateway_calls_blocked_consent_revoked_total",
    "Total call-open admissions rejected because the customer's consent is revoked.",
    labelnames=["tenant_id"],
)
"""Counter: connections closed at the WebSocket-open boundary because
the customer's consent has been revoked. Defense-in-depth against
schedule-time races (revocation lands after the scheduler cleared the
call but before the WebSocket actually connected) and inbound calls
that never went through the scheduler at all. A rising rate here means
the upstream consent surface (CRM/self-service) is producing revocations
faster than the scheduler notices — normal for the first minute after a
bulk import, alertable if it stays elevated."""

GREETING_OUTCOMES: Counter = Counter(
    "voiceos_media_gateway_greeting_outcomes_total",
    "Terminal outcome of the call-open greeting (per call).",
    labelnames=["outcome"],
)
"""Counter: greeting outcomes — ``ok`` (spoke and returned), ``timeout``
(hit greeting_timeout_s — GPU TTS unreachable/slow), ``error`` (exception).
A rising timeout rate is the earliest signal of GPU/TTS regression: without
this metric, a hung greeting only surfaces in per-call logs long after
callers have already heard 30–60s of dead air."""


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


def record_call_blocked_consent_revoked(tenant_id: str) -> None:
    """Increment the consent-revoked block counter for ``tenant_id``."""
    CALLS_BLOCKED_CONSENT_REVOKED.labels(tenant_id=tenant_id).inc()
