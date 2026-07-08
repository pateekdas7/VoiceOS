"""UserAdminController -- user CRUD, role assignment, SSO config (V5 Ch13).

Architecture: V5 Ch13 (Administration Portal); V5 Ch8 (User Management).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.libs.contracts.models.user import OrgScope, User
from src.libs.contracts.primitives import TenantId
from src.services.user_management.invitation import IssuedInvitation

if TYPE_CHECKING:
    from src.services.user_management.service import UserService
    from src.services.user_management.sso_stub import SSOIntegration

VALID_SSO_PROVIDERS = frozenset({"NONE", "SAML", "OIDC"})
"""Matches the ``sso_config.provider`` CHECK constraint (migration 0018) -- a protocol,
not a vendor name (e.g. Okta/Auth0 are OIDC providers, not values of this field)."""


class InvalidSSOProviderError(ValueError):
    """Raised by ``configure_sso`` when ``provider`` is not one of :data:`VALID_SSO_PROVIDERS`."""


class UserAdminController:
    """User CRUD + role/invitation/SSO-config administration, scoped to the tenant (V5 Ch13).

    ``configure_sso`` delegates to :class:`SSOIntegration` (Sprint-021's
    documented stub), which records configuration *intent* in the
    ``sso_config`` table without performing a real SAML/OIDC handshake —
    that stub's own module docstring names this sprint as where it should
    be plugged in.
    """

    def __init__(self, user_service: UserService, sso: SSOIntegration | None = None) -> None:
        self._users = user_service
        self._sso = sso

    def list_users(self, tenant_id: TenantId) -> tuple[User, ...]:
        return self._users.list_users(tenant_id)

    def get_user(self, tenant_id: TenantId, user_id: str) -> User | None:
        return self._users.get(tenant_id, user_id)

    def invite_user(
        self, tenant_id: TenantId, email: str, role_id: str, scope_type: str, invited_by: str, scope_id: str = ""
    ) -> IssuedInvitation:
        org_scope = OrgScope(scope_type=scope_type, scope_id=scope_id or str(tenant_id))
        return self._users.invite(tenant_id, email, role_id, org_scope, invited_by)

    def deactivate_user(self, tenant_id: TenantId, user_id: str) -> User:
        return self._users.deactivate(tenant_id, user_id)

    def configure_sso(self, tenant_id: TenantId, provider: str, config: dict[str, Any]) -> None:
        """Persist SSO configuration intent for ``tenant_id``.

        Raises :class:`InvalidSSOProviderError` for a ``provider`` outside
        :data:`VALID_SSO_PROVIDERS` (validated here rather than surfacing a raw
        ``psycopg2.errors.CheckViolation`` from the ``sso_config`` CHECK constraint),
        or :class:`SSONotConfiguredError` if no SSO backend is wired.
        """
        if provider not in VALID_SSO_PROVIDERS:
            raise InvalidSSOProviderError(f"provider must be one of {sorted(VALID_SSO_PROVIDERS)}, got {provider!r}")
        if self._sso is None:
            raise SSONotConfiguredError("no SSOIntegration backend wired into this UserAdminController")
        self._sso.configure(str(tenant_id), provider, config)


class SSONotConfiguredError(RuntimeError):
    """Raised by ``configure_sso`` when no :class:`SSOIntegration` backend was supplied."""


__all__ = ["VALID_SSO_PROVIDERS", "InvalidSSOProviderError", "SSONotConfiguredError", "UserAdminController"]
