"""SaaS Operations Platform -- feature flags, fleet rollout rings, tenant data
migrations, entitlement operations (V5 Ch23, Sprint-026).

Note: Sprint-026.md's own directory listing writes this package as
``src/services/saas-ops/`` (hyphenated) -- not a valid Python package name.
Named ``saas_ops`` (underscore) instead, same resolved-ambiguity precedent as
``admin-portal``/``ai-config``/``bi-platform`` in Sprint-024/025.
"""

from __future__ import annotations

from .entitlement_ops import EntitlementOpsService
from .feature_flags import FeatureFlagService
from .fleet_rollout import FleetRolloutManager
from .migration import TenantDataMigration, TenantMigrationAlreadyRunningError, TenantMigrationDefinition

__all__ = [
    "EntitlementOpsService",
    "FeatureFlagService",
    "FleetRolloutManager",
    "TenantDataMigration",
    "TenantMigrationAlreadyRunningError",
    "TenantMigrationDefinition",
]
