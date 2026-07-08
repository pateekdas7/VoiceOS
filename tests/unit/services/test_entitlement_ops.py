"""Unit tests for EntitlementOpsService (Sprint-026, V5 Ch23)."""

from __future__ import annotations

from src.libs.contracts.primitives import TenantId
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome
from src.services.saas_ops.entitlement_ops import EntitlementOpsService

_TENANT = TenantId("tenant-a")


class _FakePolicyEngine:
    def __init__(self, outcome: PolicyOutcome) -> None:
        self._outcome = outcome

    def check_entitlement(
        self,
        tenant_id: str,
        feature: str,
        tier: str,
        usage_quantity: int | None = None,
        usage_limit: int | None = None,
        trial_expired: bool = False,
        subject: str = "saas_ops",
    ) -> PolicyDecision:
        return PolicyDecision(outcome=self._outcome, matching_rules=(), reason="fake")


class TestEntitlementOpsService:
    def test_check_license_permissive_when_unwired(self) -> None:
        service = EntitlementOpsService(policy_engine=None)

        assert service.check_license(_TENANT, "GROWTH") is True

    def test_check_license_true_on_permit(self) -> None:
        service = EntitlementOpsService(policy_engine=_FakePolicyEngine(PolicyOutcome.PERMIT))

        assert service.check_license(_TENANT, "GROWTH") is True

    def test_check_license_false_on_deny(self) -> None:
        service = EntitlementOpsService(policy_engine=_FakePolicyEngine(PolicyOutcome.DENY))

        assert service.check_license(_TENANT, "GROWTH", trial_expired=True) is False

    def test_audit_entitlements_reports_reason_on_denial(self) -> None:
        service = EntitlementOpsService(policy_engine=_FakePolicyEngine(PolicyOutcome.DENY))

        result = service.audit_entitlements(_TENANT, "TRIAL", trial_expired=True)

        assert result.within_entitlement is False
        assert result.reason != ""
        assert result.tenant_id == _TENANT

    def test_audit_entitlements_no_reason_when_within(self) -> None:
        service = EntitlementOpsService(policy_engine=_FakePolicyEngine(PolicyOutcome.PERMIT))

        result = service.audit_entitlements(_TENANT, "ENTERPRISE")

        assert result.within_entitlement is True
        assert result.reason == ""
