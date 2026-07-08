"""AuthzService — the RBAC/ABAC authorization check API (V4 Ch6 §6.4).

Every call enforces tenant isolation (AR-8) first, then RBAC, then ABAC —
in that order, so a cross-tenant attempt never reaches the permission
tables at all.

Architecture: V4 Ch6 (Authorization/RBAC).
"""

from __future__ import annotations

from .abac_evaluator import ABACEvaluator
from .models import AuthorizationOutcome, AuthorizationRequest, AuthorizationResult
from .rbac_engine import RBACEngine
from .tenant_isolation import TenantIsolationGuard


class AuthzService:
    """The authorization check API every enforcement point (PEP) consults.

    Args:
        rbac_engine: Role -> permission evaluator. Defaults to a fresh
            :class:`RBACEngine` (the built-in role table, V4 Ch6 §6.3).
        abac_evaluator: Organizational-scope evaluator. Defaults to a fresh
            :class:`ABACEvaluator` (business_unit_id/branch_id scoping).
        tenant_isolation_guard: AR-8 enforcement point. Defaults to a fresh
            :class:`TenantIsolationGuard`.
    """

    def __init__(
        self,
        rbac_engine: RBACEngine | None = None,
        abac_evaluator: ABACEvaluator | None = None,
        tenant_isolation_guard: TenantIsolationGuard | None = None,
    ) -> None:
        self._rbac = rbac_engine or RBACEngine()
        self._abac = abac_evaluator or ABACEvaluator()
        self._tenant_guard = tenant_isolation_guard or TenantIsolationGuard()

    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        """Evaluate ``request`` under tenant-isolation -> RBAC -> ABAC, in order.

        Raises:
            TenantIsolationViolationError: Cross-tenant access attempted (AR-8).
                Never returns a DENY result for this case — it always raises,
                so callers cannot accidentally treat it as an ordinary denial.
        """
        self._tenant_guard.enforce(request.auth_context, request.resource_tenant_id)

        if not self._rbac.check_http_method(request.auth_context.role, request.action):
            return AuthorizationResult(
                outcome=AuthorizationOutcome.DENY,
                reason=f"role '{request.auth_context.role}' lacks permission for action '{request.action}'",
            )

        if not self._abac.evaluate(request.subject_attributes, request.resource_attributes):
            return AuthorizationResult(
                outcome=AuthorizationOutcome.DENY,
                reason="resource organizational scope does not match subject scope (ABAC)",
            )

        return AuthorizationResult(outcome=AuthorizationOutcome.PERMIT)
