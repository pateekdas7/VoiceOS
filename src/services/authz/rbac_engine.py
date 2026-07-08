"""RBACEngine — role → permission mapping and the write/read HTTP-verb gate
(V4 Ch6 §6.4).

Architecture: V4 Ch6 (Authorization/RBAC).
"""

from __future__ import annotations

from .roles import ROLE_PERMISSIONS, WRITE_PERMISSIONS, Role

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RBACEngine:
    """Evaluates whether a role holds a given permission (V4 Ch6 §6.4)."""

    def check(self, role: Role | str, permission: str) -> bool:
        """PERMIT (True) iff ``role`` holds ``permission``. Unknown roles DENY."""
        role_enum = self._coerce_role(role)
        if role_enum is None:
            return False
        return permission in ROLE_PERMISSIONS.get(role_enum, frozenset())

    def check_http_method(self, role: Role | str, method: str) -> bool:
        """PERMIT (True) iff ``role`` may perform an HTTP ``method``.

        Write-class methods (POST/PUT/PATCH/DELETE) require at least one
        ``write:*`` permission; read-class methods require only that the
        role holds any permission at all. AUDITOR/AGENT therefore always
        DENY on write methods regardless of what they can read.
        """
        role_enum = self._coerce_role(role)
        if role_enum is None:
            return False
        permissions = ROLE_PERMISSIONS.get(role_enum, frozenset())
        method_upper = method.upper()
        if method_upper in _WRITE_METHODS:
            return bool(permissions & WRITE_PERMISSIONS)
        return bool(permissions)

    @staticmethod
    def _coerce_role(role: Role | str) -> Role | None:
        if isinstance(role, Role):
            return role
        try:
            return Role(role)
        except ValueError:
            return None
