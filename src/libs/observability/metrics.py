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
