"""Unit tests for the AI Governance service (Sprint-018, V4 Ch3).

All tests run fully in-process. EventBus-backed tests use a real
`EventBus`/`Publisher` pair over `FakeRedisClient` (no network I/O) — same
precedent as `tests/unit/services/test_policy_engine.py` (Sprint-017).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.services.ai_governance import metrics as governance_metrics
from src.services.ai_governance.governance_layer import HUMAN_REVIEW_REQUIRED_EVENT_TYPE, GovernanceLayer
from src.services.ai_governance.law_of_authority import LawOfAuthorityChecker
from src.services.ai_governance.service import AIGovernanceService
from src.services.ai_governance.verdict import GovernanceStatus
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService
from tests.fixtures.redis import FakeRedisClient


def _response_plan(
    facts: dict[str, Any] | None = None, call_id: str = "call-1", tenant_id: str = "tenant-1"
) -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id=call_id,
        tenant_id=tenant_id,
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
        facts=facts or {},
    )


# ---------------------------------------------------------------------------
# Required named tests (Sprint-018.md)
# ---------------------------------------------------------------------------


class TestRequiredNamedTests:
    def test_law_of_authority_block_hallucination(self) -> None:
        plan = _response_plan(facts={"outstanding_balance_minor": 1_250_000})  # ₹12,500
        service = AIGovernanceService.create()

        verdict = service.evaluate_output("Aapka bakaya ₹15,000 hai.", plan)

        assert verdict.status == GovernanceStatus.BLOCK
        assert verdict.violations

    def test_law_of_authority_approve_grounded(self) -> None:
        plan = _response_plan(facts={"outstanding_balance_minor": 1_250_000})  # ₹12,500
        service = AIGovernanceService.create()

        verdict = service.evaluate_output("Aapka bakaya ₹12,500 hai.", plan)

        assert verdict.status == GovernanceStatus.APPROVE

    def test_governance_require_human_routes_to_supervisor(self, fake_redis: FakeRedisClient) -> None:
        bus = EventBus(fake_redis, stream="voiceos-events")
        publisher = Publisher(bus)
        service = AIGovernanceService.create(publisher=publisher)
        plan = _response_plan()

        verdict = service.evaluate_output(
            "Sab kuch theek hai.", plan, risk_score=0.95, call_id="call-1", tenant_id="tenant-1"
        )

        assert verdict.status == GovernanceStatus.REQUIRE_HUMAN

        events = bus.replay_from()
        assert any(envelope.event_type == HUMAN_REVIEW_REQUIRED_EVENT_TYPE for _entry_id, envelope in events)


# ---------------------------------------------------------------------------
# LawOfAuthorityChecker — direct unit coverage
# ---------------------------------------------------------------------------


class TestLawOfAuthorityChecker:
    def test_clean_output_no_amounts_no_violations(self) -> None:
        checker = LawOfAuthorityChecker()
        plan = _response_plan()
        assert checker.check("Namaste, aap kaise hain?", plan) == ()

    def test_matching_amount_grounded(self) -> None:
        checker = LawOfAuthorityChecker()
        plan = _response_plan(facts={"outstanding_balance_minor": 500_000})  # ₹5,000
        assert checker.check("Aapka bakaya Rs. 5,000 hai.", plan) == ()

    def test_mismatched_amount_flagged(self) -> None:
        checker = LawOfAuthorityChecker()
        plan = _response_plan(facts={"outstanding_balance_minor": 500_000})  # ₹5,000
        violations = checker.check("Aapka bakaya ₹9,999 hai.", plan)
        assert len(violations) == 1
        assert "RI-5" in violations[0]

    def test_account_number_without_authoritative_fact_is_not_flagged(self) -> None:
        checker = LawOfAuthorityChecker()
        plan = _response_plan()
        # No account-like fact present — a long digit run (e.g. a phone
        # number) must not be flagged with no authoritative context.
        assert checker.check("Aap 9876543210 par sampark kar sakte hain.", plan) == ()

    def test_mismatched_account_number_flagged(self) -> None:
        checker = LawOfAuthorityChecker()
        plan = _response_plan(facts={"loan_account_number": "12345678"})
        violations = checker.check("Aapka khata number 87654321 hai.", plan)
        assert len(violations) == 1

    def test_matching_account_number_grounded(self) -> None:
        checker = LawOfAuthorityChecker()
        plan = _response_plan(facts={"loan_account_number": "12345678"})
        assert checker.check("Aapka khata number 12345678 hai.", plan) == ()

    def test_date_within_tolerance_grounded(self) -> None:
        checker = LawOfAuthorityChecker()
        plan = _response_plan(facts={"due_date": "2026-07-15"})
        assert checker.check("Aapki due date 2026-07-16 hai.", plan) == ()

    def test_date_outside_tolerance_flagged(self) -> None:
        checker = LawOfAuthorityChecker()
        plan = _response_plan(facts={"due_date": "2026-07-15"})
        violations = checker.check("Aapki due date 2026-08-01 hai.", plan)
        assert len(violations) == 1


# ---------------------------------------------------------------------------
# GovernanceLayer — policy-engine and content-safety paths
# ---------------------------------------------------------------------------


class TestGovernanceLayer:
    def test_approve_when_no_violations(self) -> None:
        layer = GovernanceLayer()
        plan = _response_plan()
        verdict = layer.evaluate("Namaste, main aapki madad kar sakta hoon.", plan)
        assert verdict.status == GovernanceStatus.APPROVE

    def test_abusive_content_blocked(self) -> None:
        layer = GovernanceLayer()
        plan = _response_plan()
        verdict = layer.evaluate("Tum chutiya ho.", plan)
        assert verdict.status == GovernanceStatus.BLOCK

    def test_policy_engine_require_human_maps_via_pdp(self, fake_redis: FakeRedisClient) -> None:
        """High risk_score routed through the PDP's AIGOV-HUMAN-REVIEW-HIGH-RISK
        rule (step 2, not the direct fallback gate) maps to REQUIRE_HUMAN."""
        policy_engine = PolicyEngine(redis=fake_redis)
        policy_service = PolicyEngineService(policy_engine)
        layer = GovernanceLayer(policy_engine_service=policy_service)
        plan = _response_plan()

        verdict = layer.evaluate("Sab thik hai.", plan, risk_score=0.95)
        assert verdict.status == GovernanceStatus.REQUIRE_HUMAN

    def test_law_of_authority_violation_increments_counter(self) -> None:
        layer = GovernanceLayer()
        plan = _response_plan(facts={"outstanding_balance_minor": 100_000})
        before = governance_metrics.LAW_OF_AUTHORITY_VIOLATIONS._value.get()

        layer.evaluate("Aapka bakaya ₹99,999 hai.", plan)

        after = governance_metrics.LAW_OF_AUTHORITY_VIOLATIONS._value.get()
        assert after == before + 1

    def test_verdict_counter_increments_by_outcome(self) -> None:
        layer = GovernanceLayer()
        plan = _response_plan()
        before = governance_metrics.GOVERNANCE_VERDICTS_BY_OUTCOME.labels(outcome="APPROVE")._value.get()

        layer.evaluate("Namaste, sab kuch theek hai.", plan)

        after = governance_metrics.GOVERNANCE_VERDICTS_BY_OUTCOME.labels(outcome="APPROVE")._value.get()
        assert after == before + 1
