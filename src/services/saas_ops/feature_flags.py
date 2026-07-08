"""FeatureFlagService -- per-tenant/per-plan/per-cohort flag resolution (V5 Ch23).

Resolution order (Sprint-026.md): global -> plan-level (e.g. ENTERPRISE) ->
cohort (e.g. early-access) -> per-tenant, each successively-scoped row
overriding the wider one if present. ``GRADUAL_ROLLOUT`` resolves via a
deterministic hash of ``(flag_name, tenant_id)`` so the same tenant always
gets the same in/out answer for a given percentage (no per-call flapping).

Resolved answers are cached in Redis for 30s (TTLGuard) when a Redis client
is supplied -- optional, same "None preserves prior behavior" precedent as
every Sprint-016+ cache parameter.

Architecture: V5 Ch23 (SaaS Operations Platform -- Feature Flag System).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from src.libs.contracts.models.saas_ops import FeatureFlag, FeatureFlagScope, FeatureFlagState
from src.libs.contracts.primitives import TenantId
from src.libs.redis_client.ttl_guard import TTLGuard

from . import metrics

_CACHE_TTL_SECONDS = 30


class TenantAttributesPort(Protocol):
    """Resolves a tenant's plan tier / cohort membership, for PLAN/COHORT flag scopes.

    Optional collaborator: when not supplied, ``is_enabled()`` still resolves
    correctly across GLOBAL and TENANT scopes, simply skipping the PLAN/
    COHORT tiers (same graceful-degradation precedent as every other
    optional port in this codebase).
    """

    def plan_tier(self, tenant_id: TenantId) -> str | None: ...
    def cohort(self, tenant_id: TenantId) -> str | None: ...


class FeatureFlagRepositoryPort(Protocol):
    def upsert(self, flag: FeatureFlag) -> FeatureFlag: ...
    def get(self, flag_name: str, scope: FeatureFlagScope, scope_value: str | None) -> FeatureFlag | None: ...
    def list_for_flag(self, flag_name: str) -> tuple[FeatureFlag, ...]: ...


class FeatureFlagService:
    """Create/resolve feature flags across the GLOBAL/PLAN/COHORT/TENANT hierarchy."""

    def __init__(
        self,
        repository: FeatureFlagRepositoryPort,
        redis: Any | None = None,
        tenant_attributes: TenantAttributesPort | None = None,
    ) -> None:
        self._repo = repository
        self._ttl_guard = TTLGuard(redis) if redis is not None else None
        self._redis = redis
        self._tenant_attributes = tenant_attributes

    def set_flag(
        self,
        flag_name: str,
        scope: FeatureFlagScope,
        state: FeatureFlagState,
        *,
        scope_value: str | None = None,
        rollout_percentage: float = 0.0,
    ) -> FeatureFlag:
        """Create or update one targeting row for ``flag_name``."""
        now = datetime.now(UTC)
        existing = self._repo.get(flag_name, scope, scope_value)
        flag = FeatureFlag(
            flag_id=existing.flag_id if existing is not None else str(uuid.uuid4()),
            flag_name=flag_name,
            scope=scope,
            scope_value=scope_value,
            state=state,
            rollout_percentage=rollout_percentage,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._repo.upsert(flag)
        self._invalidate_cache(flag_name, scope_value or "")
        return flag

    def is_enabled(self, flag_name: str, tenant_id: TenantId) -> bool:
        """Resolve whether ``flag_name`` is enabled for ``tenant_id``.

        Precedence (most specific wins): TENANT row > COHORT row > PLAN row >
        GLOBAL row > (no row anywhere) False.
        """
        cache_key = self._cache_key(flag_name, tenant_id)
        if self._redis is not None:
            cached = self._redis.get(cache_key)
            if cached is not None:
                metrics.record_flag_resolution(cache_hit=True)
                return bool(cached == b"1")

        effective: FeatureFlag | None = self._repo.get(flag_name, FeatureFlagScope.GLOBAL, None)

        if self._tenant_attributes is not None:
            plan = self._tenant_attributes.plan_tier(tenant_id)
            if plan is not None:
                plan_row = self._repo.get(flag_name, FeatureFlagScope.PLAN, plan)
                if plan_row is not None:
                    effective = plan_row

            cohort = self._tenant_attributes.cohort(tenant_id)
            if cohort is not None:
                cohort_row = self._repo.get(flag_name, FeatureFlagScope.COHORT, cohort)
                if cohort_row is not None:
                    effective = cohort_row

        tenant_row = self._repo.get(flag_name, FeatureFlagScope.TENANT, str(tenant_id))
        if tenant_row is not None:
            effective = tenant_row

        result = self._resolve_state(effective, flag_name, tenant_id)

        if self._ttl_guard is not None:
            self._ttl_guard.set(cache_key, b"1" if result else b"0", ex=_CACHE_TTL_SECONDS)
        metrics.record_flag_resolution(cache_hit=False)
        return result

    @staticmethod
    def _resolve_state(flag: FeatureFlag | None, flag_name: str, tenant_id: TenantId) -> bool:
        if flag is None or flag.state == FeatureFlagState.DISABLED:
            return False
        if flag.state == FeatureFlagState.ENABLED:
            return True
        # GRADUAL_ROLLOUT: deterministic per-(flag, tenant) bucket in [0, 100).
        digest = hashlib.sha256(f"{flag_name}:{tenant_id}".encode()).hexdigest()
        bucket = int(digest[:8], 16) % 100
        return bucket < flag.rollout_percentage

    def _invalidate_cache(self, flag_name: str, scope_value: str) -> None:
        if self._redis is None:
            return
        # A scoped update can change the effective answer for any tenant
        # matching that scope; the cache entry is keyed per-tenant, so there
        # is no single key to invalidate for a PLAN/COHORT/GLOBAL change.
        # Short 30s TTL (per Sprint-026.md) bounds the staleness window
        # instead -- the same trade-off ModelConfigService documents for its
        # 300s cache.
        if scope_value:
            self._redis.delete(self._cache_key(flag_name, TenantId(scope_value)))

    @staticmethod
    def _cache_key(flag_name: str, tenant_id: TenantId) -> str:
        return f"voiceos:feature_flag:{flag_name}:{tenant_id}"


__all__ = ["FeatureFlagRepositoryPort", "FeatureFlagService", "TenantAttributesPort"]
