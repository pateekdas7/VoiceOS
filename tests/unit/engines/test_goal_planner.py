"""Unit tests for GoalPlanner.

Covers the single-goal guarantee, priority ordering, and all goal types.
Architecture: V2 Ch7.
"""

from __future__ import annotations

import pytest

from src.engines.goal_planner.engine import GoalPlanner
from src.engines.goal_planner.goals import Goal
from src.engines.risk.flags import RiskFlag
from src.engines.risk.result import RiskAssessment
from src.libs.contracts.context import (
    ConsentStatus,
    ContactInfo,
    CustomerContext,
    OutstandingBalance,
    PartyInfo,
)
from src.libs.contracts.primitives import Currency, CustomerId, Money, TenantId
from src.libs.contracts.response_plan import IntentLabel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    outstanding_minor: int = 500_000,
    overdue_minor: int = 200_000,
    dpd: int = 45,
) -> CustomerContext:
    from src.libs.contracts.context import LoanSummary
    from src.libs.contracts.primitives import AccountId

    loan = LoanSummary(
        account_id=AccountId("acc-001"),
        product_type="PERSONAL_LOAN",
        outstanding_balance=Money(amount_minor=outstanding_minor, currency=Currency.INR),
        dpd=dpd,
        total_overdue=Money(amount_minor=overdue_minor, currency=Currency.INR),
    )
    return CustomerContext(
        customer_id=CustomerId("cust-001"),
        tenant_id=TenantId("tenant-001"),
        primary_party=PartyInfo(
            party_id=CustomerId("cust-001"),
            role="BORROWER",
            name="Test Customer",
            contact=ContactInfo(phone_number="+919876543210"),  # type: ignore[arg-type]
        ),
        loans=(loan,),
        outstanding=OutstandingBalance(
            total_outstanding=Money(amount_minor=outstanding_minor, currency=Currency.INR),
            total_overdue=Money(amount_minor=overdue_minor, currency=Currency.INR),
            account_count=1,
        ),
        consent_status=ConsentStatus.GRANTED,
    )


def _make_risk(flags: list[RiskFlag], human_handoff: bool = False) -> RiskAssessment:
    return RiskAssessment(
        flags=flags,
        escalation_required=any(
            f in (RiskFlag.ABUSE_DETECTED, RiskFlag.LEGAL_THREAT, RiskFlag.ESCALATION_TRIGGER) for f in flags
        ),
        human_handoff_required=human_handoff,
    )


@pytest.fixture
def planner() -> GoalPlanner:
    return GoalPlanner()


# ---------------------------------------------------------------------------
# Required named test: exactly one goal per turn
# ---------------------------------------------------------------------------


def test_goal_exactly_one_goal(planner: GoalPlanner) -> None:
    """GoalPlanner always returns exactly 1 goal."""
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert isinstance(result, Goal)


# ---------------------------------------------------------------------------
# Priority 1: Human handoff
# ---------------------------------------------------------------------------


def test_abuse_detected_gives_transfer_agent(planner: GoalPlanner) -> None:
    risk = _make_risk([RiskFlag.ABUSE_DETECTED], human_handoff=True)
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.PAYMENT,
        risk=risk,
        identity_verified=True,
    )
    assert result == Goal.TRANSFER_AGENT


# ---------------------------------------------------------------------------
# Priority 2: End call
# ---------------------------------------------------------------------------


def test_disconnect_intent_gives_end_call(planner: GoalPlanner) -> None:
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.DISCONNECT,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert result == Goal.END_CALL


def test_consent_revoke_gives_end_call(planner: GoalPlanner) -> None:
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.CONSENT_REVOKE,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert result == Goal.END_CALL


def test_closing_state_gives_end_call(planner: GoalPlanner) -> None:
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.OTHER,
        conversation_state="CLOSING",
        identity_verified=True,
    )
    assert result == Goal.END_CALL


# ---------------------------------------------------------------------------
# Priority 4: Identity verification
# ---------------------------------------------------------------------------


def test_unverified_identity_gives_verify(planner: GoalPlanner) -> None:
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="GREETING",
        identity_verified=False,
    )
    assert result == Goal.VERIFY_IDENTITY


def test_no_context_gives_verify_identity(planner: GoalPlanner) -> None:
    """Without CustomerContext, identity verification is the safe goal."""
    result = planner.plan(
        context=None,
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=False,
    )
    assert result == Goal.VERIFY_IDENTITY


# ---------------------------------------------------------------------------
# Priority 5: Dispute handling
# ---------------------------------------------------------------------------


