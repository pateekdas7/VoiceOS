"""Unit tests for DialogueResponseEngine (Path-A Phase 6f).

Builds ResponsePlan fixtures directly (not through the full CIL pipeline)
so each test controls exactly which real-engine output (intents/entities)
the bucket router sees — the point being verified is that
DialogueResponseEngine correctly *consumes* those real outputs, not that
IntentEngine/EntityExtractor classify any particular utterance a certain
way (that's covered by their own test suites).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.engines.dialogue_response.buckets import Bucket
from src.engines.dialogue_response.engine import DialogueResponseEngine
from src.engines.dialogue_response.installment import InstallmentPlanKind, compute_installment_plan
from src.libs.contracts.context import ConsentStatus, ContactInfo, CustomerContext, LoanSummary, OutstandingBalance, PartyInfo
from src.libs.contracts.primitives import AccountId, Currency, CustomerId, Money, TenantId
from src.libs.contracts.response_plan import IntentLabel, IntentSignal, ResponsePlan
from src.services.conversation_engine.session_state import ConversationSessionState

_LENDER = "Rajat Finance"


def _make_context(outstanding_minor: int = 5_000_000, name: str = "Sunita Sharma") -> CustomerContext:
    loan = LoanSummary(
        account_id=AccountId("acc-001"),
        product_type="PERSONAL_LOAN",
        outstanding_balance=Money(amount_minor=outstanding_minor, currency=Currency.INR),
        dpd=45,
        total_overdue=Money(amount_minor=outstanding_minor, currency=Currency.INR),
    )
    return CustomerContext(
        customer_id=CustomerId("cust-001"),
        tenant_id=TenantId("tenant-001"),
        primary_party=PartyInfo(
            party_id=CustomerId("cust-001"),
            role="BORROWER",
            name=name,
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


def _make_plan(
    intents: tuple[IntentSignal, ...] = (),
    entities: dict[str, str] | None = None,
) -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-001",
        tenant_id="tenant-001",
        created_at=datetime.now(timezone.utc),
        intents=intents,
        entities=entities or {},
    )


def _session() -> ConversationSessionState:
    return ConversationSessionState(call_id="call-001")  # type: ignore[arg-type]


class TestAwaitIdentity:
    def test_confirm_transitions_to_conversation(self) -> None:
        engine = DialogueResponseEngine()
        session = _session()
        context = _make_context()

        out = engine.generate_reply(session, _make_plan(), context, "haan speaking", _LENDER)

        assert session.dialogue_state_name == "CONVERSATION"
        assert session.identity_verified is True
        assert "50,000" in out.reply_text

    def test_deny_transitions_to_close(self) -> None:
        engine = DialogueResponseEngine()
        session = _session()
        context = _make_context()

        out = engine.generate_reply(session, _make_plan(), context, "wrong number", _LENDER)

        assert session.dialogue_state_name == "CLOSE"
        assert session.farewell_requested is True
        assert "wrong number" in out.reply_text.lower()

    def test_ambiguous_first_time_reasks_with_customer_name(self) -> None:
        engine = DialogueResponseEngine()
        session = _session()
        context = _make_context(name="Ravi Kumar")

        out = engine.generate_reply(session, _make_plan(), context, "ek minute rukiye", _LENDER)

        assert session.dialogue_state_name == "AWAIT_IDENTITY"
        assert session.identity_reprompted is True
        assert "Ravi Kumar" in out.reply_text

    def test_ambiguous_second_time_proceeds_to_conversation(self) -> None:
        engine = DialogueResponseEngine()
        session = _session()
        session.set_identity_reprompted(True)
        context = _make_context()

        out = engine.generate_reply(session, _make_plan(), context, "ek minute rukiye", _LENDER)

        assert session.dialogue_state_name == "CONVERSATION"
        assert "50,000" in out.reply_text


class TestConversationBuckets:
    def _in_conversation(self, name: str = "Sunita Sharma") -> tuple[DialogueResponseEngine, ConversationSessionState, CustomerContext]:
        engine = DialogueResponseEngine()
        session = _session()
        session.set_dialogue_state_name("CONVERSATION")
        session.set_last_ask("when_pay")
        context = _make_context(name=name)
        return engine, session, context

    def test_farewell_intent_closes_call(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan(intents=(IntentSignal(label=IntentLabel.DISCONNECT, confidence=0.9),))

        out = engine.generate_reply(session, plan, context, "bye", _LENDER)

        assert session.dialogue_state_name == "CLOSE"
        assert out.bucket == Bucket.FAREWELL

    def test_dispute_intent_routes_to_loan_denial_close(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan(intents=(IntentSignal(label=IntentLabel.DISPUTE, confidence=0.9),))

        out = engine.generate_reply(session, plan, context, "maine koi loan nahi liya", _LENDER)

        assert session.dialogue_state_name == "CLOSE"
        assert out.bucket == Bucket.LOAN_DENIAL
        assert "callback" in out.reply_text.lower()

    def test_identity_question_routes_to_ask_who(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan()

        out = engine.generate_reply(session, plan, context, "aap kaun ho", _LENDER)

        assert out.bucket == Bucket.ASK_WHO
        assert _LENDER in out.reply_text

    def test_amount_query_routes_to_ask_amount(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan()

        out = engine.generate_reply(session, plan, context, "kitna outstanding hai mera", _LENDER)

        assert out.bucket == Bucket.ASK_AMOUNT
        assert "50,000" in out.reply_text

    def test_hardship_intent_routes_to_hardship_bucket(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan(intents=(IntentSignal(label=IntentLabel.HARDSHIP, confidence=0.9),))

        out = engine.generate_reply(session, plan, context, "abhi paise nahi hain", _LENDER)

        assert out.bucket == Bucket.HARDSHIP
        assert session.last_ask == "part_payment"

    def test_promise_date_entity_routes_to_gives_date_and_updates_ledger(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan(entities={"PROMISE_DATE": "2026-08-30"})

        out = engine.generate_reply(session, plan, context, "30 August tak kar dunga", _LENDER)

        assert out.bucket == Bucket.GIVES_DATE
        assert session.last_ask == "confirm_date"
        assert session.commitment["date"] == "30 August"
        assert "30 August" in out.reply_text

    def test_date_entity_priority_beats_cooccurring_hardship_intent(self) -> None:
        """A concrete date in this turn must win over a co-occurring hardship
        signal — mirrors conv_server.py v3.13's fix for exactly this case."""
        engine, session, context = self._in_conversation()
        plan = _make_plan(
            intents=(IntentSignal(label=IntentLabel.HARDSHIP, confidence=0.9),),
            entities={"PROMISE_DATE": "2026-08-06"},
        )

        out = engine.generate_reply(session, plan, context, "abhi paise nahi, agle hafte tak", _LENDER)

        assert out.bucket == Bucket.GIVES_DATE

    def test_amount_entity_routes_to_gives_amount_and_computes_plan(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan(entities={"AMOUNT": "5000"})

        out = engine.generate_reply(session, plan, context, "5000 monthly de dunga", _LENDER)

        assert out.bucket == Bucket.GIVES_AMOUNT
        assert session.last_ask == "confirm_plan"
        assert session.commitment["amount_minor"] == 500_000
        assert "5,000" in out.reply_text

    def test_lumpsum_full_amount_uses_lumpsum_template(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan(entities={"AMOUNT": "50000"})

        out = engine.generate_reply(session, plan, context, "50000 ek baar mein de dunga", _LENDER)

        assert out.bucket == Bucket.GIVES_AMOUNT
        assert "clear हो जाएगा" in out.reply_text

    def test_bare_ack_before_confirm_re_anchors(self) -> None:
        engine, session, context = self._in_conversation()
        session.set_last_ask("when_pay")
        plan = _make_plan()

        out = engine.generate_reply(session, plan, context, "haan", _LENDER)

        assert out.bucket == Bucket.ACK
        assert session.dialogue_state_name == "CONVERSATION"
        assert "50,000" in out.reply_text

    def test_bare_ack_after_confirm_date_closes_softly(self) -> None:
        engine, session, context = self._in_conversation()
        session.set_last_ask("confirm_date")
        plan = _make_plan()

        out = engine.generate_reply(session, plan, context, "haan pakka", _LENDER)

        assert out.bucket == Bucket.ACK
        assert session.dialogue_state_name == "CLOSE"

    def test_unmatched_turn_anchors(self) -> None:
        engine, session, context = self._in_conversation()
        plan = _make_plan()

        out = engine.generate_reply(session, plan, context, "मुझे समझ नहीं आया कुछ भी", _LENDER)

        assert out.bucket == Bucket.ELSE
        assert "50,000" in out.reply_text


class TestClose:
    def test_close_state_repeats_farewell(self) -> None:
        engine = DialogueResponseEngine()
        session = _session()
        session.set_dialogue_state_name("CLOSE")
        context = _make_context()

        out = engine.generate_reply(session, _make_plan(), context, "bye", _LENDER)

        assert out.bucket is None
        assert out.dialogue_state_name == "CLOSE"


class TestRegisterGuardsApplied:
    def test_customer_name_never_appears_in_reply(self) -> None:
        engine, session, context = TestConversationBuckets()._in_conversation(name="Prateek Das")
        plan = _make_plan()

        out = engine.generate_reply(session, plan, context, "aap kaun ho", _LENDER)

        assert "Prateek" not in out.reply_text

    def test_empathy_directive_is_recorded_on_session(self) -> None:
        engine, session, context = TestConversationBuckets()._in_conversation()
        plan = _make_plan(intents=(IntentSignal(label=IntentLabel.HARDSHIP, confidence=0.9),))

        engine.generate_reply(session, plan, context, "abhi paise nahi hain", _LENDER)

        assert session.last_empathy_state == "HARDSHIP_FINANCIAL"


class TestInstallmentPlan:
    def test_monthly_plan_rounds_up(self) -> None:
        plan = compute_installment_plan(outstanding_minor=5_000_000, offered_amount_minor=400_000, cadence="monthly")

        assert plan.kind == InstallmentPlanKind.MONTHLY
        assert plan.months == 13

    def test_full_lumpsum(self) -> None:
        plan = compute_installment_plan(outstanding_minor=5_000_000, offered_amount_minor=5_000_000, cadence=None)

        assert plan.kind == InstallmentPlanKind.LUMPSUM_FULL

    def test_partial_lumpsum_tracks_remaining(self) -> None:
        plan = compute_installment_plan(outstanding_minor=5_000_000, offered_amount_minor=2_000_000, cadence="one-shot")

        assert plan.kind == InstallmentPlanKind.LUMPSUM_PARTIAL
        assert plan.remaining_minor == 3_000_000
