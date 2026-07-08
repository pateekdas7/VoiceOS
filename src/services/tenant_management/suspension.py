"""TenantSuspender — suspend / reactivate a tenant (V5 Ch3).

"Suspension → drain calls + freeze data" (Sprint-021.md): call admission is
gated via ``PolicyEngineService.check_tenant_active()`` (Sprint-021's new
SaaS policy pack) — every call-admission check made anywhere in the system
consults the tenant's current status, so a SUSPENDED tenant's new calls are
rejected at that single enforcement point. There is no live in-flight call
registry yet to "drain" in this codebase (the collections/CRM call-handling
system of record is Sprint-022 scope) — draining existing calls to
completion, once real calls exist, is naturally satisfied by
``check_tenant_active`` denying only *new* admission, never terminating an
already-admitted call. "Freeze data" is the SUSPENDED status itself: every
repository in this codebase already refuses to correlate/read/write data
outside its ``tenant_id`` scope (AR-8), and a suspended tenant is simply
data that authorized actors continue to read (for support/reactivation) but
no new authoritated effect (calls, PTPs, etc.) may be produced against it.

Architecture: V5 Ch3 (Tenant Lifecycle) §3.9 (Suspension Workflow).
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.tenant import Tenant, TenantStatus
from src.libs.contracts.primitives import TenantId

from .lifecycle import TenantLifecycle
from .ports import TenantRepositoryPort


class TenantSuspender:
    """Suspends and reactivates a tenant, transitioning it through ``TenantLifecycle``."""

    def __init__(self, tenant_repository: TenantRepositoryPort, audit_logger: AuditLogger | None = None) -> None:
        self._repo = tenant_repository
        self._audit_logger = audit_logger

    def suspend(self, tenant_id: TenantId, actor_id: str = "system") -> TenantStatus:
        tenant = self._require_tenant(tenant_id)
        target = TenantLifecycle.transition(tenant.status, TenantStatus.SUSPENDED)
        self._repo.update_status(tenant_id, target, suspended_at=datetime.now(UTC))
        if self._audit_logger is not None:
            self._audit_logger.record_tenant_lifecycle(tenant_id, actor_id, "PRODUCTION->SUSPENDED")
        return target

    def reactivate(self, tenant_id: TenantId, actor_id: str = "system") -> TenantStatus:
        tenant = self._require_tenant(tenant_id)
        target = TenantLifecycle.transition(tenant.status, TenantStatus.PRODUCTION)
        self._repo.update_status(tenant_id, target, activated_at=datetime.now(UTC))
        if self._audit_logger is not None:
            self._audit_logger.record_tenant_lifecycle(tenant_id, actor_id, "SUSPENDED->PRODUCTION")
        return target

    def is_call_admission_allowed(self, tenant_id: TenantId) -> bool:
        """Whether ``tenant_id`` may currently admit new calls (PRODUCTION status only)."""
        tenant = self._require_tenant(tenant_id)
        return tenant.status is TenantStatus.PRODUCTION

    def _require_tenant(self, tenant_id: TenantId) -> Tenant:
        tenant = self._repo.get(tenant_id)
        if tenant is None:
            raise ValueError(f"tenant not found: {tenant_id}")
        return tenant
