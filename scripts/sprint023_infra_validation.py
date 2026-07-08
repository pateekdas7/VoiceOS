#!/usr/bin/env python3
"""Sprint-023 Phase 2 infrastructure validation — run against the real
Postgres (migration 0020) on the CPU node.

Exercises the scenarios in implementation/sprints/Sprint-023.md's Phase 2
"Integration validation"/"Infrastructure Validation" sections against real
infrastructure. Not part of the pytest suite (pytest coverage of the same
behaviors lives in tests/unit/services/test_{campaign_management,
contact_center,hitl}.py and tests/integration/services/test_{rbi_scheduling,
hitl_sla}.py) — this is an operational smoke-test / evidence script,
following the Sprint-013/.../022 precedent.

Supervisor barge-in / live transfer use ``FakeAudioBridge`` (in-memory) —
Sprint-023.md's own Phase 2 Infrastructure Validation section says
"verified via audio routing mock on staging" (real telephony bridging is
out of scope this sprint; no dialer/PSTN gateway is deployed on this node).

Usage:
    POSTGRES_DSN=<dsn> python scripts/sprint023_infra_validation.py
"""

from __future__ import annotations

import datetime
import os
import sys
import uuid
from datetime import UTC, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from src.libs.contracts.context import ConsentStatus, ContactInfo, CustomerContext, PartyInfo
from src.libs.contracts.models.campaign import ABTestVariant, AudienceCriteria, RetryPolicy
from src.libs.contracts.models.hitl import HITLPriority
from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import CustomerId, PhoneNumber, TenantId
from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.repositories.campaign import CampaignRepository
from src.libs.repositories.hitl import HITLDecisionRepository, HITLQueueRepository
from src.libs.repositories.tenant import TenantRepository
from src.services.ai_governance.service import AIGovernanceService
from src.services.ai_governance.verdict import GovernanceStatus
from src.services.campaign_management.ab_testing import ABTestingFramework
from src.services.campaign_management.lifecycle import CampaignLifecycleError
from src.services.campaign_management.scheduler import ScheduleEngine
from src.services.campaign_management.service import CampaignService
from src.services.contact_center.live_transfer import LiveTransferService
from src.services.contact_center.router import AgentState, SkillsBasedRouter
from src.services.contact_center.supervisor import SupervisorService
from src.services.hitl.override_logger import OverrideLogger
from src.services.hitl.queue import HITLQueue
from src.services.hitl.sla_enforcer import SLAEnforcer
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService
from tests.fixtures.audio_bridge import FakeAudioBridge

POSTGRES_DSN = os.environ["POSTGRES_DSN"]

