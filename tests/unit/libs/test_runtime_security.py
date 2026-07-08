"""Unit tests for src/libs/runtime_security/ (V4 Ch13)."""

from __future__ import annotations

from src.libs.runtime_security.container_policy import ContainerSecurityPolicy
from src.libs.runtime_security.network_policy import NetworkPolicy


class TestContainerSecurityPolicy:
    def test_privileged_container_is_a_violation(self) -> None:
        policy = ContainerSecurityPolicy()

        violations = policy.validate(
            {"privileged": True, "readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False}
        )

        assert any("privileged" in violation for violation in violations)

    def test_compliant_container_has_no_violations(self) -> None:
        policy = ContainerSecurityPolicy()

        violations = policy.validate(
            {
                "privileged": False,
                "readOnlyRootFilesystem": True,
                "runAsUser": 1000,
                "allowPrivilegeEscalation": False,
            }
        )

        assert violations == []

    def test_to_security_context_matches_policy(self) -> None:
        policy = ContainerSecurityPolicy()

        context = policy.to_security_context()

        assert context["readOnlyRootFilesystem"] is True
        assert context["capabilities"] == {"drop": ["ALL"]}


class TestNetworkPolicy:
    def test_default_deny_unless_allowlisted(self) -> None:
        policy = NetworkPolicy()

        assert policy.is_allowed("conversation_engine", "policy_engine", 8080) is False

        policy.allow("conversation_engine", "policy_engine", 8080)

        assert policy.is_allowed("conversation_engine", "policy_engine", 8080) is True

    def test_manifest_only_includes_rules_for_target_service(self) -> None:
        policy = NetworkPolicy()
        policy.allow("conversation_engine", "policy_engine", 8080)
        policy.allow("conversation_engine", "audit_service", 9090)

        manifest = policy.to_manifest("policy_engine")

        assert len(manifest["spec"]["ingress"]) == 1
