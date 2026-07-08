"""APIKeyLifecycleService -- issuance, rotation, revocation for Public API keys
(V5 Ch16, V4 Ch12, Sprint-025 Part-3).

Every operation is audit-logged (``AuditLogger.record_api_key_operation`` for
issue/revoke, matching the mandatory-coverage ``api_key.issued``/
``api_key.revoked`` action codes already defined in ``src.libs.audit.event``;
a plain ``api_key.rotated`` action for rotation, which has no dedicated
mandatory-coverage constant). Tenant ownership is enforced the same way
every other tenant-scoped repository does (AR-8): every method takes
``tenant_id`` and the underlying ``APIKeyRepository`` mechanically scopes to it.

Architecture: V5 Ch16 (API Platform); V4 Ch12 (API Security -- API Keys).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from src.libs.contracts.models.integration import APIKeyRecord
from src.libs.contracts.primitives import TenantId

from ..auth.api_key_validator import APIKeyValidator

if TYPE_CHECKING:
    from src.libs.audit.logger import AuditLogger

_RAW_KEY_BYTES = 32
_ROTATED_ACTION = "api_key.rotated"


class APIKeyNotFoundError(LookupError):
    """Raised when an operation targets an ``api_key_id`` that doesn't exist for the tenant."""


class APIKeyRepositoryPort(Protocol):
    def create(self, record: APIKeyRecord) -> APIKeyRecord: ...
    def list_for_tenant(self, tenant_id: TenantId) -> tuple[APIKeyRecord, ...]: ...
    def revoke(self, tenant_id: TenantId, api_key_id: str, revoked_at: Any) -> int: ...
    def rotate(self, tenant_id: TenantId, api_key_id: str, new_key_hash: str) -> int: ...


class APIKeyLifecycleService:
    """Issues, rotates, and revokes Public API keys (Sprint-025 Part-3: API key lifecycle)."""

    def __init__(self, repository: APIKeyRepositoryPort, audit_logger: AuditLogger | None = None) -> None:
        self._repo = repository
        self._audit = audit_logger

    def issue(
        self,
        tenant_id: TenantId,
        *,
        role: str = "",
        scopes: tuple[str, ...] = (),
        plan_tier: str = "",
        expires_at: datetime | None = None,
        issued_by: str = "",
    ) -> tuple[str, APIKeyRecord]:
        """Generate + persist a new API key.

        Returns ``(raw_key, record)`` -- the raw key is shown to the caller
        exactly once; only its SHA-256 hash (``APIKeyValidator.hash_key``) is
        ever persisted (secure hashing, storage).
        """
        raw_key = secrets.token_urlsafe(_RAW_KEY_BYTES)
        record = APIKeyRecord(
            api_key_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            key_hash=APIKeyValidator.hash_key(raw_key),
            role=role,
            scopes=scopes,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
            plan_tier=plan_tier,
        )
        self._repo.create(record)
        if self._audit is not None:
            self._audit.record_api_key_operation(tenant_id, issued_by, record.api_key_id)
        return raw_key, record

    def rotate(self, tenant_id: TenantId, api_key_id: str, rotated_by: str = "") -> str:
        """Replace an existing key's credential material with a freshly generated one.

        Returns the new raw key (shown once). Tenant ownership, scopes, and
        plan association on the ``api_key_id`` row are preserved unchanged.
        """
        raw_key = secrets.token_urlsafe(_RAW_KEY_BYTES)
        if self._repo.rotate(tenant_id, api_key_id, APIKeyValidator.hash_key(raw_key)) == 0:
            raise APIKeyNotFoundError(api_key_id)
        if self._audit is not None:
            self._audit.record(tenant_id, rotated_by, _ROTATED_ACTION, "APIKey", api_key_id, "SUCCESS")
        return raw_key

    def revoke(self, tenant_id: TenantId, api_key_id: str, revoked_by: str = "") -> None:
        if self._repo.revoke(tenant_id, api_key_id, datetime.now(UTC)) == 0:
            raise APIKeyNotFoundError(api_key_id)
        if self._audit is not None:
            self._audit.record_api_key_operation(tenant_id, revoked_by, api_key_id, revoked=True)

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[APIKeyRecord, ...]:
        return self._repo.list_for_tenant(tenant_id)


__all__ = ["APIKeyLifecycleService", "APIKeyNotFoundError", "APIKeyRepositoryPort"]
