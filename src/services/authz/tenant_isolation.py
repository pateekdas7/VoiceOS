"""TenantIsolationGuard — the enforcement point for AR-8 (V4 Ch6 §6.6).

Every authorization check the :class:`~.service.AuthzService` performs is
preceded by this guard. A cross-tenant access attempt never reaches the
RBAC/ABAC layer — it is rejected immediately and logged as a CRITICAL
security event.

Architecture: V4 Ch6 (Authorization/RBAC — tenant isolation); AR-8.
"""

from __future__ import annotations

import logging

from src.services.auth.models import AuthContext
from src.services.auth.mtls_enforcer import SERVICE_MESH_TENANT_ID

logger = logging.getLogger(__name__)


class TenantIsolationViolationError(Exception):
    """Raised when a subject attempts to access a resource outside its tenant scope.

    Every occurrence is logged as a CRITICAL security event (V4 Ch6 §6.6)
    before this exception propagates — this is not a routine DENY, it is a
    signal that either a bug or an active attack is in progress.
    """


class TenantIsolationGuard:
    """Enforces AR-8 immediately before any RBAC/ABAC check runs.

    A system-level identity (``tenant_id == SERVICE_MESH_TENANT_ID`` —
    mTLS-authenticated service-to-service calls, V4 Ch5 §5.4) is exempt:
    internal services are not scoped to a single customer tenant.
    """

    def enforce(self, auth_context: AuthContext, resource_tenant_id: str) -> None:
        """Raise :class:`TenantIsolationViolationError` on a cross-tenant mismatch."""
        if auth_context.tenant_id == resource_tenant_id:
            return
        if auth_context.tenant_id == SERVICE_MESH_TENANT_ID:
            return
        logger.critical(
            "TenantIsolationGuard: CROSS-TENANT ACCESS ATTEMPT subject=%s auth_tenant=%s resource_tenant=%s",
            auth_context.subject,
            auth_context.tenant_id,
            resource_tenant_id,
        )
        raise TenantIsolationViolationError(
            f"subject tenant '{auth_context.tenant_id}' may not access resource tenant '{resource_tenant_id}'"
        )
