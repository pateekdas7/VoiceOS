"""Prometheus metrics for the Administration Portal (V5 Ch13, Sprint-025)."""

from __future__ import annotations

from prometheus_client import Counter

ADMIN_MUTATIONS_TOTAL: Counter = Counter(
    "voiceos_admin_mutations_total",
    "Total Admin Portal mutating requests, by route and response status.",
    labelnames=["route", "status"],
)

ADMIN_RBAC_DENIALS_TOTAL: Counter = Counter(
    "voiceos_admin_rbac_denials_total",
    "Total Admin Portal requests rejected for lacking ADMIN/SUPERVISOR role.",
)


def record_mutation(route: str, status: int) -> None:
    ADMIN_MUTATIONS_TOTAL.labels(route=route, status=str(status)).inc()


def record_rbac_denial() -> None:
    ADMIN_RBAC_DENIALS_TOTAL.inc()
