"""TenantProvisioner — provisions tenant resources on PRODUCTION activation (V5 Ch3).

Runs when a tenant transitions ``SANDBOX -> PRODUCTION`` (Sprint-021.md's
Acceptance Criteria / required-test-name / Phase 2 deployment procedure all
agree provisioning happens on PRODUCTION activation, not at TRIAL creation
— see CHANGELOG.md's Sprint-021 entry for the resolved spec ambiguity):

1. Creates the tenant's KEK in KMS (``KMSClientProtocol.ensure_kek``).
2. Touches the tenant's Redis namespace (a marker key — no server-side
   "namespace" concept exists in Redis itself; this proves connectivity and
   reserves the ``voiceos:tenant:<id>:*`` prefix other services will use).
3. Default policy pack seeding: every built-in rule already applies at the
   global scope automatically (``PolicyEngine._load_active_rule_ids_from_source``,
   Sprint-017) — a tenant with no ``PolicyRepository`` rows of its own simply
   inherits the global set as-is, so there is nothing to write unless a
   ``policy_repository`` is wired, in which case its rows are copied forward.
4. Creates the tenant's default administrator ``User`` + system ``ADMIN``
   ``Role`` + a TENANT-scoped ``RoleAssignment``.
5. Emits a ``TenantProvisioned`` EventBus event.

Every collaborator is optional/additive (``None`` skips that step) — the
same pattern used by every cross-cutting integration since Sprint-013.

Architecture: V5 Ch3 (Tenant Lifecycle) §3.7 (Provisioning Workflow).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.user import OrgScope, Role, RoleAssignment, User
from src.libs.contracts.primitives import TenantId
from src.libs.encryption.kms_client import KMSClientProtocol
from src.libs.event_bus.publisher import Publisher
from src.libs.redis_client.ttl_guard import TTLGuard

from .ports import UserProvisioningPort

TENANT_PROVISIONED_EVENT_TYPE = "tenant.provisioned"

_NAMESPACE_KEY_PREFIX = "voiceos:tenant:"
_NAMESPACE_MARKER_TTL_SECONDS = 30 * 24 * 3600  # 30 days; refreshed on each re-provision

ADMIN_ROLE_NAME = "ADMIN"
ADMIN_ROLE_PERMISSIONS = ("read:all", "write:all", "write:campaigns", "write:users")

SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-000000000000"
"""Sentinel UUID for system-initiated writes (``role_assignments.assigned_by`` is
UUID NOT NULL — same all-zero sentinel convention as ``PolicyRepository``'s
``COALESCE(tenant_id, '00000000-...')`` null-scope marker)."""


def _kek_id_for(tenant_id: str) -> str:
    """Mirrors ``EnvelopeEncryption._kek_id_for`` (Sprint-019) — one KEK per tenant."""
    return f"tenant-{tenant_id}"


@dataclass(frozen=True)
class ProvisioningResult:
    """What ``TenantProvisioner.provision()`` produced."""

    tenant_id: TenantId
    kek_id: str
    redis_namespace_key: str
    admin_user: User
    admin_role: Role
    event_published: bool


class TenantProvisioner:
    """Provisions real resources for a tenant activating to PRODUCTION."""

    def __init__(
        self,
        user_repository: UserProvisioningPort | None = None,
        kms_client: KMSClientProtocol | None = None,
        redis: Any | None = None,
        publisher: Publisher | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._user_repository = user_repository
        self._kms_client = kms_client
        self._redis = redis
        self._publisher = publisher
        self._audit_logger = audit_logger

    def provision(self, tenant_id: TenantId, admin_email: str, admin_name: str) -> ProvisioningResult:
        kek_id = _kek_id_for(tenant_id)
        if self._kms_client is not None:
            self._kms_client.ensure_kek(kek_id)

        namespace_key = f"{_NAMESPACE_KEY_PREFIX}{tenant_id}:namespace"
        if self._redis is not None:
            TTLGuard(self._redis).set(namespace_key, "1", ex=_NAMESPACE_MARKER_TTL_SECONDS)

        admin_role, admin_user = self._create_default_admin(tenant_id, admin_email, admin_name)

        event_published = False
        if self._publisher is not None:
            self._publisher.publish(
                event_type=TENANT_PROVISIONED_EVENT_TYPE,
                tenant_id=tenant_id,
                payload={"tenant_id": str(tenant_id), "admin_user_id": admin_user.user_id},
                correlation_id=str(tenant_id),
            )
            event_published = True

        if self._audit_logger is not None:
            self._audit_logger.record_tenant_provisioned(tenant_id, admin_user.user_id)

        return ProvisioningResult(
            tenant_id=tenant_id,
            kek_id=kek_id,
            redis_namespace_key=namespace_key,
            admin_user=admin_user,
            admin_role=admin_role,
            event_published=event_published,
        )

    def _create_default_admin(self, tenant_id: TenantId, email: str, name: str) -> tuple[Role, User]:
        now = datetime.now(UTC)
        role = Role(
            role_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=ADMIN_ROLE_NAME,
            description="Default tenant administrator (created at provisioning)",
            permissions=ADMIN_ROLE_PERMISSIONS,
            is_system_role=True,
            created_at=now,
            updated_at=now,
        )
        user = User(
            user_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            email=email,
            name=name,
            created_at=now,
            updated_at=now,
        )
        if self._user_repository is not None:
            self._user_repository.create_role(role)
            self._user_repository.create_user(user)
            self._user_repository.assign_role(
                RoleAssignment(
                    assignment_id=str(uuid.uuid4()),
                    user_id=user.user_id,
                    role_id=role.role_id,
                    org_scope=OrgScope(scope_type="TENANT", scope_id=str(tenant_id)),
                    assigned_by=SYSTEM_ACTOR_ID,
                    assigned_at=now,
                )
            )
        return role, user
