"""TenantDeleter — async tenant deletion with crypto-shredding (V5 Ch3; V4 Ch8/Ch9).

Deletion crypto-shreds the tenant's entire KEK in one step
(``KMSClientProtocol.destroy_kek(f"tenant-{tenant_id}")``) rather than
shredding one DEK at a time (``CryptoShredder``, Sprint-019, is a per-record
operation) — destroying the KEK makes every DEK it ever wrapped permanently
unrecoverable in a single call, which is the correct semantics for "delete
this entire tenant," not a loop over every record it ever had.

Architecture: V5 Ch3 (Tenant Lifecycle) §3.10 (Deletion Workflow); V4 Ch8
§8.12 (crypto-shredding).
"""

from __future__ import annotations

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.tenant import TenantStatus
from src.libs.contracts.primitives import TenantId
from src.libs.encryption.kms_client import KMSClientProtocol

from .lifecycle import TenantLifecycle
from .ports import TenantRepositoryPort


def _kek_id_for(tenant_id: str) -> str:
    return f"tenant-{tenant_id}"


class TenantDeleter:
    """Drives a tenant through CANCELLED -> DELETING -> DELETED with crypto-shredding."""

    def __init__(
        self,
        tenant_repository: TenantRepositoryPort,
        kms_client: KMSClientProtocol | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._repo = tenant_repository
        self._kms_client = kms_client
        self._audit_logger = audit_logger

    def begin_deletion(self, tenant_id: TenantId, actor_id: str = "system") -> TenantStatus:
        """CANCELLED -> DELETING: starts the async deletion job."""
        tenant = self._repo.get(tenant_id)
        if tenant is None:
            raise ValueError(f"tenant not found: {tenant_id}")
        target = TenantLifecycle.transition(tenant.status, TenantStatus.DELETING)
        self._repo.update_status(tenant_id, target)
        if self._audit_logger is not None:
            self._audit_logger.record_tenant_lifecycle(tenant_id, actor_id, "CANCELLED->DELETING")
        return target

    def complete_deletion(self, tenant_id: TenantId, actor_id: str = "system") -> TenantStatus:
        """DELETING -> DELETED: crypto-shreds the tenant KEK and finalizes the record."""
        tenant = self._repo.get(tenant_id)
        if tenant is None:
            raise ValueError(f"tenant not found: {tenant_id}")
        target = TenantLifecycle.transition(tenant.status, TenantStatus.DELETED)

        if self._kms_client is not None:
            self._kms_client.destroy_kek(_kek_id_for(tenant_id))

        self._repo.update_status(tenant_id, target)
        if self._audit_logger is not None:
            self._audit_logger.record_tenant_lifecycle(tenant_id, actor_id, "DELETING->DELETED")
        return target
