"""PlatformRBACEngine -- role -> permission mapping and the write/read HTTP-verb
gate for PlatformActors (ADR-005 Sec 3).

Structurally parallel to ``src.services.authz.rbac_engine.RBACEngine`` --
same shape, deliberately not shared code, since a PlatformActor must never
be checkable against tenant-scoped permissions or vice versa (ADR-005 Sec 3).
"""

from __future__ import annotations

from .roles import PLATFORM_ROLE_PERMISSIONS, PLATFORM_WRITE_PERMISSIONS, PlatformRole

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class PlatformRBACEngine:
    """Evaluates whether a PlatformRole holds a given permission (ADR-005 Sec 3)."""

    def check(self, role: PlatformRole | str, permission: str) -> bool:
        """PERMIT (True) iff ``role`` holds ``permission``. Unknown roles DENY."""
        role_enum = self._coerce_role(role)
        if role_enum is None:
            return False
        return permission in PLATFORM_ROLE_PERMISSIONS.get(role_enum, frozenset())

    def check_http_method(self, role: PlatformRole | str, method: str) -> bool:
        """PERMIT (True) iff ``role`` may perform an HTTP ``method``.

        Write-class methods (POST/PUT/PATCH/DELETE) require at least one
        ``platform:write:*`` permission; read-class methods require only
        that the role holds any permission at all. PLATFORM_SUPPORT
        therefore always DENIES on write methods.
        """
        role_enum = self._coerce_role(role)
        if role_enum is None:
            return False
        permissions = PLATFORM_ROLE_PERMISSIONS.get(role_enum, frozenset())
        method_upper = method.upper()
        if method_upper in _WRITE_METHODS:
            return bool(permissions & PLATFORM_WRITE_PERMISSIONS)
        return bool(permissions)

    @staticmethod
    def _coerce_role(role: PlatformRole | str) -> PlatformRole | None:
        if isinstance(role, PlatformRole):
            return role
        try:
            return PlatformRole(role)
        except ValueError:
            return None
