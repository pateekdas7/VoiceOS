"""Unit tests for FeatureFlagService (Sprint-026, V5 Ch23).

All tests run fully in-process -- no live Postgres/Redis required (Phase 1).
Repository interactions use a small in-memory fake double, mirroring the
``_Fake*Repository`` precedent from ``test_ai_config.py``/``test_billing.py``.

Required named test (Sprint-026.md acceptance criteria):
    FeatureFlagService.is_enabled() returns correct value based on tenant
    ring/scope assignment: flag=ENABLED for a tenant -> True; flag=DISABLED -> False.
"""

from __future__ import annotations

from src.libs.contracts.models.saas_ops import FeatureFlag, FeatureFlagScope, FeatureFlagState
from src.libs.contracts.primitives import TenantId
from src.services.saas_ops.feature_flags import FeatureFlagService
from tests.fixtures.redis import FakeRedisClient

_TENANT_A = TenantId("tenant-a")
_TENANT_B = TenantId("tenant-b")


class _FakeFeatureFlagRepository:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, FeatureFlagScope, str | None], FeatureFlag] = {}

    def upsert(self, flag: FeatureFlag) -> FeatureFlag:
        self._rows[(flag.flag_name, flag.scope, flag.scope_value)] = flag
        return flag

    def get(self, flag_name: str, scope: FeatureFlagScope, scope_value: str | None) -> FeatureFlag | None:
        return self._rows.get((flag_name, scope, scope_value))

    def list_for_flag(self, flag_name: str) -> tuple[FeatureFlag, ...]:
        return tuple(f for (name, _s, _v), f in self._rows.items() if name == flag_name)


class _FakeTenantAttributes:
    def __init__(self, plans: dict[TenantId, str] | None = None, cohorts: dict[TenantId, str] | None = None) -> None:
        self._plans = plans or {}
        self._cohorts = cohorts or {}

    def plan_tier(self, tenant_id: TenantId) -> str | None:
        return self._plans.get(tenant_id)

    def cohort(self, tenant_id: TenantId) -> str | None:
        return self._cohorts.get(tenant_id)