_NOW = datetime.datetime.now(UTC)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    conn = psycopg2.connect(POSTGRES_DSN)
    print(f"Connected to Postgres: {POSTGRES_DSN.split('@')[-1]}")
    print()

    tenant_id = TenantId(str(uuid.uuid4()))

    try:
        tenant_repo = TenantRepository(conn)
        tenant_repo.create(
            Tenant(
                tenant_id=tenant_id,
                slug=f"sprint023-validation-{uuid.uuid4().hex[:8]}",
                display_name="Sprint-023 Validation Tenant",
                subscription_tier="GROWTH",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )

        campaign_repo = CampaignRepository(conn)
        hitl_queue_repo = HITLQueueRepository(conn)
        hitl_decision_repo = HITLDecisionRepository(conn)

        # 1. Campaign lifecycle: DRAFT -> REVIEW -> APPROVED -> ACTIVE via real Postgres.
        campaign_service = CampaignService(campaign_repo)
        campaign = campaign_service.create(
            tenant_id,
            "Sprint-023 Validation Campaign",
            AudienceCriteria(min_dpd=30),
            RetryPolicy(),
            created_by="validator",
        )
        campaign_service.submit_for_review(tenant_id, campaign.campaign_id)
        campaign_service.approve(tenant_id, campaign.campaign_id, "validation-supervisor")
        activated = campaign_service.activate(
            tenant_id, campaign.campaign_id, "validation-supervisor", target_call_count=100
        )
        results.append(
            (
                "Campaign lifecycle DRAFT->REVIEW->APPROVED->ACTIVE (real Postgres)",
                activated.status.value == "ACTIVE",
                f"status={activated.status.value}",
            )
        )

        # 2. Campaign lifecycle: DRAFT -> ACTIVE (skip APPROVED) -> raises / 422-equivalent.
        draft_campaign = campaign_service.create(
            tenant_id, "Invalid Transition Test", AudienceCriteria(), RetryPolicy(), created_by="validator"
        )
        invalid_transition_raised = False
        try:
            campaign_service.activate(tenant_id, draft_campaign.campaign_id, "validator", target_call_count=10)
        except CampaignLifecycleError:
            invalid_transition_raised = True
        results.append(
            (
                "Campaign lifecycle: DRAFT->ACTIVE without APPROVED raises",
                invalid_transition_raised,
                f"raised={invalid_transition_raised}",
            )
        )

        # 3. RBI calling hours: 21:00 scheduling request -> DENY -> schedule returns None.
        schedule_engine = ScheduleEngine(PolicyEngineService(PolicyEngine()))
        result_21h = schedule_engine.schedule_next_call(tenant_id, "cust-1", str(campaign.campaign_id), hour=21)
        results.append(
            ("RBI calling hours: 21:00 request -> schedule returns None", result_21h is None, f"result={result_21h}")
        )

        # 4. RBI frequency: 3 calls already today -> DENY -> schedule returns None.
        result_freq = schedule_engine.schedule_next_call(
            tenant_id, "cust-1", str(campaign.campaign_id), hour=10, calls_today_count=3
        )
        results.append(
            ("RBI frequency: 3 calls today -> schedule returns None", result_freq is None, f"result={result_freq}")
        )

        # 5. A/B variant: dispatch 100 calls -> ~50% in variant A, ~50% in variant B.
        variants = (
            ABTestVariant(variant_id="a", name="control", traffic_weight=50),
            ABTestVariant(variant_id="b", name="treatment", traffic_weight=50),
        )
        assignments = [
            ABTestingFramework.assign_variant(f"cust-{i}", str(campaign.campaign_id), variants) for i in range(100)
        ]
        a_count = sum(1 for v in assignments if v is not None and v.variant_id == "a")
        b_count = sum(1 for v in assignments if v is not None and v.variant_id == "b")
        balanced = abs(a_count - b_count) <= 30  # deterministic hash, not a fair coin — generous tolerance
        results.append(
            ("A/B variant: 100 dispatches -> ~50/50 split (deterministic hash)", balanced, f"a={a_count} b={b_count}")
        )

        # 6. HITL: REQUIRE_HUMAN verdict -> durable queue item -> supervisor approves -> resolved.
        hitl_queue = HITLQueue(hitl_queue_repo)
        hitl_item = hitl_queue.enqueue(tenant_id, "validation-call-1", "REQUIRE_HUMAN", priority=HITLPriority.HIGH)
        pending = hitl_queue.list_pending(tenant_id)
        override_logger = OverrideLogger(hitl_decision_repo, hitl_queue_repo)
        override_logger.record_decision(
            tenant_id,
            hitl_item.hitl_item_id,
            "validation-supervisor",
            "APPROVE",
            "verified via real Postgres validation run",
        )
        resolved = hitl_queue.get(tenant_id, hitl_item.hitl_item_id)
        results.append(
            (
                "HITL: REQUIRE_HUMAN enqueued -> queue item -> supervisor approves -> resumed",
                any(i.hitl_item_id == hitl_item.hitl_item_id for i in pending)
                and resolved is not None
                and resolved.status.value == "RESOLVED",
                f"pending_before={len(pending)} resolved_status={resolved.status.value if resolved else None}",
            )
        )

        # 6b. Every REQUIRE_HUMAN verdict from AIGovernanceService actually reaches the
        # durable HITL queue (not just type-compatible in principle) -- HITLQueue
        # substituted as GovernanceLayer's human_oversight_router, proven against real
        # Postgres, then confirmed durable via a fresh HITLQueue/repository instance.
        governance_service = AIGovernanceService.create(human_oversight_router=hitl_queue)
        governance_plan = ResponsePlan(
            plan_id=str(uuid.uuid4()),
            version=1,
            call_id="validation-call-governance",
            tenant_id=str(tenant_id),
            created_at=_NOW,
            strategy=StrategyAction(action=StrategyLabel.ASK),
            facts={},
        )
        verdict = governance_service.evaluate_output(
            "Sab kuch theek hai.",
            governance_plan,
            risk_score=0.95,
            call_id="validation-call-governance",
            tenant_id=str(tenant_id),
        )
        fresh_hitl_queue = HITLQueue(HITLQueueRepository(conn))
        queued_after_restart = any(
            i.call_id == "validation-call-governance" for i in fresh_hitl_queue.list_pending(tenant_id)
        )
        results.append(
            (
                "AI Governance REQUIRE_HUMAN verdict reaches the durable HITL queue (survives a fresh instance)",
                verdict.status == GovernanceStatus.REQUIRE_HUMAN and queued_after_restart,
                f"verdict={verdict.status.value} queued_after_restart={queued_after_restart}",
            )
        )

        # 7. HITL SLA: item older than CRITICAL SLA (5 min) -> HITLSLABreached (mark_sla_breached applied).
        old_item = hitl_queue.enqueue(tenant_id, "validation-call-2", "REQUIRE_HUMAN", priority=HITLPriority.CRITICAL)
        sla_enforcer = SLAEnforcer(hitl_queue_repo)
        breached = sla_enforcer.check_and_escalate(tenant_id, now=old_item.enqueued_at + timedelta(minutes=6))
        results.append(
            (
                "HITL SLA: CRITICAL item age > 5min -> HITLSLABreached",
                any(b.hitl_item_id == old_item.hitl_item_id for b in breached),
                f"breached_count={len(breached)}",
            )
        )

        # 8. Live transfer: assembles AgentScreenContext with transcript + AI summary.
        router = SkillsBasedRouter()
        router.register_agent(AgentState(agent_id="validation-agent", skills=("collections",)))
        audio_bridge = FakeAudioBridge()
        live_transfer = LiveTransferService(router, audio_bridge)
        customer_context = CustomerContext(
            customer_id=CustomerId("cust-validation"),
            tenant_id=tenant_id,
            primary_party=PartyInfo(
                party_id=CustomerId("cust-validation"),
                role="BORROWER",
                name="Validation Customer",
                contact=ContactInfo(phone_number=PhoneNumber("+919876500000")),
            ),
            consent_status=ConsentStatus.GRANTED,
            call_id="validation-call-3",
        )
        transfer_result = live_transfer.initiate_transfer(
            tenant_id,
            "validation-call-3",
            "ESCALATE",
            customer_context,
            transcript=("Agent: Hello", "Customer: I need a human"),
            ai_summary="Customer requested to speak with a human agent.",
            required_skills=("collections",),
        )
        results.append(
            (
                "Live transfer: AgentScreenContext includes transcript + AI summary",
                len(transfer_result.context.transcript) == 2 and transfer_result.context.ai_summary != "",
                f"transcript_len={len(transfer_result.context.transcript)}",
            )
        )

        # 9. Supervisor barge-in: AI audio muted (audio routing mock, per Sprint-023.md).
        # Uses a fresh call_id (not "validation-call-3") — that one was already
        # muted by the live-transfer check above, which would give a false
        # positive for "monitor is read-only" here.
        supervisor = SupervisorService(audio_bridge)
        supervisor.monitor(tenant_id, "validation-call-4", "validation-supervisor")
        monitor_muted = audio_bridge.is_ai_muted("validation-call-4")
        supervisor.barge_in(tenant_id, "validation-call-4", "validation-supervisor")
        bargein_muted = audio_bridge.is_ai_muted("validation-call-4")
        results.append(
            (
                "Supervisor monitor read-only (no mute) + barge-in mutes AI",
                monitor_muted is False and bargein_muted is True,
                f"monitor_muted={monitor_muted} bargein_muted={bargein_muted}",
            )
        )
    finally:
        conn.rollback()  # discard any statement left aborted mid-transaction by a failure above
        cur = conn.cursor()
        cur.execute("DELETE FROM hitl_decisions WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM hitl_queue WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM campaigns WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
        conn.commit()
        conn.close()

    print(f"{'CHECK':<75} {'RESULT':<8} DETAIL")
    print("-" * 125)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<75} {status_str:<8} {detail}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
