"""Prometheus metrics for the Policy Engine (V4 Ch4 §4.17 Observability).

Exposes decision rates by outcome and evaluation latency — the two
top-line signals called out for the PDP: "Decision rates (permit/deny) ...
cache hit rate, decision latency."

Architecture: V4 Ch4 §4.14 (performance targets: p99 < 10ms cached), §4.17.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

POLICY_DECISIONS_TOTAL: Counter = Counter(
    "voiceos_policy_decisions_total",
    "Total policy decisions made, by outcome.",
    labelnames=["outcome"],
)
"""Counter: one increment per PolicyEngine.evaluate() call, labeled PERMIT/DENY/REQUIRE/FORBID."""

POLICY_LATENCY_MS: Histogram = Histogram(
    "voiceos_policy_latency_ms",
    "Policy evaluation latency in milliseconds.",
    buckets=(0.5, 1, 2, 5, 10, 25, 50, 100, 250),
)
"""Histogram: evaluate() wall-clock duration. Target p99 < 10ms cached (V4 Ch4 §4.14)."""

CACHE_LOOKUPS_TOTAL: Counter = Counter(
    "voiceos_policy_cache_lookups_total",
    "Total Redis rule-cache lookups, by result.",
    labelnames=["result"],
)
"""Counter: 'hit' | 'miss', labeled by cache lookup result (V4 Ch4 §4.14 cache hit rate target > 90%)."""


def record_decision(outcome: str) -> None:
    """Increment the decisions-by-outcome counter."""
    POLICY_DECISIONS_TOTAL.labels(outcome=outcome).inc()


def record_latency_ms(duration_ms: float) -> None:
    """Observe one evaluation's latency."""
    POLICY_LATENCY_MS.observe(duration_ms)


def record_cache_lookup(hit: bool) -> None:
    """Increment the cache-lookup counter for a hit or miss."""
    CACHE_LOOKUPS_TOTAL.labels(result="hit" if hit else "miss").inc()