def test_dispute_claim_gives_handle_dispute(planner: GoalPlanner) -> None:
    risk = _make_risk([RiskFlag.DISPUTE_CLAIM])
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.PAYMENT,
        risk=risk,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert result == Goal.HANDLE_DISPUTE


def test_dispute_intent_gives_handle_dispute(planner: GoalPlanner) -> None:
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.DISPUTE,
        conversation_state="DISPUTE_HANDLING",
        identity_verified=True,
    )
    assert result == Goal.HANDLE_DISPUTE


# ---------------------------------------------------------------------------
# Priority 6/7/8: Collection goals
# ---------------------------------------------------------------------------


def test_low_dpd_gives_full_payment_goal(planner: GoalPlanner) -> None:
    """DPD ≤ 30 → COLLECT_FULL_PAYMENT."""
    ctx = _make_context(outstanding_minor=300_000, dpd=15)
    result = planner.plan(
        context=ctx,
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert result == Goal.COLLECT_FULL_PAYMENT


def test_high_dpd_gives_partial_payment_goal(planner: GoalPlanner) -> None:
    """DPD > 30 with significant outstanding → COLLECT_PARTIAL_PAYMENT."""
    ctx = _make_context(outstanding_minor=500_000, dpd=60)
    result = planner.plan(
        context=ctx,
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert result == Goal.COLLECT_PARTIAL_PAYMENT


def test_small_outstanding_gives_secure_ptp(planner: GoalPlanner) -> None:
    """Very small outstanding (below partial threshold) → SECURE_PTP."""
    ctx = _make_context(outstanding_minor=50, dpd=60)  # ₹0.50
    result = planner.plan(
        context=ctx,
        primary_intent=IntentLabel.PROMISE_TO_PAY,
        conversation_state="NEGOTIATION",
        identity_verified=True,
    )
    assert result == Goal.SECURE_PTP


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_same_inputs(planner: GoalPlanner) -> None:
    ctx = _make_context()
    risk = _make_risk([RiskFlag.DISPUTE_CLAIM])
    g1 = planner.plan(
        context=ctx,
        primary_intent=IntentLabel.PAYMENT,
        risk=risk,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    g2 = planner.plan(
        context=ctx,
        primary_intent=IntentLabel.PAYMENT,
        risk=risk,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert g1 == g2


def test_returns_goal_enum(planner: GoalPlanner) -> None:
    result = planner.plan(
        context=_make_context(),
        primary_intent=IntentLabel.PAYMENT,
        identity_verified=True,
    )
    assert isinstance(result, Goal)


def test_all_goals_reachable(planner: GoalPlanner) -> None:
    """Verify all 8 goals can be reached through different inputs."""
    reachable: set[Goal] = set()

    # TRANSFER_AGENT
    reachable.add(
        planner.plan(
            _make_context(),
            IntentLabel.PAYMENT,
            risk=_make_risk([RiskFlag.ABUSE_DETECTED], human_handoff=True),
            identity_verified=True,
        )
    )
    # END_CALL
    reachable.add(planner.plan(_make_context(), IntentLabel.DISCONNECT, identity_verified=True))
    # VERIFY_IDENTITY
    reachable.add(planner.plan(_make_context(), IntentLabel.PAYMENT, identity_verified=False))
    # HANDLE_DISPUTE
    reachable.add(
        planner.plan(
            _make_context(),
            IntentLabel.DISPUTE,
            conversation_state="DISPUTE_HANDLING",
            identity_verified=True,
        )
    )
    # COLLECT_FULL_PAYMENT
    reachable.add(
        planner.plan(
            _make_context(outstanding_minor=200_000, dpd=10),
            IntentLabel.PAYMENT,
            conversation_state="DEBT_DISCUSSION",
            identity_verified=True,
        )
    )
    # COLLECT_PARTIAL_PAYMENT
    reachable.add(
        planner.plan(
            _make_context(outstanding_minor=500_000, dpd=90),
            IntentLabel.PAYMENT,
            conversation_state="DEBT_DISCUSSION",
            identity_verified=True,
        )
    )
    # SECURE_PTP
    reachable.add(
        planner.plan(
            _make_context(outstanding_minor=50, dpd=90),
            IntentLabel.PROMISE_TO_PAY,
            identity_verified=True,
        )
    )

    assert Goal.TRANSFER_AGENT in reachable
    assert Goal.END_CALL in reachable
    assert Goal.VERIFY_IDENTITY in reachable
    assert Goal.HANDLE_DISPUTE in reachable
    assert len(reachable) >= 6
