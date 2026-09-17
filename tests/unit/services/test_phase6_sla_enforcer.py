"""Unit tests for Phase 6a: SLAEnforcer job runner wiring.

Verifies SLAEnforcer behaviour that the CronJob runner depends on:
- Idempotency: already-breached items are not re-escalated on subsequent ticks
- Cross-tenant scanning: tenant_id=None polls all tenants
- Empty queue: no items returned when queue is empty
- Correct items returned when multiple items are checked
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call

import pytest

from src.libs.contracts.models.hitl import HITLItem, HITLItemStatus, HITLPriority
from src.libs.contracts.primitives import TenantId
from src.services.hitl.sla_enforcer import SLAEnforcer


def _make_item(
    hitl_item_id: str = "item-1",
    tenant_id: str = "t-1",
    priority: HITLPriority = HITLPriority.HIGH,
    sla_breached: bool = False,
    deadline_offset_seconds: int = -30,
) -> HITLItem:
    now = datetime.now(UTC)
    return HITLItem(
        hitl_item_id=hitl_item_id,
        tenant_id=tenant_id,
        call_id="call-1",
        turn_id="turn-1",
        priority=priority,
        status=HITLItemStatus.PENDING,
        payload={},
        enqueued_at=now - timedelta(seconds=300),
        sla_deadline_at=now + timedelta(seconds=deadline_offset_seconds),
        sla_breached=sla_breached,
    )


class _FakeRepo:
    def __init__(self, items: list[HITLItem] | None = None) -> None:
        self._items = items or []
        self.breached_ids: list[str] = []

    def find_open(self, tenant_id: TenantId | None = None):
        return tuple(self._items)

    def mark_sla_breached(self, tenant_id: TenantId, hitl_item_id: str) -> None:
        self.breached_ids.append(hitl_item_id)


class TestSLAEnforcerIdempotency:
    def test_already_breached_item_is_not_re_marked(self) -> None:
        already_breached = _make_item("item-1", sla_breached=True, deadline_offset_seconds=-60)
        repo = _FakeRepo([already_breached])
        enforcer = SLAEnforcer(repo)
        result = enforcer.check_and_escalate()

        assert len(result) == 0
        assert "item-1" not in repo.breached_ids

    def test_new_breach_is_marked(self) -> None:
        item = _make_item("item-2", sla_breached=False, deadline_offset_seconds=-60)
        repo = _FakeRepo([item])
        enforcer = SLAEnforcer(repo)
        result = enforcer.check_and_escalate()

        assert len(result) == 1
        assert "item-2" in repo.breached_ids

    def test_not_yet_breached_item_is_skipped(self) -> None:
        item = _make_item("item-3", sla_breached=False, deadline_offset_seconds=+600)
        repo = _FakeRepo([item])
        enforcer = SLAEnforcer(repo)
        result = enforcer.check_and_escalate()

        assert len(result) == 0
        assert repo.breached_ids == []


class TestSLAEnforcerCrossTenant:
    def test_cross_tenant_scan_passes_none_to_find_open(self) -> None:
        repo = MagicMock()
        repo.find_open.return_value = ()
        enforcer = SLAEnforcer(repo)
        enforcer.check_and_escalate(tenant_id=None)
        repo.find_open.assert_called_once_with(None)

    def test_multiple_tenants_items_all_processed(self) -> None:
        items = [
            _make_item("item-a", tenant_id="t-1", deadline_offset_seconds=-10),
            _make_item("item-b", tenant_id="t-2", deadline_offset_seconds=-10),
        ]
        repo = _FakeRepo(items)
        enforcer = SLAEnforcer(repo)
        result = enforcer.check_and_escalate()

        assert len(result) == 2
        assert set(repo.breached_ids) == {"item-a", "item-b"}


class TestSLAEnforcerEmptyQueue:
    def test_empty_queue_returns_empty_tuple(self) -> None:
        repo = _FakeRepo([])
        enforcer = SLAEnforcer(repo)
        result = enforcer.check_and_escalate()
        assert result == ()


class TestSLAEnforcerMixedItems:
    def test_only_breached_past_deadline_are_returned(self) -> None:
        items = [
            _make_item("past-breach", sla_breached=True, deadline_offset_seconds=-100),
            _make_item("new-breach", sla_breached=False, deadline_offset_seconds=-5),
            _make_item("future", sla_breached=False, deadline_offset_seconds=+500),
        ]
        repo = _FakeRepo(items)
        enforcer = SLAEnforcer(repo)
        result = enforcer.check_and_escalate()

        assert len(result) == 1
        assert result[0].hitl_item_id == "new-breach"
        assert repo.breached_ids == ["new-breach"]
