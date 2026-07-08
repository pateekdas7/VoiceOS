"""OrgService — CRUD over Organization/BusinessUnit/Branch (V5 Ch2).

Architecture: V5 Ch2 (Multi-Tenant Architecture — org hierarchy).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.contracts.primitives import TenantId

from .hierarchy import OrgHierarchy
from .models import Branch, BusinessUnit, Organization
from .ports import OrganizationRepositoryPort


class OrgService:
    """CRUD + hierarchy access for a tenant's Organization/BusinessUnit/Branch tree."""

    def __init__(self, organization_repository: OrganizationRepositoryPort) -> None:
        self._repo = organization_repository

    def create_organization(self, tenant_id: TenantId, legal_name: str, country: str = "IN") -> Organization:
        now = datetime.now(UTC)
        org = Organization(
            org_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            legal_name=legal_name,
            country=country,
            created_at=now,
            updated_at=now,
        )
        return self._repo.create_organization(org)

    def add_business_unit(self, tenant_id: TenantId, org_id: str, name: str) -> BusinessUnit:
        bu = BusinessUnit(bu_id=str(uuid.uuid4()), name=name)
        return self._repo.create_business_unit(tenant_id, org_id, bu)

    def add_branch(self, tenant_id: TenantId, bu_id: str, name: str, city: str = "", state: str = "") -> Branch:
        branch = Branch(branch_id=str(uuid.uuid4()), name=name, city=city, state=state)
        return self._repo.create_branch(tenant_id, bu_id, branch)

    def get_organization(self, tenant_id: TenantId) -> Organization | None:
        return self._repo.get_organization(tenant_id)

    def get_hierarchy(self, tenant_id: TenantId) -> OrgHierarchy | None:
        organization = self._repo.get_organization(tenant_id)
        if organization is None:
            return None
        return OrgHierarchy(organization)
