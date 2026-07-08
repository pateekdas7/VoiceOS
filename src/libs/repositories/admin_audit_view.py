"""AdminAuditViewRepository -- read-only queries over the ``admin_audit_views``
VIEW (Sprint-025 Part-3, migration 0025).

Architecture: V5 Ch13 (Administration Portal); V4 Ch11 (Audit).
"""

from __future__ import annotations

from typing import Any

from ..audit.event import AuditEvent
from ..contracts.primitives import TenantId
from .base import BaseRepository

_VIEW = "admin_audit_views"
_COLUMNS = ("audit_id", "tenant_id", "actor_id", "action", "resource_type", "resource_id", "outcome", "recorded_at")


class AdminAuditViewRepository(BaseRepository):
    """Tenant-scoped queries over ``admin_audit_views`` -- Admin-Portal-originated
    audit entries only (``resource_type = 'AdminAPI' OR action LIKE 'admin_portal.%'``,
    filtered by the view itself), giving ``AuditAdminController`` a dedicated query
    surface without duplicating audit data or re-filtering the full ``audit_log`` table."""

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[AuditEvent, ...]:
        rows = self._tenant_select(_VIEW, _COLUMNS, tenant_id, order_by="recorded_at DESC")
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> AuditEvent:
        audit_id, tenant_id, actor_id, action, resource_type, resource_id, outcome, recorded_at = row
        return AuditEvent(
            audit_id=str(audit_id),
            tenant_id=str(tenant_id),
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            recorded_at=recorded_at,
        )


__all__ = ["AdminAuditViewRepository"]
