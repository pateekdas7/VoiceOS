"""Prometheus metrics for the CRM domain (V5 Ch3/Ch4 Observability).

Architecture: V5 Ch4 §4.14 (performance targets: context assembly < 50ms).
"""

from __future__ import annotations

from prometheus_client import Gauge, Histogram

CUSTOMER_COUNT: Gauge = Gauge(
    "voiceos_crm_customer_count",
    "Number of active customer records, by tenant.",
    labelnames=["tenant_id"],
)

CONTEXT_ASSEMBLY_LATENCY_MS: Histogram = Histogram(
    "voiceos_context_assembly_latency_ms",
    "CustomerContextAssembler.assemble() wall-clock duration in milliseconds.",
    buckets=(5, 10, 20, 50, 100, 250, 500),
)
"""Target p99 < 50ms (V5 Ch4 §5.14); Sprint-022.md's own AC uses a looser
p99 < 100ms pass/fail bar — this histogram lets both be checked."""


def set_customer_count(tenant_id: str, count: int) -> None:
    CUSTOMER_COUNT.labels(tenant_id=tenant_id).set(count)


def record_context_assembly_latency_ms(duration_ms: float) -> None:
    CONTEXT_ASSEMBLY_LATENCY_MS.observe(duration_ms)
