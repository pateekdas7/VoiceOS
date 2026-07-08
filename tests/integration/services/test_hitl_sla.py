"""Integration test: durable HITL queue (real Postgres) + SLA enforcement (Sprint-023, V4 Ch15).

Required named test: ``test_hitl_sla_integration`` — real Postgres queue +
time mock -> SLA enforcement triggers correctly.

Runs against real Postgres (migration 0020 applied via the session-scoped
``pg_conn`` fixture). Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from src.libs.contracts.models.hitl import HITLItemStatus, HITLPriority
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.hitl import HITLDecisionRepository, HITLQueueRepository
from src.services.hitl.override_logger import OverrideLogger
from src.services.hitl.queue import HITLQueue
from src.services.hitl.sla_enforcer import SLAEnforcer
from tests.integration.conftest import requires_postgres


@requires_postgres
class TestHITLQueueDurability:
    def test_hitl_queue_survives_a_fresh_repository_instance(self, pg_conn: Any) -> None:
        """Durability: an item enqueued via one HITLQueue instance is visible via a fresh one
        (simulates surviving a process restart, unlike Sprint-020's in-memory HumanOversightRouter)."""
        tenant_id = TenantId(str(uuid.uuid4()))
        queue = HITLQueue(HITLQueueRepository(pg_conn))
        item = queue.enqueue(tenant_id, f"call-{uuid.uuid4()}", "REQUIRE_HUMAN", priority=HITLPriority.HIGH)

        fresh_queue = HITLQueue(HITLQueueRepository(pg_conn))
        fetched = fresh_queue.get(tenant_id, item.hitl_item_id)

        assert fetched is not None
        assert fetched.status == HITLItemStatus.PENDING
        assert fetched.priority == HITLPriority.HIGH


@requires_postgres
class TestHITLSLAIntegration:
    def test_hitl_sla_integration(self, pg_conn: Any) -> None:
        """Real Postgres queue + time mock -> SLA enforcement triggers correctly."""
        tenant_id = TenantId(str(uuid.uuid4()))
        repo = HITLQueueRepository(pg_conn)
        queue = HITLQueue(repo)
        call_id = f"call-{uuid.uuid4()}"

        item = queue.enqueue(tenant_id, call_id, "REQUIRE_HUMAN", priority=HITLPriority.CRITICAL)

        # CRITICAL SLA = 5 minutes (DEFAULT_SLA_MINUTES). Simulate polling
        # at T+10min (a mocked "now", not a real sleep) — must breach.
        simulated_now = item.enqueued_at + timedelta(minutes=10)
        enforcer = SLAEnforcer(repo)

        breached = enforcer.check_and_escalate(tenant_id, now=simulated_now)

        assert any(b.hitl_item_id == item.hitl_item_id for b in breached)
        stored = repo.get(tenant_id, item.hitl_item_id)
        assert stored is not None
        assert stored.sla_breached is True

    def test_hitl_sla_not_breached_before_deadline(self, pg_conn: Any) -> None:
        tenant_id = TenantId(str(uuid.uuid4()))
        repo = HITLQueueRepository(pg_conn)
        queue = HITLQueue(repo)
        item = queue.enqueue(tenant_id, f"call-{uuid.uuid4()}", "reason", priority=HITLPriority.CRITICAL)

        enforcer = SLAEnforcer(repo)
        breached = enforcer.check_and_escalate(tenant_id, now=item.enqueued_at + timedelta(minutes=1))

        assert breached == ()
        stored = repo.get(tenant_id, item.hitl_item_id)
        assert stored is not None
        assert stored.sla_breached is False

    def test_override_logger_persists_decision_against_real_postgres(self, pg_conn: Any) -> None:
        tenant_id = TenantId(str(uuid.uuid4()))
        queue_repo = HITLQueueRepository(pg_conn)
        queue = HITLQueue(queue_repo)
        item = queue.enqueue(tenant_id, f"call-{uuid.uuid4()}", "REQUIRE_HUMAN")

        logger = OverrideLogger(HITLDecisionRepository(pg_conn), queue_repo)
        logger.record_decision(tenant_id, item.hitl_item_id, "sup-1", "APPROVE", "verified via callback")

        resolved = queue_repo.get(tenant_id, item.hitl_item_id)
        assert resolved is not None
        assert resolved.status == HITLItemStatus.RESOLVED

        decisions = HITLDecisionRepository(pg_conn).find_by_item(tenant_id, item.hitl_item_id)
        assert len(decisions) == 1
        assert decisions[0].rationale == "verified via callback"
        assert decisions[0].supervisor_id == "sup-1"
