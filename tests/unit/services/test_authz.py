"""Unit tests for the Authorization service (Sprint-018, V4 Ch6)."""

from __future__ import annotations

import pytest

from src.services.auth.models import AuthContext, AuthMethod, AuthorizationDeniedError
from src.services.auth.mtls_enforcer import SERVICE_MESH_TENANT_ID
from src.services.authz.abac_evaluator import ABACEvaluator
from src.services.authz.jit_privilege import JITPrivilege
from src.services.authz.models import AuthorizationOutcome, AuthorizationRequest
from src.services.authz.rbac_engine import RBACEngine
from src.services.authz.roles import Role
from src.services.authz.service import AuthzService
from src.services.authz.tenant_isolation import TenantIsolationGuard, TenantIsolationViolationError


def _auth_context(tenant_id: str = "tenant-1", role: Role = Role.AGENT) -> AuthContext:
    return AuthContext(subject="user-1", tenant_id=tenant_id, role=role.value, auth_method=AuthMethod.JWT)


# ---------------------------------------------------------------------------
# Required named tests (Sprint-018.md)
# ---------------------------------------------------------------------------


class TestRequiredNamedTests:
    def test_rbac_auditor_cannot_write(self) -> None:
        engine = RBACEngine()
        assert engine.check_http_method(Role.AUDITOR, "POST") is False
        assert engine.check_http_method(Role.AUDITOR, "PUT") is False
        assert engine.check_http_method(Role.AUDITOR, "DELETE") is False

    def test_rbac_admin_can_write(self) -> None:
        engine = RBACEngine()
        assert engine.check_http_method(Role.ADMIN, "POST") is True

    def test_tenant_isolation_cross_tenant(self) -> None:
        guard = TenantIsolationGuard()
        auth_context = _auth_context(tenant_id="tenant-A")

        with pytest.raises(TenantIsolationViolationError):
            guard.enforce(auth_context, resource_tenant_id="tenant-B")


# ---------------------------------------------------------------------------
# RBACEngine — additional coverage
# ---------------------------------------------------------------------------


class TestRBACEngine:
    def test_auditor_can_read(self) -> None:
        engine = RBACEngine()
        assert engine.check_http_method(Role.AUDITOR, "GET") is True

    def test_agent_cannot_write(self) -> None:
        engine = RBACEngine()
        assert engine.check_http_method(Role.AGENT, "POST") is False

    def test_supervisor_can_write_campaigns_permission(self) -> None:
        engine = RBACEngine()
        assert engine.check(Role.SUPERVISOR, "write:campaigns") is True
        assert engine.check(Role.AGENT, "write:campaigns") is False

    def test_unknown_role_string_denies(self) -> None:
        engine = RBACEngine()
        assert engine.check("NOT_A_ROLE", "read:all") is False


# ---------------------------------------------------------------------------
# ABACEvaluator
# ---------------------------------------------------------------------------


class TestABACEvaluator:
    def test_matching_branch_permits(self) -> None:
        evaluator = ABACEvaluator()
        assert evaluator.evaluate({"branch_id": "b1"}, {"branch_id": "b1"}) is True

    def test_mismatched_branch_denies(self) -> None:
        evaluator = ABACEvaluator()
        assert evaluator.evaluate({"branch_id": "b1"}, {"branch_id": "b2"}) is False

    def test_unscoped_resource_permits(self) -> None:
        evaluator = ABACEvaluator()
        assert evaluator.evaluate({}, {}) is True


# ---------------------------------------------------------------------------
# TenantIsolationGuard
# ---------------------------------------------------------------------------


class TestTenantIsolationGuard:
    def test_same_tenant_permits(self) -> None:
        guard = TenantIsolationGuard()
        guard.enforce(_auth_context(tenant_id="tenant-1"), resource_tenant_id="tenant-1")  # no raise

    def test_service_mesh_identity_exempt(self) -> None:
        guard = TenantIsolationGuard()
        service_identity = AuthContext(
            subject="policy-engine", tenant_id=SERVICE_MESH_TENANT_ID, role="service", auth_method=AuthMethod.MTLS
        )
        guard.enforce(service_identity, resource_tenant_id="any-tenant")  # no raise


# ---------------------------------------------------------------------------
# JITPrivilege
# ---------------------------------------------------------------------------


class TestJITPrivilege:
    def test_grant_requires_minimum_approvers(self) -> None:
        jit = JITPrivilege(required_approvals=2)
        with pytest.raises(AuthorizationDeniedError):
            jit.grant(subject="agent-1", role=Role.SUPERVISOR, reason="incident", approvers=("supervisor-1",))

    def test_grant_succeeds_with_enough_approvers(self) -> None:
        jit = JITPrivilege(required_approvals=2)
        grant = jit.grant(
            subject="agent-1",
            role=Role.SUPERVISOR,
            reason="incident",
            approvers=("supervisor-1", "supervisor-2"),
        )
        assert jit.is_active(grant) is True

    def test_grant_expires_after_ttl(self) -> None:
        import datetime

        jit = JITPrivilege(required_approvals=2)
        granted_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=100)
        grant = jit.grant(
            subject="agent-1",
            role=Role.SUPERVISOR,
            reason="incident",
            approvers=("supervisor-1", "supervisor-2"),
            ttl_seconds=10,
            granted_at=granted_at,
        )
        assert jit.is_active(grant) is False


# ---------------------------------------------------------------------------
# AuthzService — full authorize() pipeline
# ---------------------------------------------------------------------------


class TestAuthzService:
    def test_cross_tenant_raises_before_rbac(self) -> None:
        service = AuthzService()
        request = AuthorizationRequest(
            auth_context=_auth_context(tenant_id="tenant-A", role=Role.ADMIN),
            action="POST",
            resource_tenant_id="tenant-B",
        )
        with pytest.raises(TenantIsolationViolationError):
            service.authorize(request)

    def test_auditor_denied_on_write(self) -> None:
        service = AuthzService()
        request = AuthorizationRequest(
            auth_context=_auth_context(tenant_id="tenant-1", role=Role.AUDITOR),
            action="DELETE",
            resource_tenant_id="tenant-1",
        )
        result = service.authorize(request)
        assert result.outcome == AuthorizationOutcome.DENY

    def test_admin_permitted_on_write(self) -> None:
        service = AuthzService()
        request = AuthorizationRequest(
            auth_context=_auth_context(tenant_id="tenant-1", role=Role.ADMIN),
            action="POST",
            resource_tenant_id="tenant-1",
        )
        result = service.authorize(request)
        assert result.outcome == AuthorizationOutcome.PERMIT

    def test_abac_scope_mismatch_denies(self) -> None:
        service = AuthzService()
        request = AuthorizationRequest(
            auth_context=_auth_context(tenant_id="tenant-1", role=Role.ADMIN),
            action="GET",
            resource_tenant_id="tenant-1",
            subject_attributes={"branch_id": "b1"},
            resource_attributes={"branch_id": "b2"},
        )
        result = service.authorize(request)
        assert result.outcome == AuthorizationOutcome.DENY
