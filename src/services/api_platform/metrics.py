"""Prometheus metrics for the Public API Platform (V5 Ch16, Sprint-025)."""

from __future__ import annotations

from prometheus_client import Counter

PUBLIC_API_REQUESTS_TOTAL: Counter = Counter(
    "voiceos_public_api_requests_total",
    "Total Public API requests, by route and response status.",
    labelnames=["route", "status"],
)

PUBLIC_API_RATE_LIMIT_REJECTIONS_TOTAL: Counter = Counter(
    "voiceos_public_api_rate_limit_rejections_total",
    "Total Public API requests rejected for exceeding the per-tenant rate limit.",
)

PUBLIC_API_ENTITLEMENT_DENIALS_TOTAL: Counter = Counter(
    "voiceos_public_api_entitlement_denials_total",
    "Total Public API requests denied by PolicyEngine entitlement checks.",
)


def record_request(route: str, status: int) -> None:
    PUBLIC_API_REQUESTS_TOTAL.labels(route=route, status=str(status)).inc()


def record_rate_limit_rejection() -> None:
    PUBLIC_API_RATE_LIMIT_REJECTIONS_TOTAL.inc()


def record_entitlement_denial() -> None:
    PUBLIC_API_ENTITLEMENT_DENIALS_TOTAL.inc()
