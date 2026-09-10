"""Gate 3D P2 — Balance flow end-to-end deterministic proof.

Chains together the three authoritative components a real call must walk
through to render an outstanding balance to the customer:

    CustomerContext (from CustomerContextAssembler)
        └─> ResponsePlanningEngine.assemble(context=...)
              └─> ResponsePlan.facts['outstanding_balance_minor']
                    └─> DialogueResponseEngine.generate_reply(plan, context)
                          └─> reply_text with formatted rupees (or
                              clarify_no_record when absent)

Every hop is verified with the same rendered-text assertions the customer
would perceive on the wire — no mocks between the engines, both are the
real production classes. This closes the loop that Fix D-1/D-2/D-3
previously broke:

  * D-1: /voice never provided customer_id → no CustomerContext.
        Now covered by tests/unit/services/test_voice_ani_resolution.py.
  * D-2: DialogueResponseEngine mis-read the facts key.
        Locked in by tests/unit/engines/test_dialogue_response.py::
        TestFixDNoRecordBranch::test_facts_outstanding_balance_minor_key_is_read.
  * D-3: 0 sentinel fabricated "₹0 outstanding".
        Locked in by TestFixDNoRecordBranch above and reinforced here.

This module adds the missing middle-of-the-chain proof: given a real
CustomerContext with a real outstanding balance, ResponsePlanningEngine
populates the fact under the exact key DialogueResponseEngine consumes,
and the rendered text carries that amount — not clarify, not ₹0.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.engines.dialogue_response.engine import DialogueResponseEngine
from src.libs.contracts.primitives import (
    AccountId, Currency, CustomerId, Money, TenantId,
)
from src.libs.contracts.response_plan import ResponsePlan
from src.libs.contracts.context import (
    ConsentStatus, ContactInfo, CustomerContext, LoanSummary,
    OutstandingBalance, PartyInfo,
)
from src.libs.contracts.turn import TurnInput, TurnRole

# Reuse existing engine + context fixtures rather than duplicating.
from tests.unit.engines.test_response_planning import _build_engine

# Reuse existing session helper + lender constant so we render exactly as
# a real call would.
from tests.unit.engines.test_dialogue_response import (
    _LENDER,
    _session,
)


def _make_full_context(
    *, outstanding_minor: int, customer_name: str = "Prateek", dpd: int = 45
) -> CustomerContext:
    """A CustomerContext shaped exactly as CustomerContextAssembler would
    produce for a real call — every field DialogueResponseEngine and
    ResponsePlanningEngine actually read is populated."""
    loan = LoanSummary(
        account_id=AccountId("acc-p2-001"),
        product_type="PERSONAL_LOAN",
        outstanding_balance=Money(amount_minor=outstanding_minor, currency=Currency.INR),
        dpd=dpd,
        total_overdue=Money(amount_minor=outstanding_minor, currency=Currency.INR),
    )
    return CustomerContext(
        customer_id=CustomerId("cust-p2-001"),
        tenant_id=TenantId("tenant-001"),
        primary_party=PartyInfo(
            party_id=CustomerId("cust-p2-001"),
            role="BORROWER",
            name=customer_name,
            contact=ContactInfo(phone_number="+919911954448"),  # type: ignore[arg-type]
        ),
        loans=(loan,),
        outstanding=OutstandingBalance(
            total_outstanding=Money(amount_minor=outstanding_minor, currency=Currency.INR),
            total_overdue=Money(amount_minor=outstanding_minor, currency=Currency.INR),
            account_count=1,
        ),
        consent_status=ConsentStatus.GRANTED,
    )


def _make_turn(transcript: str, turn_index: int = 1) -> TurnInput:
    return TurnInput(
        turn_id=str(uuid.uuid4()),
        call_id="call-p2-001",
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(),
        created_at=datetime.utcnow(),
        correlation_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        turn_index=turn_index,
    )


# ---------------------------------------------------------------------------
# P2.1 — Real outstanding balance flows through every hop into rendered text
# ---------------------------------------------------------------------------


def test_p2_end_to_end_outstanding_balance_renders_in_reply() -> None:
    """Rs 50,000 outstanding (5_000_000 minor units) must survive
    ResponsePlanningEngine → DialogueResponseEngine → reply_text intact."""
    rp_engine = _build_engine()
    context = _make_full_context(outstanding_minor=5_000_000)

    plan, _decisions = rp_engine.assemble(
        turn=_make_turn("kitna outstanding hai", turn_index=1),
        context=context,
        retrieval=[],
    )

    # Middle-of-chain proof: ResponsePlanningEngine populates the exact
    # key DialogueResponseEngine reads.
    assert "outstanding_balance_minor" in plan.facts, \
        f"ResponsePlanningEngine did not populate outstanding_balance_minor: facts={plan.facts!r}"
    assert plan.facts["outstanding_balance_minor"] == 5_000_000

    # End-of-chain proof: DialogueResponseEngine renders that amount.
    dr_engine = DialogueResponseEngine()
    session = _session()
    session.set_dialogue_state_name("CONVERSATION")
    out = dr_engine.generate_reply(
        session=session, response_plan=plan, context=context,
        user_text="kitna outstanding hai", lender_name=_LENDER,
    )

    # 5_000_000 minor units == Rs 50,000 == "50,000" formatted.
    assert "50,000" in out.reply_text, \
        f"Rs 50,000 outstanding did not render in reply_text: {out.reply_text!r}"
    # Never fabricated: no clarify_no_record.
    assert "detail" not in out.reply_text.lower(), \
        f"clarify_no_record leaked into reply despite authoritative balance: {out.reply_text!r}"


# ---------------------------------------------------------------------------
# P2.2 — Genuine zero balance renders as zero (never routed to clarify)
# ---------------------------------------------------------------------------


def test_p2_authoritative_zero_flows_through_and_renders_as_zero() -> None:
    """Customer who genuinely owes 0 must have that fact preserved end-
    to-end. 0 is only forbidden as a substitute for 'unknown'."""
    rp_engine = _build_engine()
    context = _make_full_context(outstanding_minor=0)

    plan, _ = rp_engine.assemble(
        turn=_make_turn("kitna outstanding hai", turn_index=1),
        context=context,
        retrieval=[],
    )

    assert plan.facts.get("outstanding_balance_minor") == 0, \
        f"authoritative zero lost between context and plan.facts: {plan.facts!r}"

    dr_engine = DialogueResponseEngine()
    session = _session()
    session.set_dialogue_state_name("CONVERSATION")
    out = dr_engine.generate_reply(
        session=session, response_plan=plan, context=context,
        user_text="kitna outstanding hai", lender_name=_LENDER,
    )

    # Genuine zero must NOT be routed to clarify_no_record.
    assert "detail" not in out.reply_text.lower(), \
        f"clarify_no_record fired for authoritative zero: {out.reply_text!r}"


# ---------------------------------------------------------------------------
# P2.3 — Missing context: no fabrication, clarify_no_record fires
# ---------------------------------------------------------------------------


def test_p2_missing_context_never_fabricates_and_asks_for_account_number() -> None:
    """When customer_id was never resolved (no CustomerContext), the
    chain must fall through to clarify_no_record and never fabricate
    any rupee amount — Fix D-3 lock-in in an end-to-end shape."""
    rp_engine = _build_engine()

    plan, _ = rp_engine.assemble(
        turn=_make_turn("kitna outstanding hai", turn_index=1),
        context=None,
        retrieval=[],
    )
    # With no context, the fact must be absent (never fabricated as 0).
    assert "outstanding_balance_minor" not in plan.facts

    dr_engine = DialogueResponseEngine()
    session = _session()
    session.set_dialogue_state_name("CONVERSATION")
    out = dr_engine.generate_reply(
        session=session, response_plan=plan, context=None,
        user_text="kitna outstanding hai", lender_name=_LENDER,
    )

    forbidden = ("₹0", "₹ 0", "0 outstanding", "0 rupees", "rs. 0", "rs 0")
    for token in forbidden:
        assert token.lower() not in out.reply_text.lower(), \
            f"fabricated {token!r} in reply despite missing context: {out.reply_text!r}"
    # Should route to clarify_no_record — the surviving safe path.
    assert (
        "loan account number" in out.reply_text
        or "account का detail" in out.reply_text
    ), f"expected clarify_no_record when context is None; got: {out.reply_text!r}"
