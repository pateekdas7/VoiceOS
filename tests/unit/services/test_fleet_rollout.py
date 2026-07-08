"""Unit tests for FleetRolloutManager (Sprint-026, V5 Ch23).

Required named test (Sprint-026.md acceptance criteria):
    FleetRolloutManager.assign_rollout_ring() distributes tenants across
    rings (no ring is empty in a test with >=4 tenants).
"""

from __future__ import annotations

import uuid

from src.libs.contracts.models.saas_ops import FleetVersion, RolloutRing, TenantRolloutAssignment
from src.libs.contracts.primitives import TenantId
from src.services.saas_ops.fleet_rollout import FleetRolloutManager, RolloutHealthGateFailedError


class _FakeRolloutRepository:
    def __init__(self) -> None:
        self._rings: dict[TenantId, TenantRolloutAssignment] = {}
        self._versions: dict[RolloutRing, FleetVersion] = {}

    def get_ring(self, tenant_id: TenantId) -> TenantRolloutAssignment | None:
        return self._rings.get(tenant_id)

    def assign_ring(self, tenant_id: TenantId, ring: RolloutRing) -> TenantRolloutAssignment:
        from datetime import UTC, datetime

        existing = self._rings.get(tenant_id)
        if existing is not None:
            return existing
        assignment = TenantRolloutAssignment(tenant_id=tenant_id, ring=ring, assigned_at=datetime.now(UTC))
        self._rings[tenant_id] = assignment
        return assignment

    def get_version_for_ring(self, ring: RolloutRing) -> FleetVersion | None:
        return self._versions.get(ring)

    def promote_ring(self, ring: RolloutRing, version: str) -> FleetVersion:
        from datetime import UTC, datetime

        fv = FleetVersion(ring=ring, version=version, promoted_at=datetime.now(UTC))
        self._versions[ring] = fv
        return fv


class TestFleetRolloutManager:
    def test_assign_rollout_ring_distributes_across_all_four_rings(self) -> None:
        repo = _FakeRolloutRepository()
        manager = FleetRolloutManager(repo)
        tenants = [TenantId(str(uuid.uuid4())) for _ in range(200)]

        rings = {manager.assign_rollout_ring(t) for t in tenants}

        assert rings == {RolloutRing.CANARY, RolloutRing.EARLY, RolloutRing.GENERAL, RolloutRing.LAGGARD}

    def test_assign_rollout_ring_is_idempotent_and_sticky(self) -> None:
        repo = _FakeRolloutRepository()
        manager = FleetRolloutManager(repo)
        tenant = TenantId("tenant-sticky")

        first = manager.assign_rollout_ring(tenant)
        second = manager.assign_rollout_ring(tenant)

        assert first == second

    def test_assign_rollout_ring_deterministic_same_tenant_same_ring_across_instances(self) -> None:
        tenant = TenantId("tenant-deterministic")
        ring_a = FleetRolloutManager(_FakeRolloutRepository()).assign_rollout_ring(tenant)
        ring_b = FleetRolloutManager(_FakeRolloutRepository()).assign_rollout_ring(tenant)

        assert ring_a == ring_b

    def test_get_version_for_ring_none_when_never_promoted(self) -> None:
        manager = FleetRolloutManager(_FakeRolloutRepository())

        assert manager.get_version_for_ring(RolloutRing.CANARY) is None

    def test_promote_canary_directly_no_gate(self) -> None:
        manager = FleetRolloutManager(_FakeRolloutRepository())

        result = manager.promote_ring(RolloutRing.CANARY, "v2.1.0")

        assert result.version == "v2.1.0"
        assert manager.get_version_for_ring(RolloutRing.CANARY) == "v2.1.0"

    def test_promote_early_blocked_until_canary_on_same_version(self) -> None:
        manager = FleetRolloutManager(_FakeRolloutRepository())

        try:
            manager.promote_ring(RolloutRing.EARLY, "v2.1.0")
            raised = False
        except RolloutHealthGateFailedError:
            raised = True
        assert raised

    def test_promote_early_succeeds_once_canary_matches(self) -> None:
        manager = FleetRolloutManager(_FakeRolloutRepository())
        manager.promote_ring(RolloutRing.CANARY, "v2.1.0")

        result = manager.promote_ring(RolloutRing.EARLY, "v2.1.0")

        assert result.version == "v2.1.0"

    def test_promote_blocked_when_health_check_fails(self) -> None:
        manager = FleetRolloutManager(_FakeRolloutRepository())
        manager.promote_ring(RolloutRing.CANARY, "v2.1.0")

        try:
            manager.promote_ring(RolloutRing.EARLY, "v2.1.0", health_check=lambda ring: False)
            raised = False
        except RolloutHealthGateFailedError:
            raised = True
        assert raised

    def test_promote_full_ring_by_ring_progression(self) -> None:
        manager = FleetRolloutManager(_FakeRolloutRepository())

        for ring in (RolloutRing.CANARY, RolloutRing.EARLY, RolloutRing.GENERAL, RolloutRing.LAGGARD):
            manager.promote_ring(ring, "v3.0.0", health_check=lambda _r: True)

        assert manager.get_version_for_ring(RolloutRing.LAGGARD) == "v3.0.0"
