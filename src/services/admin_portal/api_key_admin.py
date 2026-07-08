"""APIKeyAdminController -- API key lifecycle administration (V5 Ch13, Ch16, Sprint-025 Part-3).

Architecture: V5 Ch13 (Administration Portal); V5 Ch16 (API Platform);
V4 Ch12 (API Security -- API Keys).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from src.libs.contracts.models.integration import APIKeyRecord
from src.libs.contracts.primitives import TenantId

if TYPE_CHECKING:
    from src.services.api_platform.api_key_lifecycle import APIKeyLifecycleService


class APIKeyAdminController:
    """API key issuance/rotation/revocation administration (V5 Ch13/Ch16)."""

    def __init__(self, lifecycle_service: APIKeyLifecycleService) -> None:
        self._keys = lifecycle_service

    def list_keys(self, tenant_id: TenantId) -> tuple[APIKeyRecord, ...]:
        return self._keys.list_for_tenant(tenant_id)

    def issue_key(
        self,
        tenant_id: TenantId,
        issued_by: str,
        *,
        role: str = "",
        scopes: tuple[str, ...] = (),
        plan_tier: str = "",
        expires_at: datetime | None = None,
    ) -> tuple[str, APIKeyRecord]:
        """Returns ``(raw_key, record)`` -- the raw key must be shown to the caller now; it
        is never recoverable afterward (only its hash is persisted)."""
        return self._keys.issue(
            tenant_id, role=role, scopes=scopes, plan_tier=plan_tier, expires_at=expires_at, issued_by=issued_by
        )

    def rotate_key(self, tenant_id: TenantId, api_key_id: str, rotated_by: str) -> str:
        return self._keys.rotate(tenant_id, api_key_id, rotated_by)

    def revoke_key(self, tenant_id: TenantId, api_key_id: str, revoked_by: str) -> None:
        self._keys.revoke(tenant_id, api_key_id, revoked_by)


__all__ = ["APIKeyAdminController"]
