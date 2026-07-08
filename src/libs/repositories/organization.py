"""OrganizationRepository — Organization/BusinessUnit/Branch hierarchy (V5 Ch2).

Architecture: V5 Ch2 (Multi-Tenant Architecture — org hierarchy); AR-8.
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.tenant import Branch, BusinessUnit, Organization
from ..contracts.primitives import TenantId
from .base import BaseRepository

_ORG_TABLE = "organizations"
_BU_TABLE = "business_units"
_BRANCH_TABLE = "branches"

_ORG_COLUMNS = ("org_id", "tenant_id", "legal_name", "country", "registration_number", "created_at", "updated_at")
_BU_COLUMNS = ("bu_id", "org_id", "name", "is_active")
_BRANCH_COLUMNS = ("branch_id", "bu_id", "name", "city", "state", "is_active")


class OrganizationRepository(BaseRepository):
    """CRUD + hierarchy-traversal queries over ``organizations``/``business_units``/``branches``."""

    def create_organization(self, org: Organization) -> Organization:
        self._execute(
            f"""
            INSERT INTO {_ORG_TABLE} (
                org_id, tenant_id, legal_name, country, registration_number, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                org.org_id,
                org.tenant_id,
                org.legal_name,
                org.country,
                org.registration_number,
                org.created_at,
                org.updated_at,
            ),
        )
        self._commit()
        return org

    def create_business_unit(self, tenant_id: TenantId, org_id: str, bu: BusinessUnit) -> BusinessUnit:
        self._execute(
            f"INSERT INTO {_BU_TABLE} (bu_id, org_id, tenant_id, name, is_active) VALUES (%s, %s, %s, %s, %s)",
            (bu.bu_id, org_id, tenant_id, bu.name, bu.is_active),
        )
        self._commit()
        return bu

    def create_branch(self, tenant_id: TenantId, bu_id: str, branch: Branch) -> Branch:
        self._execute(
            f"""
            INSERT INTO {_BRANCH_TABLE} (branch_id, bu_id, tenant_id, name, city, state, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (branch.branch_id, bu_id, tenant_id, branch.name, branch.city, branch.state, branch.is_active),
        )
        self._commit()
        return branch

    def get_organization(self, tenant_id: TenantId) -> Organization | None:
        """Fetch the tenant's single Organization, fully hydrated with its BU/Branch hierarchy."""
        org_rows = self._tenant_select(_ORG_TABLE, _ORG_COLUMNS, tenant_id)
        if not org_rows:
            return None
        org_id, _tenant_id, legal_name, country, registration_number, created_at, updated_at = org_rows[0]

        bu_rows = self._tenant_select(
            _BU_TABLE, _BU_COLUMNS, tenant_id, extra_where="org_id = %s", extra_params=(org_id,)
        )
        business_units = tuple(self._hydrate_bu(tenant_id, row) for row in bu_rows)

        return Organization(
            org_id=str(org_id),
            tenant_id=tenant_id,
            legal_name=legal_name,
            country=country,
            registration_number=registration_number,
            business_units=business_units,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _hydrate_bu(self, tenant_id: TenantId, row: tuple[Any, ...]) -> BusinessUnit:
        bu_id, _org_id, name, is_active = row
        branch_rows = self._tenant_select(
            _BRANCH_TABLE, _BRANCH_COLUMNS, tenant_id, extra_where="bu_id = %s", extra_params=(bu_id,)
        )
        branches = tuple(
            Branch(branch_id=str(b_id), name=b_name, city=city, state=state, is_active=b_active)
            for b_id, _bu_id, b_name, city, state, b_active in branch_rows
        )
        return BusinessUnit(bu_id=str(bu_id), name=name, branches=branches, is_active=is_active)
