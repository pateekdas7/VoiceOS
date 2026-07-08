"""OrgHierarchy — traversal and RBAC scope resolution over the org tree (V5 Ch2.4/2.5).

Three levels: ``Organization -> BusinessUnit -> Branch``. A ``RoleAssignment``
(Sprint-018/V4 Ch6) carries an ``OrgScope`` (``scope_type`` in
``TENANT``/``ORG``/``BUSINESS_UNIT``/``BRANCH`` + ``scope_id``) —
``resolve_scope`` answers whether a user assigned at one scope may see a
resource at another: broader scopes see everything beneath them; a scope
never sees a sibling or an unrelated branch/BU.

Architecture: V5 Ch2 (Multi-Tenant Architecture) §2.4/2.5; V4 Ch6 (RBAC).
"""

from __future__ import annotations

from .models import Organization, OrgScope

_TENANT = "TENANT"
_ORG = "ORG"
_BUSINESS_UNIT = "BUSINESS_UNIT"
_BRANCH = "BRANCH"


class OrgHierarchy:
    """Wraps one tenant's ``Organization`` tree for scope-resolution queries."""

    def __init__(self, organization: Organization) -> None:
        self._organization = organization

    def resolve_scope(self, user_scope: OrgScope, resource_scope: OrgScope) -> bool:
        """Whether ``user_scope`` covers ``resource_scope`` (V4 Ch6 §6.3).

        - ``TENANT``/``ORG`` scope sees every resource under this organization.
        - ``BUSINESS_UNIT`` scope sees that BU itself and every branch under it.
        - ``BRANCH`` scope sees only that exact branch — never a sibling
          branch, and never a resource scoped to a different BU.
        """
        if user_scope.scope_type in (_TENANT, _ORG):
            return True

        if user_scope.scope_type == _BUSINESS_UNIT:
            if resource_scope.scope_type == _BUSINESS_UNIT:
                return resource_scope.scope_id == user_scope.scope_id
            if resource_scope.scope_type == _BRANCH:
                return resource_scope.scope_id in self._branch_ids_under_bu(user_scope.scope_id)
            return False

        if user_scope.scope_type == _BRANCH:
            return resource_scope.scope_type == _BRANCH and resource_scope.scope_id == user_scope.scope_id

        return False

    def _branch_ids_under_bu(self, bu_id: str) -> frozenset[str]:
        for bu in self._organization.business_units:
            if bu.bu_id == bu_id:
                return frozenset(branch.branch_id for branch in bu.branches)
        return frozenset()

    def bu_for_branch(self, branch_id: str) -> str | None:
        """The ``bu_id`` owning ``branch_id``, or ``None`` if not found in this tree."""
        for bu in self._organization.business_units:
            if any(branch.branch_id == branch_id for branch in bu.branches):
                return bu.bu_id
        return None
