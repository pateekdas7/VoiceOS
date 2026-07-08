"""Prometheus metrics for the Event Bus.

Exposes the top-line reliability signals called out in V3 Ch3 §3.17
(Observability): append rate, dedup hits, and DLQ depth. Consumer lag and
replay throughput are derived from these plus XPENDING/XLEN at the
infrastructure layer (V3 Ch3 §3.17).

Architecture: V3 Ch3 §3.17; V3 Ch17 (Tracing/Observability); V7 Ch4.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

EVENTS_PUBLISHED: Counter = Counter(
    "voiceos_eventbus_events_published_total",
    "Total domain events published to the event bus.",
    labelnames=["event_type", "stream"],
)
"""Counter: events successfully appended (XADD) to a stream."""

EVENTS_CONSUMED: Counter = Counter(
    "voiceos_eventbus_events_consumed_total",
    "Total domain events successfully processed by a consumer.",
    labelnames=["event_type", "stream", "group"],
)
"""Counter: events whose handler completed and were ACKed."""

DLQ_DEPTH: Gauge = Gauge(
    "voiceos_eventbus_dlq_depth",
    "Current number of entries in a stream's dead-letter queue.",
    labelnames=["stream"],
)
"""Gauge: DLQ stream length. Zero at steady state (V3 Ch3 §3.17)."""

DEDUP_HITS: Counter = Counter(
    "voiceos_eventbus_dedup_hits_total",
    "Total events skipped because their event_id was already processed.",
    labelnames=["stream"],
)
"""Counter: consumer-side dedup hits (V3 Ch8 idempotency)."""


def record_published(event_type: str, stream: str) -> None:
    """Increment the published-events counter."""
    EVENTS_PUBLISHED.labels(event_type=event_type, stream=stream).inc()


def record_consumed(event_type: str, stream: str, group: str) -> None:
    """Increment the consumed-events counter."""
    EVENTS_CONSUMED.labels(event_type=event_type, stream=stream, group=group).inc()


def record_dlq_depth(stream: str, depth: int) -> None:
    """Set the DLQ depth gauge for ``stream``."""
    DLQ_DEPTH.labels(stream=stream).set(depth)


def record_dedup_hit(stream: str) -> None:
    """Increment the dedup-hit counter."""
    DEDUP_HITS.labels(stream=stream).inc()
