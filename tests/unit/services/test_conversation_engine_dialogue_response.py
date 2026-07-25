"""Unit tests for ConversationEngine's Phase 6g scripted-response wiring:
speak_scripted_text() and the dialogue_response primary-path branch in
handle_turn().

Architecture: V2 Ch13 (dialogue state); RI-4 (commit-before-act).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.libs.contracts.context import ConsentStatus, ContactInfo, CustomerContext, LoanSummary, OutstandingBalance, PartyInfo
from src.libs.contracts.primitives import AccountId, Currency, CustomerId, Money, TenantId
from src.libs.contracts.response_plan import ResponsePlan
from src.libs.contracts.streaming import AudioClause, VoiceConfig
from src.libs.contracts.turn import TurnInput, TurnRole
from src.services.ai_governance.service import AIGovernanceService
from src.services.conversation_engine.engine import CILPort, ConversationEngine, PromptBuilderPort
from src.services.knowledge_retrieval.service import KnowledgeRetrievalService
from src.services.llm_runtime.output_validator import ValidationResult
from src.services.playback.scheduler import PlaybackScheduler

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeTTS:
    """Echoes each text chunk it receives back as one AudioClause.

    synthesize_stream() itself must be a plain coroutine that RETURNS an
    async iterator (matching TTSService's real signature — `await
    tts.synthesize_stream(...)` then `async for ... in clause_stream`), not
    an async-generator function itself (that would make the call site's
    `await` fail with "async_generator can't be used in 'await'").
    """

    def __init__(self) -> None:
        self.received_texts: list[str] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig | None = None
    ) -> AsyncIterator[AudioClause]:
        return self._generate(text_chunks)

    async def _generate(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[AudioClause]:
        async for chunk in text_chunks:
            self.received_texts.append(chunk)
            yield AudioClause(audio_data=b"pcm", sample_rate=24000, text=chunk, clause_index=0, is_final=True)


class _AlwaysValidValidator:
    def validate(self, text: str, response_plan: ResponsePlan) -> ValidationResult:
        return ValidationResult(valid=True)


@dataclass(frozen=True)
class _FakeDialogueTurnOutput:
    reply_text: str


class _FakeDialogueResponse:
    def __init__(self, reply_text: str = "Perfect sir, aapka payment kab tak ho jaayega?") -> None:
        self.reply_text = reply_text
        self.calls: list[tuple[Any, ...]] = []

    def generate_reply(
        self,
        session: Any,
        response_plan: ResponsePlan,
        context: CustomerContext | None,
        user_text: str,
        lender_name: str,
    ) -> _FakeDialogueTurnOutput:
        self.calls.append((session, response_plan, context, user_text, lender_name))
        return _FakeDialogueTurnOutput(reply_text=self.reply_text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(
    dialogue_response: _FakeDialogueResponse | None = None,
    llm_service: Any = None,
    tts_service: Any = None,
    validator: Any = None,
) -> ConversationEngine:
    return ConversationEngine(
        cil=cast(CILPort, MagicMock()),
        prompt_builder=cast(PromptBuilderPort, MagicMock(build=MagicMock(return_value=("prompt", "hash")))),
        llm_service=llm_service if llm_service is not None else MagicMock(),
        tts_service=tts_service if tts_service is not None else _FakeTTS(),
        validator=validator if validator is not None else _AlwaysValidValidator(),
        knowledge=cast(KnowledgeRetrievalService, MagicMock(retrieve=AsyncMock(return_value=[]))),
        quality_scorer=cast(Any, MagicMock()),
        ai_governance_service=AIGovernanceService.create(),
        dialogue_response=dialogue_response,
        lender_name="Rajat Finance",
    )


def _make_turn(call_id: str = "call-1", transcript: str = "haan speaking") -> TurnInput:
    return TurnInput(
        turn_id=str(uuid.uuid4()),
        call_id=call_id,
        tenant_id="tenant-1",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(),
        created_at=datetime.utcnow(),
        correlation_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        turn_index=0,
    )


def _make_context() -> CustomerContext:
    loan = LoanSummary(
        account_id=AccountId("acc-001"),
        product_type="PERSONAL_LOAN",
        outstanding_balance=Money(amount_minor=500_000, currency=Currency.INR),
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
            total_outstanding=Money(amount_minor=500_000, currency=Currency.INR),
            total_overdue=Money(amount_minor=500_000, currency=Currency.INR),
            account_count=1,
        ),
        consent_status=ConsentStatus.GRANTED,
    )


def _stub_cil(engine: ConversationEngine) -> ResponsePlan:
    """Configure engine._cil.assemble() to return a minimal valid plan; return that plan."""
    from src.libs.contracts.decision import DecisionEnvelope

    plan = ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-1",
        tenant_id="tenant-1",
        created_at=datetime.utcnow(),
    )
    envelope = DecisionEnvelope(
        envelope_id=str(uuid.uuid4()),
        call_id="call-1",
        tenant_id="tenant-1",
        timestamp=datetime.utcnow(),
        response_plan_id=plan.plan_id,
    )
    engine._cil.assemble = MagicMock(return_value=(plan, envelope))  # type: ignore[attr-defined]
    return plan


# ---------------------------------------------------------------------------
# speak_scripted_text()
# ---------------------------------------------------------------------------


class TestSpeakScriptedText:
    @pytest.mark.asyncio
    async def test_synthesizes_fixed_text_through_the_real_pipeline(self) -> None:
        tts = _FakeTTS()
        engine = _make_engine(tts_service=tts)
        playback = PlaybackScheduler()

        clauses = await engine.speak_scripted_text("Namaste sir, main Kavya bol rahi hoon.", playback)

        assert len(clauses) >= 1
        assert "Namaste sir" in tts.received_texts[0]

    @pytest.mark.asyncio
    async def test_works_without_an_explicit_response_plan(self) -> None:
        engine = _make_engine()
        playback = PlaybackScheduler()

        clauses = await engine.speak_scripted_text("Theek hai sir, thanks.", playback, response_plan=None)

        assert len(clauses) >= 1

    @pytest.mark.asyncio
    async def test_rejected_by_validator_yields_no_clause(self) -> None:
        class _AlwaysRejectValidator:
            def validate(self, text: str, response_plan: ResponsePlan) -> ValidationResult:
                return ValidationResult(valid=False, violations=["bad"], fallback_response="")

        engine = _make_engine(validator=_AlwaysRejectValidator())
        playback = PlaybackScheduler()

        clauses = await engine.speak_scripted_text("some text", playback)

        assert clauses == []


# ---------------------------------------------------------------------------
# handle_turn() — scripted path takes over when dialogue_response is wired
# ---------------------------------------------------------------------------


class TestHandleTurnScriptedPath:
    @pytest.mark.asyncio
    async def test_dialogue_response_wired_skips_llm_and_uses_scripted_reply(self) -> None:
        dialogue_response = _FakeDialogueResponse(reply_text="Perfect sir, outstanding hai.")
        llm = MagicMock()
        llm.generate_stream = AsyncMock()
        tts = _FakeTTS()
        engine = _make_engine(dialogue_response=dialogue_response, llm_service=llm, tts_service=tts)
        _stub_cil(engine)
        playback = PlaybackScheduler()

        clauses = await engine.handle_turn(_make_turn(), playback, context=_make_context())

        llm.generate_stream.assert_not_awaited()
        assert len(dialogue_response.calls) == 1
        assert len(clauses) >= 1
        assert "Perfect sir" in tts.received_texts[0]

    @pytest.mark.asyncio
    async def test_dialogue_response_receives_transcript_and_lender_name(self) -> None:
        dialogue_response = _FakeDialogueResponse()
        engine = _make_engine(dialogue_response=dialogue_response)
        _stub_cil(engine)
        playback = PlaybackScheduler()
        context = _make_context()

        await engine.handle_turn(_make_turn(transcript="30 August tak kar dunga"), playback, context=context)

        _session, _plan, passed_context, user_text, lender_name = dialogue_response.calls[0]
        assert user_text == "30 August tak kar dunga"
        assert lender_name == "Rajat Finance"
        assert passed_context is context

    @pytest.mark.asyncio
    async def test_no_dialogue_response_uses_llm_path_unchanged(self) -> None:
        """Regression guard: engines constructed without dialogue_response
        (every pre-Phase-6g caller/test) must keep using the LLM path."""
        engine = _make_engine(dialogue_response=None)
        _stub_cil(engine)
        playback = PlaybackScheduler()

        async def _empty_stream() -> AsyncIterator[Any]:
            return
            yield  # pragma: no cover

        engine._llm.generate_stream = AsyncMock(return_value=_empty_stream())  # type: ignore[attr-defined]

        clauses = await engine.handle_turn(_make_turn(), playback, context=_make_context())

        engine._llm.generate_stream.assert_awaited_once()  # type: ignore[attr-defined]
        assert clauses == []
