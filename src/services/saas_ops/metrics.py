"""Prometheus metrics for the SaaS Operations Platform (V5 Ch23, Sprint-026)."""

from __future__ import annotations

from prometheus_client import Counter

FLAG_RESOLUTIONS_TOTAL: Counter = Counter(
    "voiceos_saas_ops_flag_resolutions_total",
    "Total FeatureFlagService.is_enabled() resolutions, by cache outcome.",
    labelnames=["cache"],
)

RING_ASSIGNMENTS_TOTAL: Counter = Counter(
    "voiceos_saas_ops_ring_assignments_total",
    "Total tenant rollout ring assignments, by ring.",
    labelnames=["ring"],
)

RING_PROMOTIONS_TOTAL: Counter = Counter(
    "voiceos_saas_ops_ring_promotions_total",
    "Total fleet version promotions, by ring.",
    labelnames=["ring"],
)

MIGRATION_RUNS_TOTAL: Counter = Counter(
    "voiceos_saas_ops_migration_runs_total",
    "Total TenantDataMigration.run_migration() calls, by outcome.",
    labelnames=["migration_id", "outcome"],
)

ENTITLEMENT_CHECKS_TOTAL: Counter = Counter(
    "voiceos_saas_ops_entitlement_checks_total",
    "Total EntitlementOpsService.check_license() calls, by result.",
    labelnames=["within_entitlement"],
)


def record_flag_resolution(*, cache_hit: bool) -> None:
    FLAG_RESOLUTIONS_TOTAL.labels(cache="hit" if cache_hit else "miss").inc()


def record_ring_assignment(ring: object) -> None:
    RING_ASSIGNMENTS_TOTAL.labels(ring=str(getattr(ring, "name", ring))).inc()


def record_ring_promotion(ring: object, version: str) -> None:
    RING_PROMOTIONS_TOTAL.labels(ring=str(getattr(ring, "name", ring))).inc()


def record_migration_run(migration_id: str, *, cache_hit: bool, failed: bool = False) -> None:
    outcome = "failed" if failed else ("already_run" if cache_hit else "completed")
    MIGRATION_RUNS_TOTAL.labels(migration_id=migration_id, outcome=outcome).inc()


def record_entitlement_check(*, within: bool) -> None:
    ENTITLEMENT_CHECKS_TOTAL.labels(within_entitlement=str(within)).inc()
