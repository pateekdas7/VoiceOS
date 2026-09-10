"""Phase C — single-authoritative ClauseSplitter behavior tests.

Covers the segmentation contract mandated by the buffered-streaming design:
  - . ? ! ; ।  are the only sentence boundaries
  - commas do NOT split (comma splits inside a sentence are removed)
  - decimals and abbreviations do not falsely split ("Rs.", "Dr.", "3.14")
  - short valid replies are never dropped ("हाँ।")
  - Hindi, Hinglish, currency, dates, loan-IDs, abbreviations all preserved
  - TrueStreamingPipeline uses ClauseSplitter (no downstream regex split)
  - VeenaAdapter no longer re-splits — one HTTP request per clause

No real GPU, no network. All adapters and TTS backends are mocked.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.contracts.streaming import AudioClause, TokenChunk, VoiceConfig
from src.services.gpu_scheduler.admission import AdmissionDecision
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.gpu_scheduler.vram_ledger import AllocationToken
from src.services.llm_runtime.output_validator import ValidationResult
from src.services.playback.scheduler import PlaybackScheduler
from src.services.tts.adapters.veena_adapter import VeenaAdapter
from src.services.tts.clause_splitter import ClauseSplitter
from src.services.tts.service import TTSService, TTSServiceConfig
from src.services.tts.streaming_pipeline import TrueStreamingPipeline


class _PermissiveValidator:
    """Test validator that accepts every clause.

    Phase C tests verify segmentation only; they must not be entangled
    with OutputValidator's own min-word / persona rules (which have their
    own dedicated tests). This double passes every candidate through
    unchanged so the pipeline's clause boundaries are what is measured.
    """

    def validate(self, text: str, response_plan: ResponsePlan) -> ValidationResult:  # noqa: ARG002
        return ValidationResult(valid=True, violations=[], fallback_response="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_clauses(splitter: ClauseSplitter, text_chunks: list[str]) -> list[str]:
    out: list[str] = []
    for c in text_chunks:
        out.extend(splitter.feed(c))
    tail = splitter.flush()
    if tail:
        out.append(tail)
    return out


async def _token_stream(chunks: list[str]) -> AsyncIterator[TokenChunk]:
    for i, c in enumerate(chunks):
        finish = "stop" if i == len(chunks) - 1 else None
        yield TokenChunk(text=c, token_id=0, finish_reason=finish)


def _response_plan() -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-phaseC",
        tenant_id="tenant-phaseC",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
    )


def _voice_config() -> VoiceConfig:
    return VoiceConfig(
        pitch_shift=0.0,
        rate_scale=1.0,
        energy_scale=1.0,
        pause_ms_after_clause=150,
        language="hi-IN",
    )


def _make_scheduler() -> GPUScheduler:
    scheduler = MagicMock(spec=GPUScheduler)
    tok = AllocationToken(token_id="t", device_id="gpu-0", model_id="veena", vram_mb=2048)
    scheduler.request_allocation.return_value = (AdmissionDecision.APPROVE, tok)
    scheduler.release_allocation.return_value = None
    return scheduler


class _RecordingTTSAdapter:
    """Records every text passed to synthesize_stream (concatenated into ONE
    text per call, since PhaseC contract says adapters receive one clause).
    Emits a single AudioClause per call so the pipeline sees ordered clauses.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig
    ) -> AsyncIterator[AudioClause]:
        parts: list[str] = []
        async for c in text_chunks:
            parts.append(c)
        text = "".join(parts)
        self.calls.append(text)

        async def _gen() -> AsyncGenerator[AudioClause, None]:
            yield AudioClause(
                audio_data=b"\x00" * 32,
                sample_rate=24_000,
                text=text,
                clause_index=0,
                is_final=True,
            )

        return _gen()


def _tts_service_with_recording_adapter() -> tuple[TTSService, _RecordingTTSAdapter]:
    adapter = _RecordingTTSAdapter()
    svc = TTSService.create(adapter=adapter, config=TTSServiceConfig())
    return svc, adapter


# ---------------------------------------------------------------------------
# ClauseSplitter — strong boundaries
# ---------------------------------------------------------------------------


def test_period_boundary_splits() -> None:
    assert _all_clauses(ClauseSplitter(), ["First. Second."]) == ["First.", "Second."]


