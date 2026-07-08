"""Prometheus metrics for the Collections domain (V5 Ch4 Observability).

Architecture: V5 Ch4 §4.16/§4.17 (Observability — PTP outcome rates, DPD
distribution).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

PTPS_CREATED_TOTAL: Counter = Counter(
    "voiceos_ptps_created_total",
    "Total Promise-To-Pay records created.",
)

PTPS_FULFILLED_TOTAL: Counter = Counter(
    "voiceos_ptps_fulfilled_total",
    "Total PTPs that transitioned to KEPT.",
)

PTPS_BROKEN_TOTAL: Counter = Counter(
    "voiceos_ptps_broken_total",
    "Total PTPs that transitioned to BROKEN.",
)

DPD_DISTRIBUTION: Histogram = Histogram(
    "voiceos_dpd_distribution",
    "Distribution of real-time DPD values across evaluated loan accounts.",
    buckets=(0, 1, 30, 60, 90, 180, 365),
)


def record_ptp_created() -> None:
    PTPS_CREATED_TOTAL.inc()


def record_ptp_fulfilled() -> None:
    PTPS_FULFILLED_TOTAL.inc()


def record_ptp_broken() -> None:
    PTPS_BROKEN_TOTAL.inc()


def record_dpd(dpd: int) -> None:
    DPD_DISTRIBUTION.observe(dpd)
