"""TenantAdminController -- tenant config + lifecycle operations (V5 Ch13).

Architecture: V5 Ch13 (Administration Portal).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import TenantId

if TYPE_CHECKING:
    from src.services.tenant_management.service import TenantService


class TenantAdminController:
    """Tenant configuration + lifecycle operations, scoped to the caller's own tenant.

    Sprint-025.md's ``GET /admin/v1/tenants`` is documented as tenant-scoped
    (Sprint-025.md: "All endpoints are tenant-scoped (from JWT tenant_id
    claim)") -- so "list tenants" returns the caller's own tenant record as
    a single-element list, not a cross-tenant platform-superadmin listing
    (``TenantService``/``TenantRepository`` have no such method; every
    query in this codebase is mechanically tenant-scoped, AR-8).
    """

    def __init__(self, tenant_service: TenantService) -> None:
        self._tenants = tenant_service

    def list_tenants(self, tenant_id: TenantId) -> tuple[Tenant, ...]:
        tenant = self._tenants.get(tenant_id)
        return (tenant,) if tenant is not None else ()

    def get_tenant(self, tenant_id: TenantId) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def suspend(self, tenant_id: TenantId, actor_id: str) -> Tenant:
        return self._tenants.suspend(tenant_id, actor_id)

    def reactivate(self, tenant_id: TenantId, actor_id: str) -> Tenant:
        return self._tenants.reactivate(tenant_id, actor_id)


__all__ = ["TenantAdminController"]