def test_question_boundary_splits() -> None:
    assert _all_clauses(ClauseSplitter(), ["Ready? Yes."]) == ["Ready?", "Yes."]


def test_exclamation_boundary_splits() -> None:
    assert _all_clauses(ClauseSplitter(), ["Wow! Nice."]) == ["Wow!", "Nice."]


def test_semicolon_boundary_splits() -> None:
    assert _all_clauses(ClauseSplitter(), ["Alpha; Beta."]) == ["Alpha;", "Beta."]


def test_hindi_danda_splits() -> None:
    result = _all_clauses(
        ClauseSplitter(), ["आपका बकाया राशि है। क्या आप भुगतान कर सकते हैं?"]
    )
    assert len(result) == 2
    assert result[0].endswith("।")


# ---------------------------------------------------------------------------
# Comma no longer splits (Phase C removes weak boundary)
# ---------------------------------------------------------------------------


def test_comma_does_not_split_english() -> None:
    result = _all_clauses(
        ClauseSplitter(), ["Well, we understand your situation, and we can help."]
    )
    assert result == ["Well, we understand your situation, and we can help."]


def test_comma_does_not_split_hindi() -> None:
    result = _all_clauses(ClauseSplitter(), ["हाँ, ठीक है, मैं समझता हूँ।"])
    assert len(result) == 1
    assert result[0].endswith("।")


# ---------------------------------------------------------------------------
# Anti-split guards — decimals, currency, abbreviations
# ---------------------------------------------------------------------------


def test_decimal_number_not_split() -> None:
    assert _all_clauses(ClauseSplitter(), ["The rate is 3.14 today."]) == [
        "The rate is 3.14 today."
    ]


def test_currency_rs_not_split() -> None:
    assert _all_clauses(ClauseSplitter(), ["Amount is Rs. 5,000 today."]) == [
        "Amount is Rs. 5,000 today."
    ]


def test_currency_rs_terminal_period_still_splits() -> None:
    assert _all_clauses(ClauseSplitter(), ["Please pay Rs. 5,000. Thank you."]) == [
        "Please pay Rs. 5,000.",
        "Thank you.",
    ]


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("Dr. Sharma called me back.", ["Dr. Sharma called me back."]),
        ("Mr. Kumar and Mrs. Verma agreed.", ["Mr. Kumar and Mrs. Verma agreed."]),
        ("Contact Prof. Rao about it.", ["Contact Prof. Rao about it."]),
        ("Payment at 10 a.m. tomorrow.", ["Payment at 10 a.m. tomorrow."]),
        (
            "Ltd. and Pvt. Ltd. are common suffixes.",
            ["Ltd. and Pvt. Ltd. are common suffixes."],
        ),
    ],
)
def test_abbreviations_not_split(sentence: str, expected: list[str]) -> None:
    assert _all_clauses(ClauseSplitter(), [sentence]) == expected


def test_loan_id_with_dots_preserved() -> None:
    assert _all_clauses(ClauseSplitter(), ["Your loan ID 12345.678 is active."]) == [
        "Your loan ID 12345.678 is active."
    ]


# ---------------------------------------------------------------------------
# Short-reply preservation
# ---------------------------------------------------------------------------


def test_single_word_hindi_reply_not_dropped() -> None:
    assert _all_clauses(ClauseSplitter(), ["हाँ।"]) == ["हाँ।"]


def test_single_word_english_reply_not_dropped() -> None:
    assert _all_clauses(ClauseSplitter(), ["Yes."]) == ["Yes."]


def test_hinglish_reply_preserved() -> None:
    result = _all_clauses(ClauseSplitter(), ["Aap kaise hain? Main theek hoon."])
    assert result == ["Aap kaise hain?", "Main theek hoon."]


# ---------------------------------------------------------------------------
# Incremental streaming behavior
# ---------------------------------------------------------------------------


def test_incremental_feed_across_boundary() -> None:
    splitter = ClauseSplitter()
    got: list[str] = []
    got += splitter.feed("Hello wor")
    got += splitter.feed("ld. How ar")
    got += splitter.feed("e you?")
    tail = splitter.flush()
    if tail:
        got.append(tail)
    assert got == ["Hello world.", "How are you?"]


