"""ABACEvaluator — attribute-based access control, organizational scope
(V4 Ch6 §6.5 "ABAC — org/branch scoping").

Layered on top of RBAC: a role may hold a permission in general (RBAC) yet
still be denied access to a specific resource whose organizational
attributes (business unit, branch) don't match the subject's own scope.

Architecture: V4 Ch6 (Authorization/RBAC — ABAC).
"""

from __future__ import annotations

from typing import Any

DEFAULT_SCOPE_KEYS: tuple[str, ...] = ("business_unit_id", "branch_id")


class ABACEvaluator:
    """Evaluates organizational-scope attribute matches (V4 Ch6 §6.5).

    Args:
        scope_keys: The attribute keys checked for a subject/resource
            match. A resource that does not carry a given key is treated
            as unscoped by that attribute (no denial on that key alone).
    """

    def __init__(self, scope_keys: tuple[str, ...] = DEFAULT_SCOPE_KEYS) -> None:
        self._scope_keys = scope_keys

    def evaluate(self, subject_attributes: dict[str, Any], resource_attributes: dict[str, Any]) -> bool:
        """PERMIT (True) iff every scoped attribute the resource carries
        matches the subject's corresponding attribute."""
        for key in self._scope_keys:
            resource_value = resource_attributes.get(key)
            if resource_value is None:
                continue
            if subject_attributes.get(key) != resource_value:
                return False
        return True
