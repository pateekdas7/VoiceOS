"""Unit tests for ConversationEngine._persist_finalized_commitment() (Path-A Phase 5).

Architecture: V5 Ch4.3 (Promise-To-Pay); RI-4 (commit-before-act).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.libs.contracts.context import (
    ConsentStatus,
    ContactInfo,
    CustomerContext,
    LoanSummary,
    OutstandingBalance,
    PartyInfo,
)
from src.libs.contracts.primitives import AccountId, Currency, CustomerId, Money, TenantId
from src.libs.contracts.response_plan import (
    DeliverySpec,
    EmotionSpec,
    NegotiationEnvelope,
    NegotiationMoveType,
    ResponsePlan,
    StrategyAction,
    StrategyLabel,
)
from src.libs.contracts.turn import TurnInput, TurnRole
from src.services.ai_governance.service import AIGovernanceService
from src.services.collections.promise_to_pay import PolicyDeniedError, PromiseToPayService, PTPValidationError
from src.services.conversation_engine.engine import CILPort, ConversationEngine, PromptBuilderPort
from src.services.knowledge_retrieval.service import KnowledgeRetrievalService
from src.services.llm_runtime.output_validator import OutputValidator
from src.services.llm_runtime.service import LLMService
from src.services.tts.service import TTSService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(promise_to_pay_service: PromiseToPayService | None = None) -> ConversationEngine:
    return ConversationEngine(
        cil=cast(CILPort, MagicMock()),
        prompt_builder=cast(PromptBuilderPort, MagicMock()),
        llm_service=cast(LLMService, MagicMock()),
        tts_service=cast(TTSService, MagicMock()),
        validator=cast(OutputValidator, MagicMock()),
        knowledge=cast(KnowledgeRetrievalService, MagicMock()),
        quality_scorer=cast(Any, MagicMock()),
        ai_governance_service=AIGovernanceService.create(),
        promise_to_pay_service=promise_to_pay_service,
    )


def _make_turn(call_id: str = "call-1") -> TurnInput:
    return TurnInput(
        turn_id=str(uuid.uuid4()),
        call_id=call_id,
        tenant_id="tenant-1",
        role=TurnRole.CUSTOMER,
        transcript="teen hazaar de raha hoon",
        segments=(),
        created_at=datetime.utcnow(),
        correlation_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        turn_index=2,
    )


def _make_context(outstanding_minor: int = 500_000) -> CustomerContext:
    loan = LoanSummary(
        account_id=AccountId("acc-001"),
        product_type="PERSONAL_LOAN",
        outstanding_balance=Money(amount_minor=outstanding_minor, currency=Currency.INR),
        dpd=45,
    )
    return CustomerContext(
        customer_id=CustomerId("cust-001"),
        tenant_id=TenantId("tenant-1"),
        primary_party=PartyInfo(
            party_id=CustomerId("cust-001"),
            role="BORROWER",
            name="Test Customer",
            contact=ContactInfo(phone_number="+919876543210"),  # type: ignore[arg-type]
        ),
        loans=(loan,),
        outstanding=OutstandingBalance(
            total_outstanding=Money(amount_minor=outstanding_minor, currency=Currency.INR),
            total_overdue=Money(amount_minor=outstanding_minor, currency=Currency.INR),
            account_count=1,
        ),
        consent_status=ConsentStatus.GRANTED,
    )


def _make_response_plan(
    negotiation_envelope: NegotiationEnvelope | None,
    call_id: str = "call-1",
) -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id=call_id,
        tenant_id="tenant-1",
        created_at=datetime.utcnow(),
        intents=(),
        entities={},
        emotion=EmotionSpec(sentiment=0.0, arousal=0.0, dominant_emotion="neutral"),
        policy_constraints=(),
        risk_flags=(),
        goal="COLLECT_PARTIAL_PAYMENT",
        strategy=StrategyAction(action=StrategyLabel.NEGOTIATE, rationale="test"),
        negotiation_envelope=negotiation_envelope,
        delivery=DeliverySpec(language="hi-IN", voice_id="veena-default", target_speaking_rate=1.0),
        facts={},
        retrieval=(),
        must_say=(),
        must_not_say=(),
    )


def _finalized_envelope(proposed_minor: int = 300_000, proposed_date: date = date(2026, 8, 1)) -> NegotiationEnvelope:
    return NegotiationEnvelope(
        floor_minor=250_000,
        ceiling_minor=500_000,
        move_type=NegotiationMoveType.FULL_PAYMENT,
        proposed_amount_minor=proposed_minor,
        proposed_date=proposed_date,
        is_finalized_commitment=True,
    )


# ---------------------------------------------------------------------------
# No-op paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_when_no_service_wired() -> None:
    engine = _make_engine(promise_to_pay_service=None)
    plan = _make_response_plan(_finalized_envelope())
    await engine._persist_finalized_commitment(_make_turn(), _make_context(), plan)  # must not raise


@pytest.mark.asyncio
async def test_noop_when_no_negotiation_envelope() -> None:
    service = MagicMock()
    service.create = AsyncMock()
    engine = _make_engine(promise_to_pay_service=service)
    plan = _make_response_plan(None)

    await engine._persist_finalized_commitment(_make_turn(), _make_context(), plan)

    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_not_a_finalized_commitment() -> None:
    service = MagicMock()
    service.create = AsyncMock()
    engine = _make_engine(promise_to_pay_service=service)
    envelope = NegotiationEnvelope(
        floor_minor=250_000,
        ceiling_minor=500_000,
        move_type=NegotiationMoveType.PARTIAL_PAYMENT,
        proposed_amount_minor=300_000,
        is_finalized_commitment=False,  # e.g. an OFFER/COUNTER, not ACCEPT/PROPOSE_PTP
    )
    plan = _make_response_plan(envelope)

    await engine._persist_finalized_commitment(_make_turn(), _make_context(), plan)

    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_no_context() -> None:
    service = MagicMock()
    service.create = AsyncMock()
    engine = _make_engine(promise_to_pay_service=service)
    plan = _make_response_plan(_finalized_envelope())

    await engine._persist_finalized_commitment(_make_turn(), None, plan)

    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_context_has_no_loans() -> None:
    service = MagicMock()
    service.create = AsyncMock()
    engine = _make_engine(promise_to_pay_service=service)
    plan = _make_response_plan(_finalized_envelope())
    context = CustomerContext(
        customer_id=CustomerId("cust-001"),
        tenant_id=TenantId("tenant-1"),
        primary_party=PartyInfo(
            party_id=CustomerId("cust-001"),
            role="BORROWER",
            name="Test Customer",
            contact=ContactInfo(phone_number="+919876543210"),  # type: ignore[arg-type]
        ),
        loans=(),
        consent_status=ConsentStatus.GRANTED,
    )

    await engine._persist_finalized_commitment(_make_turn(), context, plan)

    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_missing_proposed_amount_or_date() -> None:
    service = MagicMock()
    service.create = AsyncMock()
    engine = _make_engine(promise_to_pay_service=service)
    envelope = NegotiationEnvelope(
        floor_minor=250_000,
        ceiling_minor=500_000,
        move_type=NegotiationMoveType.FULL_PAYMENT,
        proposed_amount_minor=None,  # missing
        is_finalized_commitment=True,
    )
    plan = _make_response_plan(envelope)

    await engine._persist_finalized_commitment(_make_turn(), _make_context(), plan)

    service.create.assert_not_awaited()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persists_finalized_commitment_with_correct_args() -> None:
    from src.libs.contracts.models.collections import PromiseToPay

    service = MagicMock()
    created = PromiseToPay(
        ptp_id="ptp-1",
        tenant_id=TenantId("tenant-1"),
        call_id="call-1",
        customer_id=CustomerId("cust-001"),
        loan_account_id="acc-001",
        promised_amount_minor=300_000,
        currency="INR",
        promise_date=datetime(2026, 8, 1),
        recorded_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    service.create = AsyncMock(return_value=created)
    engine = _make_engine(promise_to_pay_service=service)
    plan = _make_response_plan(_finalized_envelope(proposed_minor=300_000, proposed_date=date(2026, 8, 1)))
    context = _make_context()

    await engine._persist_finalized_commitment(_make_turn("call-1"), context, plan)

    service.create.assert_awaited_once()
    kwargs = service.create.call_args.kwargs
    assert kwargs["tenant_id"] == TenantId("tenant-1")
    assert kwargs["call_id"] == "call-1"
    assert kwargs["customer_id"] == CustomerId("cust-001")
    assert kwargs["loan_account_id"] == "acc-001"
    assert kwargs["promised_amount_minor"] == 300_000
    assert kwargs["currency"] == "INR"
    assert kwargs["promise_date"] == datetime(2026, 8, 1)


# ---------------------------------------------------------------------------
# Errors are caught, never propagated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_error_is_caught_not_raised() -> None:
    service = MagicMock()
    service.create = AsyncMock(side_effect=PTPValidationError("amount below next EMI"))
    engine = _make_engine(promise_to_pay_service=service)
    plan = _make_response_plan(_finalized_envelope())

    await engine._persist_finalized_commitment(_make_turn(), _make_context(), plan)  # must not raise

    service.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_denied_error_is_caught_not_raised() -> None:
    service = MagicMock()
    service.create = AsyncMock(side_effect=PolicyDeniedError("RBI calling window violation"))
    engine = _make_engine(promise_to_pay_service=service)
    plan = _make_response_plan(_finalized_envelope())

    await engine._persist_finalized_commitment(_make_turn(), _make_context(), plan)  # must not raise

    service.create.assert_awaited_once()