def test_incremental_feed_preserves_order() -> None:
    splitter = ClauseSplitter()
    got: list[str] = []
    for tok in ["A. ", "B. ", "C. ", "D."]:
        got += splitter.feed(tok)
    tail = splitter.flush()
    if tail:
        got.append(tail)
    assert got == ["A.", "B.", "C.", "D."]


def test_period_alone_at_end_of_buffer_waits_for_next_char() -> None:
    """`.` without trailing whitespace must NOT split (could be decimal)."""
    splitter = ClauseSplitter()
    assert splitter.feed("3.") == []
    assert splitter.feed("14 is pi.") == []
    tail = splitter.flush()
    assert tail == "3.14 is pi."


# ---------------------------------------------------------------------------
# TrueStreamingPipeline — end-to-end uses ClauseSplitter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_synthesises_each_sentence_in_order() -> None:
    svc, adapter = _tts_service_with_recording_adapter()
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()

    tokens = ["First sen", "tence. ", "Second sentence. ", "Third one."]
    all_clauses = await pipeline.run(
        token_stream=_token_stream(tokens),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
    )

    assert adapter.calls == ["First sentence.", "Second sentence.", "Third one."]
    assert len(all_clauses) == 3
    assert [c.is_final for c in all_clauses] == [False, False, True]


@pytest.mark.asyncio
async def test_pipeline_does_not_drop_short_hindi_reply() -> None:
    svc, adapter = _tts_service_with_recording_adapter()
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()

    all_clauses = await pipeline.run(
        token_stream=_token_stream(["हाँ।"]),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
    )

    assert adapter.calls == ["हाँ।"]
    assert len(all_clauses) == 1
    assert all_clauses[0].is_final is True


@pytest.mark.asyncio
async def test_pipeline_preserves_currency_and_abbreviations() -> None:
    svc, adapter = _tts_service_with_recording_adapter()
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()

    tokens = ["Dr. Sharma says pay Rs. 5,000 by 10 a.m. tomorrow. Thank you."]
    await pipeline.run(
        token_stream=_token_stream(tokens),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
    )

    assert adapter.calls == [
        "Dr. Sharma says pay Rs. 5,000 by 10 a.m. tomorrow.",
        "Thank you.",
    ]


@pytest.mark.asyncio
async def test_pipeline_barge_in_stops_before_flush() -> None:
    """When barge_in fires mid-turn, no further clauses (including flush)
    are synthesised."""
    svc, adapter = _tts_service_with_recording_adapter()
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()

    async def _tokens() -> AsyncIterator[TokenChunk]:
        yield TokenChunk(text="First one. ", token_id=0, finish_reason=None)
        # Simulate user barge-in after clause 1
        playback.barge_in_event.set()
        yield TokenChunk(text="Second one.", token_id=0, finish_reason="stop")

    await pipeline.run(
        token_stream=_tokens(),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
    )

    assert adapter.calls == ["First one."]


# ---------------------------------------------------------------------------
# VeenaAdapter — no longer re-splits
# ---------------------------------------------------------------------------


def _capture_stream_clause_calls(calls: list[str]) -> Any:
    async def _gen(
        text: str,
        voice_config: VoiceConfig,
        clause_idx: int,
        is_final_clause: bool,
    ) -> AsyncIterator[AudioClause]:
        calls.append(text)
        yield AudioClause(
            audio_data=b"\x00" * 32,
            sample_rate=24000,
            text=text,
            clause_index=clause_idx,
            is_final=is_final_clause,
        )

    return _gen


@pytest.mark.asyncio
async def test_veena_adapter_does_not_re_split_input() -> None:
    scheduler = _make_scheduler()
    adapter = VeenaAdapter(gpu_scheduler=scheduler)

    calls: list[str] = []
    with patch.object(adapter, "_stream_clause", side_effect=_capture_stream_clause_calls(calls)):
        async def _chunks() -> AsyncIterator[str]:
            yield "Alpha, beta. "
            yield "Gamma; delta! "
            yield "Epsilon?"

        gen = await adapter.synthesize_stream(_chunks(), _voice_config())
        results = [c async for c in gen]

    assert len(calls) == 1, f"Adapter must not split; got {len(calls)} calls: {calls}"
    assert calls[0] == "Alpha, beta. Gamma; delta! Epsilon?"
    assert len(results) == 1
    assert results[0].is_final is True
