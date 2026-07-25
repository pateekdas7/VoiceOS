"""Unit tests for TrueStreamingPipeline's persona/register guard gate
(Path-A Call-002 readiness).

Found via a real LLM-fallback validation run against real GPU infra
(scripts/path_a_llm_fallback_validation.py): the LLM streaming path had no
persona/register enforcement at all — a real LLM-generated reply addressed
the customer by name and used a blocked literary word. The scripted golden
path was already clean (DialogueResponseEngine runs RegisterGuard itself
before ever calling speak_scripted_text()), so this gate is defense-in-depth
there and the FIRST enforcement point for the LLM path.

Architecture: V2 Ch13 (persona/register rules); V1 Ch18 (True Streaming
Pipeline).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime

from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.contracts.streaming import AudioClause, TokenChunk, VoiceConfig
from src.services.llm_runtime.output_validator import OutputValidator
from src.services.playback.scheduler import PlaybackScheduler
from src.services.tts.streaming_pipeline import TrueStreamingPipeline


class _MockTTSAdapter:
    """Echoes each text chunk back as a single AudioClause (no real inference)."""

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig
    ) -> AsyncIterator[AudioClause]:
        async def _gen() -> AsyncGenerator[AudioClause, None]:
            collected: list[str] = []
            async for chunk in text_chunks:
                collected.append(chunk)
            text = "".join(collected)
            yield AudioClause(audio_data=b"\x00" * 128, sample_rate=24_000, text=text, clause_index=0, is_final=True)

        return _gen()


async def _single_token_stream(text: str) -> AsyncIterator[TokenChunk]:
    async def _gen() -> AsyncGenerator[TokenChunk, None]:
        yield TokenChunk(text=text, token_id=1, finish_reason="stop")

    return _gen()


def _response_plan() -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-1",
        tenant_id="tenant-1",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
    )


def _tts_service() -> object:
    from src.services.tts.service import TTSService, TTSServiceConfig

    return TTSService.create(adapter=_MockTTSAdapter(), config=TTSServiceConfig())


class TestRegisterGuardGate:
    async def test_customer_name_is_scrubbed_from_llm_output(self) -> None:
        pipeline = TrueStreamingPipeline(ai_governance_service=None)
        playback = PlaybackScheduler()
        plan = _response_plan()

        clauses = await pipeline.run(
            token_stream=await _single_token_stream("Namaste Prateek Das sir, kaise hain aap?"),
            response_plan=plan,
            tts_service=_tts_service(),
            validator=OutputValidator(),
            playback=playback,
            customer_name="Prateek Das",
        )

        assert len(clauses) == 1
        assert "Prateek" not in clauses[0].text

    async def test_no_customer_name_argument_leaves_reply_untouched(self) -> None:
        """customer_name="" (the default) must be a no-op — callers that
        intentionally speak the customer's name (the AWAIT_IDENTITY
        greeting/reask) omit this argument rather than pass it."""
        pipeline = TrueStreamingPipeline(ai_governance_service=None)
        playback = PlaybackScheduler()
        plan = _response_plan()

        clauses = await pipeline.run(
            token_stream=await _single_token_stream("Namaste Prateek Das sir, kaise hain aap?"),
            response_plan=plan,
            tts_service=_tts_service(),
            validator=OutputValidator(),
            playback=playback,
        )

        assert len(clauses) == 1
        assert "Prateek" in clauses[0].text

    async def test_literary_word_triggers_safe_fallback_on_final_clause(self) -> None:
        pipeline = TrueStreamingPipeline(ai_governance_service=None)
        playback = PlaybackScheduler()
        plan = _response_plan()

        clauses = await pipeline.run(
            token_stream=await _single_token_stream("Aapka भुगतान समय पर करें।"),
            response_plan=plan,
            tts_service=_tts_service(),
            validator=OutputValidator(),
            playback=playback,
        )

        assert len(clauses) == 1
        assert "भुगतान" not in clauses[0].text

    async def test_clean_reply_passes_through_unmodified(self) -> None:
        """A reply with no register violations and no customer-name mention
        is unaffected by the guard — content differences here come only
        from TTSService's own Hindi script conversion stage (Roman->
        Devanagari), the same pre-existing behavior test_auth_integration.py
        documents, not from the register guard added in this change."""
        pipeline = TrueStreamingPipeline(ai_governance_service=None)
        playback = PlaybackScheduler()
        plan = _response_plan()

        clauses = await pipeline.run(
            token_stream=await _single_token_stream("Sir, aapka payment kab tak ho jaayega?"),
            response_plan=plan,
            tts_service=_tts_service(),
            validator=OutputValidator(),
            playback=playback,
            customer_name="Sunita Sharma",
        )

        assert len(clauses) == 1
        assert "payment" in clauses[0].text
        assert "Sunita" not in clauses[0].text
