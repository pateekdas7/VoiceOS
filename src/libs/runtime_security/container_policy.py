"""ContainerSecurityPolicy — no privileged containers, read-only rootfs (V4 Ch13).

Declarative policy + validator, consumed both as a unit-testable guard and
(from Sprint-026 onward) as the source of the pod ``securityContext`` in the
real K8s/Helm manifests — no standalone K8s deployment exists before
Sprint-026 (see ``src/services/policy_engine/service.py``'s docstring for
the established precedent), so this module is the policy definition ahead
of that infrastructure landing.

Architecture: V4 Ch13 (Runtime Security).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContainerSecurityPolicy:
    """The mandatory container hardening baseline for every VoiceOS container."""

    privileged: bool = False
    read_only_root_filesystem: bool = True
    run_as_non_root: bool = True
    allow_privilege_escalation: bool = False
    drop_capabilities: tuple[str, ...] = field(default_factory=lambda: ("ALL",))

    def validate(self, container_spec: dict[str, Any]) -> list[str]:
        """Return a list of violations of this policy found in ``container_spec``.

        Empty list means the spec complies. ``container_spec`` uses the same
        key names as a Kubernetes container ``securityContext``.
        """
        violations: list[str] = []

        if container_spec.get("privileged", False) and not self.privileged:
            violations.append("privileged containers are not permitted")

        if self.read_only_root_filesystem and not container_spec.get("readOnlyRootFilesystem", False):
            violations.append("container must set readOnlyRootFilesystem=true")

        if self.run_as_non_root and container_spec.get("runAsUser") == 0:
            violations.append("container must not run as root (uid 0)")

        if not self.allow_privilege_escalation and container_spec.get("allowPrivilegeEscalation", True):
            violations.append("allowPrivilegeEscalation must be false")

        return violations

    def to_security_context(self) -> dict[str, Any]:
        """This policy expressed as a Kubernetes ``securityContext`` dict."""
        return {
            "privileged": self.privileged,
            "readOnlyRootFilesystem": self.read_only_root_filesystem,
            "runAsNonRoot": self.run_as_non_root,
            "allowPrivilegeEscalation": self.allow_privilege_escalation,
            "capabilities": {"drop": list(self.drop_capabilities)},
        }
