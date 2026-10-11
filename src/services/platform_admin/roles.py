"""PlatformRole and the role -> permission mapping (ADR-005 Sec 3).

Structurally parallel to ``src.services.authz.roles`` (V4 Ch6 Sec 6.3), but a
completely separate vocabulary: a PlatformRole is never checked against a
tenant-scoped permission, and a tenant Role is never checked against a
platform-scoped permission. This separation is what makes the Actor Model
(ADR-005 Sec 3) a structural guarantee rather than a documented intention.
"""

from __future__ import annotations

from enum import StrEnum


class PlatformRole(StrEnum):
    """The three VoiceOS platform-owner roles (ADR-005 Sec 3)."""

    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    """Full CRUD on all platform resources: clients, billing, infra, staff, settings."""

    PLATFORM_SUPPORT = "PLATFORM_SUPPORT"
    """Read-only across clients, infrastructure, monitoring, and audit logs."""

    PLATFORM_BILLING_OPS = "PLATFORM_BILLING_OPS"
    """Read-only platform view plus write access to billing/revenue only."""


PERM_PLATFORM_READ_ALL = "platform:read:all"
PERM_PLATFORM_WRITE_CLIENTS = "platform:write:clients"
PERM_PLATFORM_WRITE_BILLING = "platform:write:billing"
PERM_PLATFORM_WRITE_SETTINGS = "platform:write:settings"
PERM_PLATFORM_WRITE_STAFF = "platform:write:staff"
PERM_PLATFORM_READ_AUDIT = "platform:read:audit"
PERM_PLATFORM_READ_INFRA = "platform:read:infra"

PLATFORM_ROLE_PERMISSIONS: dict[PlatformRole, frozenset[str]] = {
    PlatformRole.PLATFORM_ADMIN: frozenset(
        {
            PERM_PLATFORM_READ_ALL,
            PERM_PLATFORM_WRITE_CLIENTS,
            PERM_PLATFORM_WRITE_BILLING,
            PERM_PLATFORM_WRITE_SETTINGS,
            PERM_PLATFORM_WRITE_STAFF,
            PERM_PLATFORM_READ_AUDIT,
            PERM_PLATFORM_READ_INFRA,
        }
    ),
    PlatformRole.PLATFORM_SUPPORT: frozenset(
        {
            PERM_PLATFORM_READ_ALL,
            PERM_PLATFORM_READ_AUDIT,
            PERM_PLATFORM_READ_INFRA,
        }
    ),
    PlatformRole.PLATFORM_BILLING_OPS: frozenset(
        {
            PERM_PLATFORM_READ_ALL,
            PERM_PLATFORM_WRITE_BILLING,
        }
    ),
}
"""PlatformRole -> the frozen set of permissions it holds (ADR-005 Sec 6.1/6.16)."""

PLATFORM_WRITE_PERMISSIONS = frozenset(
    {
        PERM_PLATFORM_WRITE_CLIENTS,
        PERM_PLATFORM_WRITE_BILLING,
        PERM_PLATFORM_WRITE_SETTINGS,
        PERM_PLATFORM_WRITE_STAFF,
    }
)
"""Permissions considered "write" actions for the HTTP-verb-based RBAC check,
mirroring ``src.services.authz.roles.WRITE_PERMISSIONS``."""
