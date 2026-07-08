"""Unit tests for Org Management (Sprint-021, V5 Ch2 §2.4/2.5).

All tests run fully in-process — no live Postgres required (Phase 1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.contracts.models.tenant import Branch, BusinessUnit, Organization
from src.libs.contracts.models.user import OrgScope
from src.libs.contracts.primitives import TenantId
from src.services.org_management.hierarchy import OrgHierarchy
from src.services.org_management.service import OrgService

TENANT_ID = TenantId(str(uuid.uuid4()))


class _FakeOrganizationRepository:
    """In-memory ``OrganizationRepository`` double."""

    def __init__(self) -> None:
        self._org: Organization | None = None

    def create_organization(self, org: Organization) -> Organization:
        self._org = org
        return org

    def create_business_unit(self, tenant_id: TenantId, org_id: str, bu: BusinessUnit) -> BusinessUnit:
        assert self._org is not None
        self._org = self._org.model_copy(update={"business_units": (*self._org.business_units, bu)})
        return bu

    def create_branch(self, tenant_id: TenantId, bu_id: str, branch: Branch) -> Branch:
        assert self._org is not None
        updated_bus = tuple(
            bu.model_copy(update={"branches": (*bu.branches, branch)}) if bu.bu_id == bu_id else bu
            for bu in self._org.business_units
        )
        self._org = self._org.model_copy(update={"business_units": updated_bus})
        return branch

    def get_organization(self, tenant_id: TenantId) -> Organization | None:
        return self._org


def _make_org_with_two_bus() -> Organization:
    """One Organization: BU-1 (branch-A, branch-B), BU-2 (branch-C)."""
    now = datetime.now(UTC)
    return Organization(
        org_id="org-1",
        tenant_id=TENANT_ID,
        legal_name="Acme Collections Pvt Ltd",
        business_units=(
            BusinessUnit(
                bu_id="bu-1",
                name="Retail Collections",
                branches=(
                    Branch(branch_id="branch-a", name="Mumbai South"),
                    Branch(branch_id="branch-b", name="Mumbai North"),
                ),
            ),
            BusinessUnit(
                bu_id="bu-2",
                name="SME Loans",
                branches=(Branch(branch_id="branch-c", name="Pune Central"),),
            ),
        ),
        created_at=now,
        updated_at=now,
    )


class TestOrgHierarchyResolveScope:
    def test_org_scope_bu_sees_branches(self) -> None:
        """User at BU level sees branch resources (required named test)."""
        hierarchy = OrgHierarchy(_make_org_with_two_bus())
        user_scope = OrgScope(scope_type="BUSINESS_UNIT", scope_id="bu-1")
        resource_scope = OrgScope(scope_type="BRANCH", scope_id="branch-a")
        assert hierarchy.resolve_scope(user_scope, resource_scope) is True

    def test_org_scope_branch_cannot_see_other_bu(self) -> None:
        """User at Branch level cannot see sibling branch (required named test)."""
        hierarchy = OrgHierarchy(_make_org_with_two_bus())
        user_scope = OrgScope(scope_type="BRANCH", scope_id="branch-a")
        sibling_scope = OrgScope(scope_type="BRANCH", scope_id="branch-b")
        assert hierarchy.resolve_scope(user_scope, sibling_scope) is False

        other_bu_scope = OrgScope(scope_type="BRANCH", scope_id="branch-c")
        assert hierarchy.resolve_scope(user_scope, other_bu_scope) is False

    def test_org_scope_org_sees_all_bus(self) -> None:
        """User scoped to Org sees all BUs."""
        hierarchy = OrgHierarchy(_make_org_with_two_bus())
        user_scope = OrgScope(scope_type="ORG", scope_id="org-1")
        for bu_id in ("bu-1", "bu-2"):
            assert hierarchy.resolve_scope(user_scope, OrgScope(scope_type="BUSINESS_UNIT", scope_id=bu_id)) is True
        assert hierarchy.resolve_scope(user_scope, OrgScope(scope_type="BRANCH", scope_id="branch-c")) is True

    def test_tenant_scope_sees_everything(self) -> None:
        hierarchy = OrgHierarchy(_make_org_with_two_bus())
        user_scope = OrgScope(scope_type="TENANT", scope_id=str(TENANT_ID))
        assert hierarchy.resolve_scope(user_scope, OrgScope(scope_type="BRANCH", scope_id="branch-c")) is True

    def test_bu_scope_does_not_see_other_bu(self) -> None:
        hierarchy = OrgHierarchy(_make_org_with_two_bus())
        user_scope = OrgScope(scope_type="BUSINESS_UNIT", scope_id="bu-1")
        assert hierarchy.resolve_scope(user_scope, OrgScope(scope_type="BUSINESS_UNIT", scope_id="bu-2")) is False
        assert hierarchy.resolve_scope(user_scope, OrgScope(scope_type="BRANCH", scope_id="branch-c")) is False

    def test_bu_for_branch(self) -> None:
        hierarchy = OrgHierarchy(_make_org_with_two_bus())
        assert hierarchy.bu_for_branch("branch-a") == "bu-1"
        assert hierarchy.bu_for_branch("branch-c") == "bu-2"
        assert hierarchy.bu_for_branch("no-such-branch") is None


class TestOrgService:
    def test_create_organization_and_hierarchy(self) -> None:
        repo = _FakeOrganizationRepository()
        service = OrgService(repo)

        org = service.create_organization(TENANT_ID, "Acme Collections Pvt Ltd")
        bu = service.add_business_unit(TENANT_ID, org.org_id, "Retail Collections")
        branch = service.add_branch(TENANT_ID, bu.bu_id, "Mumbai South", city="Mumbai")

        hierarchy = service.get_hierarchy(TENANT_ID)
        assert hierarchy is not None
        assert hierarchy.resolve_scope(
            OrgScope(scope_type="BUSINESS_UNIT", scope_id=bu.bu_id),
            OrgScope(scope_type="BRANCH", scope_id=branch.branch_id),
        )

    def test_get_hierarchy_none_when_no_organization(self) -> None:
        service = OrgService(_FakeOrganizationRepository())
        assert service.get_hierarchy(TENANT_ID) is None