class TestFeatureFlagService:
    def test_is_enabled_true_when_flag_enabled(self) -> None:
        repo = _FakeFeatureFlagRepository()
        service = FeatureFlagService(repo)
        service.set_flag("new_negotiation_strategy", FeatureFlagScope.GLOBAL, FeatureFlagState.ENABLED)

        assert service.is_enabled("new_negotiation_strategy", _TENANT_A) is True

    def test_is_enabled_false_when_flag_disabled(self) -> None:
        repo = _FakeFeatureFlagRepository()
        service = FeatureFlagService(repo)
        service.set_flag("new_negotiation_strategy", FeatureFlagScope.GLOBAL, FeatureFlagState.DISABLED)

        assert service.is_enabled("new_negotiation_strategy", _TENANT_A) is False

    def test_is_enabled_false_when_flag_never_created(self) -> None:
        repo = _FakeFeatureFlagRepository()
        service = FeatureFlagService(repo)

        assert service.is_enabled("nonexistent_flag", _TENANT_A) is False

    def test_tenant_scope_overrides_global(self) -> None:
        repo = _FakeFeatureFlagRepository()
        service = FeatureFlagService(repo)
        service.set_flag("beta_feature", FeatureFlagScope.GLOBAL, FeatureFlagState.DISABLED)
        service.set_flag("beta_feature", FeatureFlagScope.TENANT, FeatureFlagState.ENABLED, scope_value=str(_TENANT_A))

        assert service.is_enabled("beta_feature", _TENANT_A) is True
        assert service.is_enabled("beta_feature", _TENANT_B) is False

    def test_plan_scope_overrides_global_but_not_tenant(self) -> None:
        repo = _FakeFeatureFlagRepository()
        attrs = _FakeTenantAttributes(plans={_TENANT_A: "ENTERPRISE", _TENANT_B: "ENTERPRISE"})
        service = FeatureFlagService(repo, tenant_attributes=attrs)
        service.set_flag("enterprise_only", FeatureFlagScope.GLOBAL, FeatureFlagState.DISABLED)
        service.set_flag("enterprise_only", FeatureFlagScope.PLAN, FeatureFlagState.ENABLED, scope_value="ENTERPRISE")
        service.set_flag(
            "enterprise_only", FeatureFlagScope.TENANT, FeatureFlagState.DISABLED, scope_value=str(_TENANT_B)
        )

        assert service.is_enabled("enterprise_only", _TENANT_A) is True  # plan override applies
        assert service.is_enabled("enterprise_only", _TENANT_B) is False  # tenant override wins over plan

    def test_cohort_scope_overrides_plan(self) -> None:
        repo = _FakeFeatureFlagRepository()
        attrs = _FakeTenantAttributes(plans={_TENANT_A: "GROWTH"}, cohorts={_TENANT_A: "early-access"})
        service = FeatureFlagService(repo, tenant_attributes=attrs)
        service.set_flag("cohort_feature", FeatureFlagScope.PLAN, FeatureFlagState.DISABLED, scope_value="GROWTH")
        service.set_flag(
            "cohort_feature", FeatureFlagScope.COHORT, FeatureFlagState.ENABLED, scope_value="early-access"
        )

        assert service.is_enabled("cohort_feature", _TENANT_A) is True

    def test_gradual_rollout_is_deterministic_per_tenant(self) -> None:
        repo = _FakeFeatureFlagRepository()
        service = FeatureFlagService(repo)
        service.set_flag(
            "gradual_feature", FeatureFlagScope.GLOBAL, FeatureFlagState.GRADUAL_ROLLOUT, rollout_percentage=50.0
        )

        first = service.is_enabled("gradual_feature", _TENANT_A)
        second = service.is_enabled("gradual_feature", _TENANT_A)
        assert first == second  # same tenant, same flag -> stable answer every call

    def test_gradual_rollout_zero_percent_always_false(self) -> None:
        repo = _FakeFeatureFlagRepository()
        service = FeatureFlagService(repo)
        service.set_flag(
            "gradual_feature", FeatureFlagScope.GLOBAL, FeatureFlagState.GRADUAL_ROLLOUT, rollout_percentage=0.0
        )

        assert service.is_enabled("gradual_feature", _TENANT_A) is False

    def test_gradual_rollout_hundred_percent_always_true(self) -> None:
        repo = _FakeFeatureFlagRepository()
        service = FeatureFlagService(repo)
        service.set_flag(
            "gradual_feature", FeatureFlagScope.GLOBAL, FeatureFlagState.GRADUAL_ROLLOUT, rollout_percentage=100.0
        )

        assert service.is_enabled("gradual_feature", _TENANT_A) is True

    def test_resolution_is_cached_in_redis_for_30_seconds(self) -> None:
        repo = _FakeFeatureFlagRepository()
        redis = FakeRedisClient()
        service = FeatureFlagService(repo, redis=redis)
        service.set_flag("cached_flag", FeatureFlagScope.GLOBAL, FeatureFlagState.ENABLED)

        assert service.is_enabled("cached_flag", _TENANT_A) is True
        cache_key = f"voiceos:feature_flag:cached_flag:{_TENANT_A}"
        assert redis.ttl(cache_key) == 30

    def test_cached_resolution_survives_repository_becoming_unavailable(self) -> None:
        repo = _FakeFeatureFlagRepository()
        redis = FakeRedisClient()
        service = FeatureFlagService(repo, redis=redis)
        service.set_flag("cached_flag", FeatureFlagScope.GLOBAL, FeatureFlagState.ENABLED)
        assert service.is_enabled("cached_flag", _TENANT_A) is True

        # Simulate the underlying row disappearing -- the cached answer still wins.
        repo._rows.clear()
        assert service.is_enabled("cached_flag", _TENANT_A) is True
