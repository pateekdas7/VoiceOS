"""TenantService — CRUD + lifecycle operations façade (V5 Ch2/Ch3).

The single entry point tying together ``TenantLifecycle`` (state machine),
``TenantProvisioner`` (real-resource provisioning on PRODUCTION activation),
``IsolationProfileManager`` (isolation-tier DDL), ``TenantSuspender``, and
``TenantDeleter`` — mirroring the ``PolicyEngineService``/``AuthzService``
in-process-façade precedent (no standalone HTTP/gRPC listener exists yet;
see CPU_NODE_STATE.md §8.1 and every service since Sprint-013).

Architecture: V5 Ch2 (Multi-Tenant Architecture); V5 Ch3 (Tenant Lifecycle).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.tenant import IsolationProfile, Tenant, TenantStatus
from src.libs.contracts.primitives import TenantId

from .deletion import TenantDeleter
from .isolation import IsolationProfileManager
from .lifecycle import TenantLifecycle
from .ports import TenantRepositoryPort
from .provisioner import ProvisioningResult, TenantProvisioner
from .suspension import TenantSuspender


class TenantService:
    """CRUD + lifecycle-transition operations over ``tenants``."""

    def __init__(
        self,
        tenant_repository: TenantRepositoryPort,
        provisioner: TenantProvisioner | None = None,
        isolation_manager: IsolationProfileManager | None = None,
        suspender: TenantSuspender | None = None,
        deleter: TenantDeleter | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._repo = tenant_repository
        self._provisioner = provisioner or TenantProvisioner()
        self._isolation_manager = isolation_manager or IsolationProfileManager()
        self._suspender = suspender or TenantSuspender(tenant_repository, audit_logger)
        self._deleter = deleter or TenantDeleter(tenant_repository, audit_logger=audit_logger)
        self._audit_logger = audit_logger

    # ------------------------------------------------------------------
    # Creation (TRIAL — a lightweight DB row, no provisioning yet)
    # ------------------------------------------------------------------

    def create_tenant(
        self,
        slug: str,
        display_name: str,
        subscription_tier: str,
        isolation_profile: IsolationProfile = IsolationProfile.SHARED,
    ) -> Tenant:
        now = datetime.now(UTC)
        tenant = Tenant(
            tenant_id=TenantId(str(uuid.uuid4())),
            slug=slug,
            display_name=display_name,
            subscription_tier=subscription_tier,
            isolation_profile=isolation_profile,
            status=TenantStatus.TRIAL,
            created_at=now,
            updated_at=now,
        )
        self._repo.create(tenant)
        return tenant

    def get(self, tenant_id: TenantId) -> Tenant | None:
        return self._repo.get(tenant_id)

    def get_by_slug(self, slug: str) -> Tenant | None:
        return self._repo.get_by_slug(slug)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def move_to_sandbox(self, tenant_id: TenantId, actor_id: str = "system") -> Tenant:
        return self._apply_transition(tenant_id, TenantStatus.SANDBOX, actor_id, "TRIAL->SANDBOX")

    def activate_production(
        self,
        tenant_id: TenantId,
        admin_email: str,
        admin_name: str,
        actor_id: str = "system",
    ) -> tuple[Tenant, ProvisioningResult]:
        """SANDBOX -> PRODUCTION: transitions the tenant, then runs full provisioning.

        Provisioning (KEK, Redis namespace, policy seed, default admin,
        ``TenantProvisioned`` event) runs *after* the status transition
        succeeds — a failed transition never leaves partially-provisioned
        resources behind.
        """
        tenant = self._apply_transition(
            tenant_id, TenantStatus.PRODUCTION, actor_id, "SANDBOX->PRODUCTION", activated_at=datetime.now(UTC)
        )
        result = self._provisioner.provision(tenant_id, admin_email, admin_name)
        return tenant, result

    def suspend(self, tenant_id: TenantId, actor_id: str = "system") -> Tenant:
        self._suspender.suspend(tenant_id, actor_id)
        return self._require(tenant_id)

    def reactivate(self, tenant_id: TenantId, actor_id: str = "system") -> Tenant:
        self._suspender.reactivate(tenant_id, actor_id)
        return self._require(tenant_id)

    def cancel(self, tenant_id: TenantId, actor_id: str = "system") -> Tenant:
        return self._apply_transition(tenant_id, TenantStatus.CANCELLED, actor_id, "SUSPENDED->CANCELLED")

    def begin_deletion(self, tenant_id: TenantId, actor_id: str = "system") -> Tenant:
        self._deleter.begin_deletion(tenant_id, actor_id)
        return self._require(tenant_id)

    def complete_deletion(self, tenant_id: TenantId, actor_id: str = "system") -> Tenant:
        self._deleter.complete_deletion(tenant_id, actor_id)
        return self._require(tenant_id)

    def is_call_admission_allowed(self, tenant_id: TenantId) -> bool:
        return self._suspender.is_call_admission_allowed(tenant_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_transition(
        self,
        tenant_id: TenantId,
        target: TenantStatus,
        actor_id: str,
        transition_label: str,
        *,
        activated_at: datetime | None = None,
    ) -> Tenant:
        tenant = self._require(tenant_id)
        new_status = TenantLifecycle.transition(tenant.status, target)
        self._repo.update_status(tenant_id, new_status, activated_at=activated_at)
        if self._audit_logger is not None:
            self._audit_logger.record_tenant_lifecycle(tenant_id, actor_id, transition_label)
        return self._require(tenant_id)

    def _require(self, tenant_id: TenantId) -> Tenant:
        tenant = self._repo.get(tenant_id)
        if tenant is None:
            raise ValueError(f"tenant not found: {tenant_id}")
        return tenant
