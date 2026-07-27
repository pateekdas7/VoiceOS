"""TenantRepository — the top-level tenant record (V5 Ch2/Ch3).

Unlike every other repository in this package, ``tenants`` is not itself
tenant-scoped by a ``tenant_id`` foreign column (it *is* the tenant) — so
this repository does not use ``BaseRepository._tenant_select``; it queries
directly on ``tenant_id``/``slug`` as its own primary/unique keys.

Architecture: V5 Ch2 (Multi-Tenant Architecture); V5 Ch3 (Tenant Lifecycle).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..contracts.models.tenant import IsolationProfile, Tenant, TenantStatus
from ..contracts.primitives import TenantId
from .base import BaseRepository

_TABLE = "tenants"

_COLUMNS = (
    "tenant_id",
    "slug",
    "display_name",
    "subscription_tier",
    "isolation_profile",
    "status",
    "timezone",
    "currency",
    "max_concurrent_calls",
    "feature_flags",
    "created_at",
    "updated_at",
)


class TenantRepository(BaseRepository):
    """CRUD + lifecycle queries over the ``tenants`` table."""

    def create(self, tenant: Tenant) -> Tenant:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                tenant_id, slug, display_name, subscription_tier, isolation_profile,
                status, timezone, currency, max_concurrent_calls, feature_flags,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant.tenant_id,
                tenant.slug,
                tenant.display_name,
                tenant.subscription_tier,
                tenant.isolation_profile.value,
                tenant.status.value,
                tenant.timezone,
                tenant.currency,
                tenant.max_concurrent_calls,
                list(tenant.feature_flags),
                tenant.created_at,
                tenant.updated_at,
            ),
        )
        self._commit()
        return tenant

    def get(self, tenant_id: TenantId) -> Tenant | None:
        cur = self._execute(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
        return self._hydrate(row) if row is not None else None

    def get_by_slug(self, slug: str) -> Tenant | None:
        cur = self._execute(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE slug = %s", (slug,))
        row = cur.fetchone()
        return self._hydrate(row) if row is not None else None

    def list_all(self) -> tuple[Tenant, ...]:
        """Every tenant, platform-wide (ADR-005 Sec 6.1 -- Admin Dashboard "Clients").

        Unscoped by design, same rationale as this class's own docstring:
        ``tenants`` is the isolation root, not a tenant-owned resource, so
        "list every tenant" is inherently a platform-level query, not a
        violation of AR-8 tenant isolation (which governs resources *within*
        a tenant).
        """
        cur = self._execute(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} ORDER BY created_at")
        rows = cur.fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def update_status(
        self,
        tenant_id: TenantId,
        status: TenantStatus,
        *,
        activated_at: datetime | None = None,
        suspended_at: datetime | None = None,
    ) -> int:
        """Persist a lifecycle transition. Returns the affected row count (0 or 1)."""
        set_columns = ["status", "updated_at"]
        set_values: list[Any] = [status.value, datetime.utcnow()]
        if activated_at is not None:
            set_columns.append("activated_at")
            set_values.append(activated_at)
        if suspended_at is not None:
            set_columns.append("suspended_at")
            set_values.append(suspended_at)
        set_clause = ", ".join(f"{col} = %s" for col in set_columns)
        cur = self._execute(
            f"UPDATE {_TABLE} SET {set_clause} WHERE tenant_id = %s",
            (*set_values, tenant_id),
        )
        self._commit()
        rowcount: int = cur.rowcount
        return rowcount

    def _hydrate(self, row: tuple[Any, ...]) -> Tenant:
        (
            tenant_id,
            slug,
            display_name,
            subscription_tier,
            isolation_profile,
            status,
            timezone,
            currency,
            max_concurrent_calls,
            feature_flags,
            created_at,
            updated_at,
        ) = row
        return Tenant(
            tenant_id=TenantId(tenant_id),
            slug=slug,
            display_name=display_name,
            subscription_tier=subscription_tier,
            isolation_profile=IsolationProfile(isolation_profile),
            status=TenantStatus(status),
            timezone=timezone,
            currency=currency,
            max_concurrent_calls=max_concurrent_calls,
            feature_flags=tuple(feature_flags or ()),
            created_at=created_at,
            updated_at=updated_at,
        )
