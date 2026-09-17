"""Prometheus metrics — RED signals + cross-cutting reliability gauges (V3 Ch15).

Every service gets standard RED metrics (Requests, Errors, Duration) via
:func:`get_red_metrics`. Cross-cutting counters (``call_count``,
``intent_distribution``, ``negotiation_outcome``) and the reliability
gauges this sprint introduces (``circuit_breaker_state``, RI-3's
``queue_size_max``/``queue_size_current``) are module-level singletons.

Architecture: V3 Ch15 (Observability/Metrics) §15.7, §15.12; V3 Ch14 §14.17;
V3 Ch9/Ch10 (RI-3 queue observability).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from src.libs.circuit_breaker.breaker import CircuitState

# ---------------------------------------------------------------------------
# Cross-cutting counters (module-level singletons — one process-wide series)
# ---------------------------------------------------------------------------

call_count_total = Counter(
    "voiceos_call_count_total",
    "Total calls handled, by outcome.",
    labelnames=["outcome"],
)

intent_distribution_total = Counter(
    "voiceos_intent_distribution_total",
    "Distribution of detected customer intents.",
    labelnames=["intent"],
)

negotiation_outcome_total = Counter(
    "voiceos_negotiation_outcome_total",
    "Negotiation engine outcomes.",
    labelnames=["outcome"],
)

circuit_breaker_state = Gauge(
    "voiceos_circuit_breaker_state",
    "Circuit breaker state per dependency (0=CLOSED, 1=HALF_OPEN, 2=OPEN).",
    labelnames=["service"],
)

queue_size_max = Gauge(
    "voiceos_queue_size_max",
    "Configured max_size for a bounded queue (RI-3 compliance).",
    labelnames=["queue_name"],
)

queue_size_current = Gauge(
    "voiceos_queue_size_current",
    "Current depth of a bounded queue.",
    labelnames=["queue_name"],
)

_STATE_TO_VALUE = {
    CircuitState.CLOSED: 0,
    CircuitState.HALF_OPEN: 1,
    CircuitState.OPEN: 2,
}


def record_circuit_breaker_state(service_name: str, state: CircuitState) -> None:
    """Set the ``circuit_breaker_state`` gauge for ``service_name``.

    Designed to be passed as ``CircuitBreaker(..., on_state_change=...)``.
    """
    circuit_breaker_state.labels(service=service_name).set(_STATE_TO_VALUE[state])


def record_queue_max_size(queue_name: str, max_size: int) -> None:
    """Set the ``queue_size_max`` gauge — call once per BoundedQueue instantiation (RI-3)."""
    queue_size_max.labels(queue_name=queue_name).set(max_size)


def record_queue_depth(queue_name: str, depth: int) -> None:
    """Set the ``queue_size_current`` gauge for ``queue_name``."""
    queue_size_current.labels(queue_name=queue_name).set(depth)


# ---------------------------------------------------------------------------
# Section 17.2 — Service-level metrics
# ---------------------------------------------------------------------------

calls_initiated_total = Counter(
    "voiceos_calls_initiated_total",
    "Total outbound calls placed, by tenant and campaign.",
    labelnames=["tenant_id", "campaign_id"],
)

calls_completed_total = Counter(
    "voiceos_calls_completed_total",
    "Total calls that reached a terminal state.",
    labelnames=["tenant_id", "outcome"],
)

call_duration_seconds = Histogram(
    "voiceos_call_duration_seconds",
    "End-to-end call duration from first WebSocket frame to call end.",
    buckets=(30, 60, 120, 180, 300, 600, 900, 1800, 3600),
    labelnames=["tenant_id"],
)

dialer_queue_depth = Gauge(
    "voiceos_queue_depth",
    "Current depth of the per-tenant outbound dialer Redis queue.",
    labelnames=["tenant_id"],
)

queue_age_seconds = Histogram(
    "voiceos_queue_age_seconds",
    "Age of calls sitting in the dialer queue waiting to be placed.",
    buckets=(5, 15, 30, 60, 120, 300, 600, 1800),
    labelnames=["tenant_id"],
)

retry_count_total = Counter(
    "voiceos_retry_count_total",
    "Total call retry attempts, labelled by attempt number.",
    labelnames=["tenant_id", "attempt"],
)

stuck_calls_total = Counter(
    "voiceos_stuck_calls_total",
    "Calls detected as stuck (no state change within the watchdog window).",
    labelnames=["tenant_id"],
)

callback_auth_failures_total = Counter(
    "voiceos_callback_auth_failures_total",
    "Twilio dialer callback requests with an invalid HMAC-SHA1 signature.",
    labelnames=["source"],
)


def record_call_initiated(tenant_id: str, campaign_id: str) -> None:
    calls_initiated_total.labels(tenant_id=tenant_id, campaign_id=campaign_id).inc()


def record_call_completed(tenant_id: str, outcome: str) -> None:
    calls_completed_total.labels(tenant_id=tenant_id, outcome=outcome).inc()


def record_call_duration(tenant_id: str, duration_seconds: float) -> None:
    call_duration_seconds.labels(tenant_id=tenant_id).observe(duration_seconds)


def record_dialer_queue_depth(tenant_id: str, depth: int) -> None:
    dialer_queue_depth.labels(tenant_id=tenant_id).set(depth)


def record_queue_age(tenant_id: str, age_seconds: float) -> None:
    queue_age_seconds.labels(tenant_id=tenant_id).observe(age_seconds)


def record_retry(tenant_id: str, attempt: int) -> None:
    retry_count_total.labels(tenant_id=tenant_id, attempt=str(attempt)).inc()


def record_stuck_call(tenant_id: str) -> None:
    stuck_calls_total.labels(tenant_id=tenant_id).inc()


def record_callback_auth_failure(source: str) -> None:
    callback_auth_failures_total.labels(source=source).inc()


# ---------------------------------------------------------------------------
# Section 17.2 — Voice path latency metrics
# ---------------------------------------------------------------------------

stt_latency_ms = Histogram(
    "voiceos_stt_latency_ms",
    "STT transcription latency from first frame to final transcript.",
    buckets=(100, 200, 400, 700, 1000, 1500, 2500, 5000),
    labelnames=["language", "result"],
)

llm_latency_ms = Histogram(
    "voiceos_llm_latency_ms",
    "LLM response latency from TurnInput to first AudioClause.",
    buckets=(100, 200, 400, 700, 1000, 1500, 2500, 5000),
    labelnames=["dialogue_state"],
)

tts_latency_ms = Histogram(
    "voiceos_tts_latency_ms",
    "TTS synthesis latency from text clause to first audio frame (TTFA).",
    buckets=(100, 250, 500, 1000, 2500, 5000, 10000, 20000),
    labelnames=["cached"],
)

turn_latency_ms = Histogram(
    "voiceos_turn_latency_ms",
    "End-to-end turn latency: VADSpeechEnd → first TTS frame sent.",
    buckets=(200, 400, 700, 1000, 1500, 2500, 4000, 7000),
    labelnames=["tenant_id"],
)

gpu_errors_total = Counter(
    "voiceos_gpu_errors_total",
    "Total errors from GPU-hosted AI services.",
    labelnames=["service"],
)

ws_disconnects_total = Counter(
    "voiceos_ws_disconnects_total",
    "Total Twilio WebSocket disconnects.",
    labelnames=["reason"],
)


def record_stt_latency(language: str, result: str, latency_ms: float) -> None:
    stt_latency_ms.labels(language=language, result=result).observe(latency_ms)


def record_llm_latency(dialogue_state: str, latency_ms: float) -> None:
    llm_latency_ms.labels(dialogue_state=dialogue_state).observe(latency_ms)


def record_tts_latency(cached: bool, latency_ms: float) -> None:
    tts_latency_ms.labels(cached="true" if cached else "false").observe(latency_ms)


def record_turn_latency(tenant_id: str, latency_ms: float) -> None:
    turn_latency_ms.labels(tenant_id=tenant_id).observe(latency_ms)


def record_gpu_error(service: str) -> None:
    gpu_errors_total.labels(service=service).inc()


def record_ws_disconnect(reason: str) -> None:
    ws_disconnects_total.labels(reason=reason).inc()


# ---------------------------------------------------------------------------
# Section 17.2 — Business metrics
# ---------------------------------------------------------------------------

ptp_created_total = Counter(
    "voiceos_ptp_created_total",
    "Total promise-to-pay agreements created.",
    labelnames=["tenant_id"],
)

hitl_escalations_total = Counter(
    "voiceos_hitl_escalations_total",
    "Total calls escalated to a human agent.",
    labelnames=["tenant_id", "reason"],
)

hitl_sla_breached_total = Counter(
    "voiceos_hitl_sla_breached_total",
    "Total HITL escalations that breached the SLA pickup time.",
    labelnames=["tenant_id"],
)

billing_events_total = Counter(
    "voiceos_billing_events_total",
    "Total billing events emitted, labelled by event type.",
    labelnames=["tenant_id", "event_type"],
)


def record_ptp_created(tenant_id: str) -> None:
    ptp_created_total.labels(tenant_id=tenant_id).inc()


def record_hitl_escalation(tenant_id: str, reason: str) -> None:
    hitl_escalations_total.labels(tenant_id=tenant_id, reason=reason).inc()


def record_hitl_sla_breach(tenant_id: str) -> None:
    hitl_sla_breached_total.labels(tenant_id=tenant_id).inc()


def record_billing_event(tenant_id: str, event_type: str) -> None:
    billing_events_total.labels(tenant_id=tenant_id, event_type=event_type).inc()


# ---------------------------------------------------------------------------
# Per-service RED metrics
# ---------------------------------------------------------------------------


class REDMetrics:
    """Requests / Errors / Duration metrics for one service (V3 Ch15 §15.2)."""

    def __init__(self, service_name: str, *, registry: CollectorRegistry | None = None) -> None:
        self.requests_total = Counter(
            f"voiceos_{service_name}_requests_total",
            f"Total requests handled by {service_name}.",
            labelnames=["status"],
            registry=registry,
        )
        self.errors_total = Counter(
            f"voiceos_{service_name}_errors_total",
            f"Total errors raised by {service_name}.",
            labelnames=["error_type"],
            registry=registry,
        )
        self.request_duration_ms = Histogram(
            f"voiceos_{service_name}_request_duration_ms",
            f"Request duration for {service_name}, in milliseconds.",
            buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
            registry=registry,
        )

    def record_request(self, status: str, duration_ms: float) -> None:
        """Record one completed request: increments the count, observes duration."""
        self.requests_total.labels(status=status).inc()
        self.request_duration_ms.observe(duration_ms)

    def record_error(self, error_type: str) -> None:
        """Record one error, tagged by ``error_type``."""
        self.errors_total.labels(error_type=error_type).inc()


_RED_METRICS_CACHE: dict[str, REDMetrics] = {}


def get_red_metrics(service_name: str, *, registry: CollectorRegistry | None = None) -> REDMetrics:
    """Return the (process-wide, cached) :class:`REDMetrics` for ``service_name``.

    Prometheus metric names must be registered exactly once per process —
    this cache ensures repeated calls for the same ``service_name`` reuse
    the same collector instead of raising a duplicate-registration error.
    """
    if service_name not in _RED_METRICS_CACHE:
        _RED_METRICS_CACHE[service_name] = REDMetrics(service_name, registry=registry)
    return _RED_METRICS_CACHE[service_name]
