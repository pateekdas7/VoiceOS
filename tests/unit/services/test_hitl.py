"""Unit tests for the Human-In-The-Loop (HITL) Platform (Sprint-023): durable queue,
SLA enforcement, human review API, override audit logging.

Architecture: V4 Ch15 (Human Oversight).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from starlette.testclient import TestClient

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.hitl import HITLDecision, HITLItem, HITLItemStatus, HITLPriority
from src.libs.contracts.primitives import TenantId
from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.services.ai_governance.service import AIGovernanceService
from src.services.ai_governance.verdict import GovernanceStatus
from src.services.hitl.dashboard import HITLDashboard
from src.services.hitl.override_logger import OverrideLogger
from src.services.hitl.queue import HITLQueue
from src.services.hitl.review_api import create_review_api
from src.services.hitl.sla_enforcer import SLAEnforcer

TENANT = TenantId("tenant-a")


def _response_plan(call_id: str = "call-1", tenant_id: str = "tenant-a") -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id=call_id,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        strategy=StrategyAction(action=StrategyLabel.ASK),
        facts={},
    )


class _FakeHITLQueueRepository:
    def __init__(self) -> None:
        self._items: dict[str, HITLItem] = {}

    def create(self, item: HITLItem) -> HITLItem:
        self._items[item.hitl_item_id] = item
        return item

    def get(self, tenant_id: object, hitl_item_id: str) -> HITLItem | None:
        return self._items.get(hitl_item_id)

    def find_pending(self, tenant_id: object) -> tuple[HITLItem, ...]:
        return tuple(i for i in self._items.values() if i.status == HITLItemStatus.PENDING)

    def find_open(self, tenant_id: object | None = None) -> tuple[HITLItem, ...]:
        return tuple(i for i in self._items.values() if i.status != HITLItemStatus.RESOLVED)

    def claim(self, tenant_id: object, hitl_item_id: str, supervisor_id: str, claimed_at: datetime) -> None:
        item = self._items[hitl_item_id]
        self._items[hitl_item_id] = item.model_copy(
            update={"status": HITLItemStatus.CLAIMED, "claimed_by": supervisor_id, "claimed_at": claimed_at}
        )

    def resolve(self, tenant_id: object, hitl_item_id: str, resolved_at: datetime) -> None:
        item = self._items[hitl_item_id]
        self._items[hitl_item_id] = item.model_copy(
            update={"status": HITLItemStatus.RESOLVED, "resolved_at": resolved_at}
        )

    def mark_sla_breached(self, tenant_id: object, hitl_item_id: str) -> None:
        item = self._items[hitl_item_id]
        self._items[hitl_item_id] = item.model_copy(update={"sla_breached": True})


class _FakeHITLDecisionRepository:
    def __init__(self) -> None:
        self.created: list[HITLDecision] = []

    def create(self, decision: HITLDecision) -> HITLDecision:
        self.created.append(decision)
        return decision

    def find_by_item(self, tenant_id: object, hitl_item_id: str) -> tuple[HITLDecision, ...]:
        return tuple(d for d in self.created if d.hitl_item_id == hitl_item_id)


class _FakeAuditRepository:
    """Matches ``AuditRepository.append()`` — ``AuditLogger`` accepts ``audit_repository: Any``."""

    def __init__(self) -> None:
        self.recorded: list[dict[str, object]] = []

    def append(
        self,
        tenant_id: object,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        *,
        event_payload: dict[str, object] | None = None,
        ip_address: str = "",
    ) -> str:
        self.recorded.append(
            {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "outcome": outcome,
                "event_payload": event_payload,
            }
        )
        return "hash-1"


# ---------------------------------------------------------------------------
# HITLQueue
# ---------------------------------------------------------------------------


class TestHITLQueue:
    def test_hitl_queue_enqueue_and_dequeue(self) -> None:
        """AC: enqueue REQUIRE_HUMAN -> dequeue returns same item."""
        queue = HITLQueue(_FakeHITLQueueRepository())
        enqueued = queue.enqueue(TENANT, "call-1", "REQUIRE_HUMAN", priority=HITLPriority.HIGH)

        dequeued = queue.dequeue(TENANT, "sup-1")

        assert dequeued is not None
        assert dequeued.hitl_item_id == enqueued.hitl_item_id
        assert dequeued.status == HITLItemStatus.CLAIMED
        assert dequeued.claimed_by == "sup-1"

    def test_hitl_queue_route_is_human_oversight_router_compatible(self) -> None:
        """HITLQueue.route() satisfies the same signature HumanOversightRouter.route() does."""
        queue = HITLQueue(_FakeHITLQueueRepository())
        item = queue.route("call-1", str(TENANT), "risk_score above threshold")
        assert item.reason == "risk_score above threshold"
        assert item.priority == HITLPriority.MEDIUM

    def test_ai_governance_require_human_verdict_reaches_durable_hitl_queue(self) -> None:
        """Every REQUIRE_HUMAN verdict from AIGovernanceService is durably queued (Sprint-023 wiring):
        HITLQueue substituted as GovernanceLayer's human_oversight_router via HumanOversightRouterPort,
        not just type-compatible in principle — this proves the actual call path end-to-end."""
        repo = _FakeHITLQueueRepository()
        hitl_queue = HITLQueue(repo)
        service = AIGovernanceService.create(human_oversight_router=hitl_queue)
        plan = _response_plan(call_id="call-9", tenant_id=str(TENANT))

        verdict = service.evaluate_output(
            "Sab kuch theek hai.", plan, risk_score=0.95, call_id="call-9", tenant_id=str(TENANT)
        )

        assert verdict.status == GovernanceStatus.REQUIRE_HUMAN
        pending = hitl_queue.list_pending(TENANT)
        assert any(item.call_id == "call-9" for item in pending)

        # Durability: a fresh HITLQueue instance over the same repository sees it too
        # (simulates surviving a process restart — unlike Sprint-020's in-memory queue).
        fresh_queue = HITLQueue(repo)
        assert any(item.call_id == "call-9" for item in fresh_queue.list_pending(TENANT))

    def test_dequeue_prioritizes_critical_over_medium(self) -> None:
        queue = HITLQueue(_FakeHITLQueueRepository())
        queue.enqueue(TENANT, "call-medium", "reason", priority=HITLPriority.MEDIUM)
        queue.enqueue(TENANT, "call-critical", "reason", priority=HITLPriority.CRITICAL)

        dequeued = queue.dequeue(TENANT, "sup-1")

        assert dequeued is not None
        assert dequeued.call_id == "call-critical"

    def test_dequeue_returns_none_when_empty(self) -> None:
        queue = HITLQueue(_FakeHITLQueueRepository())
        assert queue.dequeue(TENANT, "sup-1") is None

    def test_list_pending_visible_for_get_hitl_queue(self) -> None:
        """AC: REQUIRE_HUMAN verdict enqueued -> visible via GET /hitl/queue for SUPERVISOR role."""
        repo = _FakeHITLQueueRepository()
        queue = HITLQueue(repo)
        queue.enqueue(TENANT, "call-1", "REQUIRE_HUMAN")

        pending = queue.list_pending(TENANT)

        assert len(pending) == 1
        assert pending[0].call_id == "call-1"


# ---------------------------------------------------------------------------
# SLAEnforcer
# ---------------------------------------------------------------------------


class TestSLAEnforcer:
    def test_hitl_sla_enforcer_breach(self) -> None:
        """AC: item age > 5 min -> SLABreached event emitted (CRITICAL SLA)."""
        repo = _FakeHITLQueueRepository()
        enqueued_at = datetime.now(UTC) - timedelta(minutes=10)
        item = HITLItem(
            hitl_item_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            call_id="call-1",
            reason="REQUIRE_HUMAN",
            priority=HITLPriority.CRITICAL,
            enqueued_at=enqueued_at,
            sla_deadline_at=enqueued_at + timedelta(minutes=5),
        )
        repo.create(item)
        enforcer = SLAEnforcer(repo)

        breached = enforcer.check_and_escalate(TENANT)

        assert len(breached) == 1
        assert breached[0].hitl_item_id == item.hitl_item_id
        stored = repo.get(TENANT, item.hitl_item_id)
        assert stored is not None
        assert stored.sla_breached is True

    def test_sla_enforcer_no_breach_within_window(self) -> None:
        repo = _FakeHITLQueueRepository()
        queue = HITLQueue(repo)
        queue.enqueue(TENANT, "call-1", "reason", priority=HITLPriority.CRITICAL)
        enforcer = SLAEnforcer(repo)

        breached = enforcer.check_and_escalate(TENANT)

        assert breached == ()

    def test_sla_enforcer_does_not_re_breach_already_flagged_items(self) -> None:
        repo = _FakeHITLQueueRepository()
        enqueued_at = datetime.now(UTC) - timedelta(minutes=10)
        item = HITLItem(
            hitl_item_id=str(uuid.uuid4()),
            tenant_id=TENANT,
            call_id="call-1",
            reason="reason",
            priority=HITLPriority.CRITICAL,
            enqueued_at=enqueued_at,
            sla_deadline_at=enqueued_at + timedelta(minutes=5),
            sla_breached=True,
        )
        repo.create(item)
        enforcer = SLAEnforcer(repo)

        assert enforcer.check_and_escalate(TENANT) == ()


# ---------------------------------------------------------------------------
# OverrideLogger
# ---------------------------------------------------------------------------


class TestOverrideLogger:
    def test_override_logger_audit_event(self) -> None:
        """AC: decision recorded -> audit event has supervisor_id + rationale."""
        queue_repo = _FakeHITLQueueRepository()
        queue_repo.create(
            HITLItem(
                hitl_item_id="item-1",
                tenant_id=TENANT,
                call_id="call-1",
                reason="REQUIRE_HUMAN",
                enqueued_at=datetime.now(UTC),
                sla_deadline_at=datetime.now(UTC) + timedelta(hours=4),
            )
        )
        audit_repo = _FakeAuditRepository()
        logger = OverrideLogger(_FakeHITLDecisionRepository(), queue_repo, audit_logger=AuditLogger(audit_repo))

        logger.record_decision(TENANT, "item-1", "sup-1", "APPROVE", "customer confirmed identity via OTP")

        assert len(audit_repo.recorded) == 1
        assert audit_repo.recorded[0]["actor_id"] == "sup-1"
        assert audit_repo.recorded[0]["event_payload"] == {
            "decision": "APPROVE",
            "rationale": "customer confirmed identity via OTP",
        }

    def test_override_logger_resolves_the_queue_item(self) -> None:
        queue_repo = _FakeHITLQueueRepository()
        queue_repo.create(
            HITLItem(
                hitl_item_id="item-1",
                tenant_id=TENANT,
                call_id="call-1",
                reason="reason",
                enqueued_at=datetime.now(UTC),
                sla_deadline_at=datetime.now(UTC) + timedelta(hours=4),
            )
        )
        logger = OverrideLogger(_FakeHITLDecisionRepository(), queue_repo)

        logger.record_decision(TENANT, "item-1", "sup-1", "APPROVE", "looks fine")

        item = queue_repo.get(TENANT, "item-1")
        assert item is not None
        assert item.status == HITLItemStatus.RESOLVED


# ---------------------------------------------------------------------------
# HumanReviewAPI
# ---------------------------------------------------------------------------


class TestHumanReviewAPI:
    def _build_client(self) -> tuple[TestClient, HITLQueue]:
        queue_repo = _FakeHITLQueueRepository()
        queue = HITLQueue(queue_repo)
        override_logger = OverrideLogger(_FakeHITLDecisionRepository(), queue_repo)
        app = create_review_api(queue, override_logger)
        return TestClient(app), queue

    def test_human_review_requires_rationale(self) -> None:
        """AC: POST /hitl/items/{id}/decision with no rationale -> 400 Bad Request."""
        client, queue = self._build_client()
        item = queue.enqueue(TENANT, "call-1", "REQUIRE_HUMAN")

        response = client.post(
            f"/hitl/items/{item.hitl_item_id}/decision",
            json={"tenant_id": str(TENANT), "decision": "APPROVE"},
            headers={"X-Actor-Role": "SUPERVISOR", "X-Actor-Id": "sup-1"},
        )

        assert response.status_code == 400

    def test_decision_with_rationale_succeeds(self) -> None:
        client, queue = self._build_client()
        item = queue.enqueue(TENANT, "call-1", "REQUIRE_HUMAN")

        response = client.post(
            f"/hitl/items/{item.hitl_item_id}/decision",
            json={"tenant_id": str(TENANT), "decision": "APPROVE", "rationale": "verified via OTP"},
            headers={"X-Actor-Role": "SUPERVISOR", "X-Actor-Id": "sup-1"},
        )

        assert response.status_code == 200

    def test_get_queue_requires_supervisor_role(self) -> None:
        client, queue = self._build_client()
        queue.enqueue(TENANT, "call-1", "REQUIRE_HUMAN")

        response = client.get(f"/hitl/queue?tenant_id={TENANT}", headers={"X-Actor-Role": "AGENT"})

        assert response.status_code == 403

    def test_get_queue_returns_enqueued_item_for_supervisor(self) -> None:
        client, queue = self._build_client()
        queue.enqueue(TENANT, "call-1", "REQUIRE_HUMAN")

        response = client.get(f"/hitl/queue?tenant_id={TENANT}", headers={"X-Actor-Role": "SUPERVISOR"})

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["call_id"] == "call-1"


# ---------------------------------------------------------------------------
# HITLDashboard
# ---------------------------------------------------------------------------


class TestHITLDashboard:
    def test_queue_depth_counts_pending_items(self) -> None:
        repo = _FakeHITLQueueRepository()
        queue = HITLQueue(repo)
        queue.enqueue(TENANT, "call-1", "reason")
        queue.enqueue(TENANT, "call-2", "reason")
        dashboard = HITLDashboard(repo)

        assert dashboard.queue_depth(TENANT) == 2

    def test_sla_status_splits_breached_and_within_sla(self) -> None:
        repo = _FakeHITLQueueRepository()
        enqueued_at = datetime.now(UTC) - timedelta(minutes=10)
        repo.create(
            HITLItem(
                hitl_item_id="item-breached",
                tenant_id=TENANT,
                call_id="call-1",
                reason="reason",
                enqueued_at=enqueued_at,
                sla_deadline_at=enqueued_at + timedelta(minutes=5),
                sla_breached=True,
            )
        )
        queue = HITLQueue(repo)
        queue.enqueue(TENANT, "call-2", "reason")
        dashboard = HITLDashboard(repo)

        status = dashboard.sla_status(TENANT)

        assert status == {"open": 2, "sla_breached": 1, "within_sla": 1}
