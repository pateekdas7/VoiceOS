"""Narrow structural port ``org_management`` depends on (see tenant_management.ports)."""

from __future__ import annotations

from typing import Protocol

from src.libs.contracts.primitives import TenantId

from .models import Branch, BusinessUnit, Organization


class OrganizationRepositoryPort(Protocol):
    """The ``organizations``/``business_units``/``branches`` operations OrgService needs."""

    def create_organization(self, org: Organization) -> Organization: ...

    def create_business_unit(self, tenant_id: TenantId, org_id: str, bu: BusinessUnit) -> BusinessUnit: ...

    def create_branch(self, tenant_id: TenantId, bu_id: str, branch: Branch) -> Branch: ...

    def get_organization(self, tenant_id: TenantId) -> Organization | None: ...
