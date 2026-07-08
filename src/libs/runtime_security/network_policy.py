"""NetworkPolicy — default-deny Kubernetes network policies (V4 Ch13).

Default-deny-all with explicit allowlisted service-to-service rules (Zero
Trust — V4 Ch12 §12.12 "no implicit trust between internal services").
Same pre-Sprint-026 posture as ``container_policy.py``: this is the policy
definition + validator, source of the real K8s ``NetworkPolicy`` manifest
once Sprint-026 ships standalone K8s deployments.

Architecture: V4 Ch13 (Runtime Security).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NetworkRule:
    """One allowlisted service-to-service ingress rule."""

    from_service: str
    to_service: str
    port: int


class NetworkPolicy:
    """Default-deny network policy for one namespace, with explicit allow rules."""

    def __init__(self, namespace: str = "voiceos-runtime") -> None:
        self._namespace = namespace
        self._rules: list[NetworkRule] = []

    def allow(self, from_service: str, to_service: str, port: int) -> None:
        """Allowlist ``from_service -> to_service:port`` (otherwise default-deny)."""
        self._rules.append(NetworkRule(from_service, to_service, port))

    def is_allowed(self, from_service: str, to_service: str, port: int) -> bool:
        """Whether this exact triple is allowlisted. Anything not allowlisted is denied."""
        return any(
            rule.from_service == from_service and rule.to_service == to_service and rule.port == port
            for rule in self._rules
        )

    def to_manifest(self, service: str) -> dict[str, Any]:
        """A default-deny-all-ingress Kubernetes ``NetworkPolicy`` manifest for ``service``,
        with this policy's allowlisted rules targeting it as explicit ``ingress`` entries."""
        ingress = [
            {
                "from": [{"podSelector": {"matchLabels": {"app": rule.from_service}}}],
                "ports": [{"protocol": "TCP", "port": rule.port}],
            }
            for rule in self._rules
            if rule.to_service == service
        ]
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": f"{service}-default-deny", "namespace": self._namespace},
            "spec": {
                "podSelector": {"matchLabels": {"app": service}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": ingress,
            },
        }
