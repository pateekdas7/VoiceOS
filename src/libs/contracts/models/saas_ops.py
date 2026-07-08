"""Persistent data models for the SaaS Operations Platform (V5 Ch23).

Feature flags gate experimental features per tenant/plan/cohort with
gradual percentage rollout; fleet rollout rings stage progressive version
releases across a tenant population; tenant data migrations are durable,
idempotent, per-tenant-locked schema/data changes with a mandatory rollback
procedure.

Architecture: V5 Ch23 (SaaS Operations Platform).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId


class FeatureFlagState(StrEnum):
    """Lifecycle state of a feature flag row (V5 Ch23)."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    GRADUAL_ROLLOUT = "GRADUAL_ROLLOUT"


class FeatureFlagScope(StrEnum):
    """Targeting scope, narrowest wins (V5 Ch23: "global -> plan-level -> cohort -> per-tenant")."""

    GLOBAL = "GLOBAL"
    PLAN = "PLAN"
    COHORT = "COHORT"
    TENANT = "TENANT"


class FeatureFlag(BaseModel):
    """One targeting row for a named flag.

    A flag is the union of its rows across scopes: at most one ``GLOBAL`` row
    (``scope_value=None``), and any number of ``PLAN``/``COHORT``/``TENANT``
    rows keyed by their respective ``scope_value`` (a plan-tier name, a
    cohort name, or a ``tenant_id``).
    """

    model_config = ConfigDict(frozen=True)

    flag_id: str
    flag_name: str
    scope: FeatureFlagScope
    scope_value: str | None = None
    state: FeatureFlagState = FeatureFlagState.DISABLED
    rollout_percentage: float = Field(default=0.0, ge=0.0, le=100.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RolloutRing(IntEnum):
    """Progressive fleet-rollout ring assignment (V5 Ch23)."""

    CANARY = 1
    EARLY = 2
    GENERAL = 3
    LAGGARD = 4


class TenantRolloutAssignment(BaseModel):
    """A tenant's (idempotent, sticky) assignment to a rollout ring."""

    model_config = ConfigDict(frozen=True)

    tenant_id: TenantId
    ring: RolloutRing
    assigned_at: datetime


class FleetVersion(BaseModel):
    """The current target version promoted to a given rollout ring."""

    model_config = ConfigDict(frozen=True)

    ring: RolloutRing
    version: str
    promoted_at: datetime


class TenantMigrationStatus(StrEnum):
    """Lifecycle status of a durable tenant data migration run."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class TenantMigrationRecord(BaseModel):
    """One durable, idempotent per-tenant migration run.

    ``migration_id`` is the idempotency key (V5 Ch23: "migration_id acts as
    idempotency key") -- the same ``(tenant_id, migration_id)`` pair never
    re-executes its effect once a COMPLETED row exists.
    """

    model_config = ConfigDict(frozen=True)

    migration_id: str
    tenant_id: TenantId
    status: TenantMigrationStatus
    detail: str = ""
    started_at: datetime
    completed_at: datetime | None = None


class EntitlementAuditResult(BaseModel):
    """Point-in-time snapshot of a tenant's license/entitlement standing (V5 Ch23)."""

    model_config = ConfigDict(frozen=True)

    tenant_id: TenantId
    plan_tier: str
    within_entitlement: bool
    reason: str = ""
    checked_at: datetime


__all__ = [
    "EntitlementAuditResult",
    "FeatureFlag",
    "FeatureFlagScope",
    "FeatureFlagState",
    "FleetVersion",
    "RolloutRing",
    "TenantMigrationRecord",
    "TenantMigrationStatus",
    "TenantRolloutAssignment",
]
