#!/usr/bin/env python3
"""Sprint-023 HITL queue validation — real Postgres.

Referenced by implementation/sprints/Sprint-023.md's DR Validation section:

    # Trigger REQUIRE_HUMAN verdict; verify queue item
    python3 scripts/validate/hitl_queue.py
    # Expected: item in queue; supervisor can retrieve and approve

Drives a real ``AIGovernanceService`` (with ``HITLQueue`` substituted as its
``human_oversight_router``, per Sprint-023's `HumanOversightRouterPort`
wiring) to a REQUIRE_HUMAN verdict, then confirms the item is durable: a
*second*, freshly-constructed ``HITLQueue`` instance over the same Postgres
connection can retrieve it, and a supervisor decision resolves it.

This script reads the DB credential from the POSTGRES_DSN environment
variable — it is never hardcoded here.

    export POSTGRES_DSN='postgresql://voiceos:voiceos_pw@localhost:5432/voiceos'
    python3 scripts/validate/hitl_queue.py
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import TenantId
from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.repositories.hitl import HITLDecisionRepository, HITLQueueRepository
from src.libs.repositories.tenant import TenantRepository
from src.services.ai_governance.service import AIGovernanceService
from src.services.ai_governance.verdict import GovernanceStatus
from src.services.hitl.override_logger import OverrideLogger
from src.services.hitl.queue import HITLQueue

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")


def main() -> int:
    if not POSTGRES_DSN:
        print("[hitl_queue] POSTGRES_DSN not set", file=sys.stderr)
        return 1

    import datetime

    now = datetime.datetime.now(datetime.UTC)
    conn = psycopg2.connect(POSTGRES_DSN)
    tenant_id = TenantId(str(uuid.uuid4()))
    call_id = f"validation-call-{uuid.uuid4()}"

    try:
        TenantRepository(conn).create(
            Tenant(
                tenant_id=tenant_id,
                slug=f"hitl-validation-{uuid.uuid4().hex[:8]}",
                display_name="HITL Queue Validation Tenant",
                subscription_tier="GROWTH",
                created_at=now,
                updated_at=now,
            )
        )

        hitl_queue = HITLQueue(HITLQueueRepository(conn))
        governance_service = AIGovernanceService.create(human_oversight_router=hitl_queue)
        plan = ResponsePlan(
            plan_id=str(uuid.uuid4()),
            version=1,
            call_id=call_id,
            tenant_id=str(tenant_id),
            created_at=now,
            strategy=StrategyAction(action=StrategyLabel.ASK),
            facts={},
        )

        verdict = governance_service.evaluate_output(
            "Sab kuch theek hai.", plan, risk_score=0.95, call_id=call_id, tenant_id=str(tenant_id)
        )
        print(f"[hitl_queue] AIGovernanceService verdict: {verdict.status.value}")
        if verdict.status != GovernanceStatus.REQUIRE_HUMAN:
            print("[hitl_queue] FAIL: expected REQUIRE_HUMAN", file=sys.stderr)
            return 1

        # Durability: a fresh HITLQueue/repository instance retrieves the same item.
        fresh_queue = HITLQueue(HITLQueueRepository(conn))
        pending = fresh_queue.list_pending(tenant_id)
        item = next((i for i in pending if i.call_id == call_id), None)
        if item is None:
            print("[hitl_queue] FAIL: item not found via a fresh HITLQueue instance", file=sys.stderr)
            return 1
        print(f"[hitl_queue] item durable: hitl_item_id={item.hitl_item_id} priority={item.priority.value}")

        # Supervisor retrieves and approves it.
        override_logger = OverrideLogger(HITLDecisionRepository(conn), HITLQueueRepository(conn))
        override_logger.record_decision(
            tenant_id, item.hitl_item_id, "validation-supervisor", "APPROVE", "verified via DR validation script"
        )
        resolved = fresh_queue.get(tenant_id, item.hitl_item_id)
        if resolved is None or resolved.status.value != "RESOLVED":
            print("[hitl_queue] FAIL: supervisor approval did not resolve the item", file=sys.stderr)
            return 1
        print("[hitl_queue] supervisor approved -> item RESOLVED. PASS")
        return 0
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("DELETE FROM hitl_decisions WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM hitl_queue WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
