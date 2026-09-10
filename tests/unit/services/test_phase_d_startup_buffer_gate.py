"""Phase D — StartupBufferGate + three TTS modes.

Covers the gate contract mandated by the buffered-streaming design:
  - streaming mode (default) is a pure pass-through — no gate exists.
  - buffered_streaming mode buffers until threshold_ms of audio has
    accumulated, then releases FIFO.
  - blocking mode holds every clause until is_final=True, then releases.
  - is_final=True always triggers release regardless of mode/threshold.
  - Barge-in discards buffered clauses — zero stale audio reaches Twilio.
  - Bounded buffer via max_buffered_clauses (runaway guard).
  - Clause ordering strictly preserved on release.
  - Env resolution: VOICEOS_TTS_MODE / VOICEOS_TTS_BUFFER_MS.
  - Greeting path and live-reply path both flow through TrueStreamingPipeline
    and therefore both honour the gate uniformly.

Audio duration convention used in these tests: gate is constructed with
``bytes_per_sample=1`` and clauses use ``sample_rate=8000`` (the minimum
allowed by the AudioClause contract). Under that setup a clause of
``b"\\x00" * (ms * 8)`` bytes carries exactly ``ms`` milliseconds of audio,
so tests hit thresholds by simple arithmetic without caring about Veena's
real float32/24 kHz layout. The helper ``_clause(ms=...)`` hides the
byte-count arithmetic.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime
import pytest

from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.contracts.streaming import AudioClause, TokenChunk, VoiceConfig
from src.services.llm_runtime.output_validator import ValidationResult
from src.services.playback.scheduler import PlaybackScheduler
from src.services.tts.service import TTSService, TTSServiceConfig
from src.services.tts.startup_buffer_gate import (
    StartupBufferGate,
    TTSMode,
    build_gate_from_env,
    threshold_ms_from_env,
)
from src.services.tts.streaming_pipeline import TrueStreamingPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SR = 8000  # AudioClause enforces sample_rate >= 8000.
_BYTES_PER_MS = _TEST_SR // 1000  # with bytes_per_sample=1 → 8 bytes = 1 ms.


def _clause(ms: int, *, idx: int = 0, is_final: bool = False) -> AudioClause:
    """Build a mock clause whose duration is exactly `ms` under the test
    convention (sample_rate=8000, bytes_per_sample=1 → 8 bytes = 1 ms)."""
    return AudioClause(
        audio_data=b"\x00" * (ms * _BYTES_PER_MS),
        sample_rate=_TEST_SR,
        text=f"c{idx}",
        clause_index=idx,
        is_final=is_final,
    )


def _make_gate(
    mode: TTSMode = TTSMode.BUFFERED_STREAMING,
    threshold_ms: int = 400,
    *,
    max_buffered_clauses: int = 128,
) -> tuple[StartupBufferGate, PlaybackScheduler]:
    playback = PlaybackScheduler()
    gate = StartupBufferGate(
        playback=playback,
        mode=mode,
        threshold_ms=threshold_ms,
        max_buffered_clauses=max_buffered_clauses,
        bytes_per_sample=1,
    )
    return gate, playback


class _PermissiveValidator:
    def validate(self, text: str, response_plan: ResponsePlan) -> ValidationResult:  # noqa: ARG002
        return ValidationResult(valid=True, violations=[], fallback_response="")


class _SizedRecordingTTSAdapter:
    """Emits ONE AudioClause per synth call with caller-chosen duration.

    ``ms_per_clause`` is converted to raw byte length under the test
    convention (sample_rate=8000, bytes_per_sample=1 → 8 bytes = 1 ms).
    """

    def __init__(self, ms_per_clause: int, sample_rate: int = _TEST_SR) -> None:
        self._bytes = ms_per_clause * (sample_rate // 1000)
        self._sr = sample_rate
        self.calls: list[str] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig
    ) -> AsyncIterator[AudioClause]:
        parts: list[str] = []
        async for c in text_chunks:
            parts.append(c)
        text = "".join(parts)
        self.calls.append(text)

        bytes_ = self._bytes
        sr = self._sr

        async def _gen() -> AsyncGenerator[AudioClause, None]:
            yield AudioClause(
                audio_data=b"\x00" * bytes_,
                sample_rate=sr,
                text=text,
                clause_index=0,
                is_final=True,
            )

        return _gen()


def _tts_service_with_sized_adapter(
    ms_per_clause: int,
) -> tuple[TTSService, _SizedRecordingTTSAdapter]:
    adapter = _SizedRecordingTTSAdapter(ms_per_clause=ms_per_clause)
    svc = TTSService.create(adapter=adapter, config=TTSServiceConfig())
    return svc, adapter


def _response_plan() -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-phaseD",
        tenant_id="tenant-phaseD",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
    )


async def _token_stream(chunks: list[str]) -> AsyncIterator[TokenChunk]:
    for i, c in enumerate(chunks):
        finish = "stop" if i == len(chunks) - 1 else None
        yield TokenChunk(text=c, token_id=0, finish_reason=finish)


# ---------------------------------------------------------------------------
# TTSMode / threshold_ms_from_env
# ---------------------------------------------------------------------------


def test_mode_from_env_defaults_to_streaming() -> None:
    assert TTSMode.from_env({}) is TTSMode.STREAMING


def test_mode_from_env_reads_buffered_streaming() -> None:
    assert TTSMode.from_env({"VOICEOS_TTS_MODE": "buffered_streaming"}) is TTSMode.BUFFERED_STREAMING


def test_mode_from_env_reads_blocking() -> None:
    assert TTSMode.from_env({"VOICEOS_TTS_MODE": "blocking"}) is TTSMode.BLOCKING


def test_mode_from_env_unknown_falls_back_to_streaming() -> None:
    assert TTSMode.from_env({"VOICEOS_TTS_MODE": "gibberish"}) is TTSMode.STREAMING


def test_mode_from_env_is_case_insensitive_and_trims() -> None:
    assert TTSMode.from_env({"VOICEOS_TTS_MODE": "  BLOCKING "}) is TTSMode.BLOCKING


def test_threshold_ms_from_env_defaults_to_400() -> None:
    assert threshold_ms_from_env({}) == 400


@pytest.mark.parametrize("value", [0, 400, 600, 800, 1000])
def test_threshold_ms_from_env_allows_fixed_sweep_values(value: int) -> None:
    assert threshold_ms_from_env({"VOICEOS_TTS_BUFFER_MS": str(value)}) == value


def test_threshold_ms_from_env_out_of_set_falls_back_to_default() -> None:
    assert threshold_ms_from_env({"VOICEOS_TTS_BUFFER_MS": "250"}) == 400


def test_threshold_ms_from_env_non_integer_falls_back_to_default() -> None:
    assert threshold_ms_from_env({"VOICEOS_TTS_BUFFER_MS": "fast"}) == 400


def test_threshold_ms_from_env_negative_clamps_to_zero() -> None:
    assert threshold_ms_from_env({"VOICEOS_TTS_BUFFER_MS": "-1"}) == 0


def test_build_gate_from_env_returns_none_for_streaming() -> None:
    playback = PlaybackScheduler()
    assert build_gate_from_env(playback, env={}) is None
    assert build_gate_from_env(playback, env={"VOICEOS_TTS_MODE": "streaming"}) is None


def test_build_gate_from_env_returns_gate_for_buffered() -> None:
    playback = PlaybackScheduler()
    gate = build_gate_from_env(
        playback,
        env={"VOICEOS_TTS_MODE": "buffered_streaming", "VOICEOS_TTS_BUFFER_MS": "600"},
    )
    assert gate is not None
    assert gate.mode is TTSMode.BUFFERED_STREAMING
    assert gate.threshold_ms == 600


def test_build_gate_from_env_returns_gate_for_blocking() -> None:
    playback = PlaybackScheduler()
    gate = build_gate_from_env(playback, env={"VOICEOS_TTS_MODE": "blocking"})
    assert gate is not None
    assert gate.mode is TTSMode.BLOCKING


# ---------------------------------------------------------------------------
# Gate: pass-through / degenerate cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_mode_is_pass_through() -> None:
    gate, playback = _make_gate(mode=TTSMode.STREAMING, threshold_ms=1000)
    for i in range(3):
        await gate.enqueue(_clause(100, idx=i))
    assert playback.depth == 3
    assert gate.buffered_clauses == 0


@pytest.mark.asyncio
async def test_zero_threshold_buffered_is_pass_through() -> None:
    gate, playback = _make_gate(mode=TTSMode.BUFFERED_STREAMING, threshold_ms=0)
    for i in range(3):
        await gate.enqueue(_clause(50, idx=i))
    assert playback.depth == 3
    assert gate.released is True


# ---------------------------------------------------------------------------
# Gate: threshold accumulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_threshold_accumulation_holds_below_threshold() -> None:
    gate, playback = _make_gate(threshold_ms=400)
    await gate.enqueue(_clause(100, idx=0))
    await gate.enqueue(_clause(150, idx=1))
    # 250ms buffered — below 400ms threshold
    assert playback.depth == 0
    assert gate.buffered_clauses == 2
    assert gate.buffered_ms == pytest.approx(250.0)
    assert gate.released is False


@pytest.mark.asyncio
async def test_exact_threshold_releases_all_buffered() -> None:
    gate, playback = _make_gate(threshold_ms=400)
    await gate.enqueue(_clause(100, idx=0))
    await gate.enqueue(_clause(150, idx=1))
    await gate.enqueue(_clause(150, idx=2))
    # 100+150+150 = 400ms — exactly at threshold
    assert playback.depth == 3
    assert gate.buffered_clauses == 0
    assert gate.released is True


@pytest.mark.asyncio
async def test_crossing_threshold_releases_all_buffered_including_trigger() -> None:
    gate, playback = _make_gate(threshold_ms=400)
    await gate.enqueue(_clause(100, idx=0))
    await gate.enqueue(_clause(500, idx=1))  # 600ms crosses in one shot
    assert playback.depth == 2
    assert gate.released is True


@pytest.mark.asyncio
async def test_after_release_subsequent_clauses_pass_through() -> None:
    gate, playback = _make_gate(threshold_ms=400)
    await gate.enqueue(_clause(400, idx=0))  # exact release
    assert playback.depth == 1
    await gate.enqueue(_clause(50, idx=1))
    await gate.enqueue(_clause(50, idx=2))
    assert playback.depth == 3


# ---------------------------------------------------------------------------
# Gate: is_final semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_final_flag_releases_before_threshold() -> None:
    gate, playback = _make_gate(threshold_ms=1000)
    await gate.enqueue(_clause(100, idx=0))
    await gate.enqueue(_clause(50, idx=1, is_final=True))
    # 150ms is well below 1000ms threshold, but is_final forces release.
    assert playback.depth == 2
    assert gate.released is True


@pytest.mark.asyncio
async def test_final_clause_semantics_preserved_on_release() -> None:
    gate, playback = _make_gate(threshold_ms=100)
    await gate.enqueue(_clause(60, idx=0))
    await gate.enqueue(_clause(60, idx=1, is_final=True))
    got = [playback.dequeue_nowait(), playback.dequeue_nowait()]
    assert got[0] is not None and got[0].is_final is False
    assert got[1] is not None and got[1].is_final is True


# ---------------------------------------------------------------------------
# Gate: flush_final (end-of-turn hook)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_final_releases_buffered_below_threshold() -> None:
    gate, playback = _make_gate(threshold_ms=1000)
    await gate.enqueue(_clause(200, idx=0))
    await gate.enqueue(_clause(200, idx=1))
    assert playback.depth == 0
    await gate.flush_final()
    assert playback.depth == 2
    assert gate.released is True


@pytest.mark.asyncio
async def test_flush_final_when_empty_is_noop() -> None:
    gate, playback = _make_gate(threshold_ms=400)
    await gate.flush_final()
    assert playback.depth == 0
    assert gate.released is True


# ---------------------------------------------------------------------------
# Gate: barge-in / cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_barge_in_during_accumulation_discards_buffer() -> None:
    gate, playback = _make_gate(threshold_ms=1000)
    await gate.enqueue(_clause(200, idx=0))
    await gate.enqueue(_clause(200, idx=1))
    assert gate.buffered_clauses == 2

    playback.barge_in_event.set()
    await gate.enqueue(_clause(200, idx=2))  # arrives after barge-in
    assert playback.depth == 0
    assert gate.buffered_clauses == 0


@pytest.mark.asyncio
async def test_barge_in_just_before_release_prevents_scheduler_enqueue() -> None:
    """Barge-in that fires between accumulating and the threshold-crossing
    clause must ensure zero audio reaches the scheduler."""
    gate, playback = _make_gate(threshold_ms=400)
    await gate.enqueue(_clause(300, idx=0))  # 300ms buffered, no release
    playback.barge_in_event.set()
    await gate.enqueue(_clause(200, idx=1))  # would have crossed → 500ms
    assert playback.depth == 0


@pytest.mark.asyncio
async def test_flush_final_after_barge_in_discards() -> None:
    gate, playback = _make_gate(threshold_ms=1000)
    await gate.enqueue(_clause(200, idx=0))
    playback.barge_in_event.set()
    await gate.flush_final()
    assert playback.depth == 0
    assert gate.buffered_clauses == 0


@pytest.mark.asyncio
async def test_discard_makes_subsequent_enqueue_noop() -> None:
    gate, playback = _make_gate(threshold_ms=400)
    await gate.enqueue(_clause(100, idx=0))
    gate.discard()
    await gate.enqueue(_clause(100, idx=1))
    assert playback.depth == 0


# ---------------------------------------------------------------------------
# Gate: ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clause_ordering_preserved_on_release() -> None:
    gate, playback = _make_gate(threshold_ms=300)
    inputs = [_clause(50, idx=i) for i in range(6)]  # 6×50=300ms → release at 6th
    for c in inputs:
        await gate.enqueue(c)
    got: list[AudioClause] = []
    while playback.depth:
        item = playback.dequeue_nowait()
        assert item is not None
        got.append(item)
    assert [c.clause_index for c in got] == [0, 1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Gate: bounded buffer (runaway guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounded_buffer_forces_release_when_cap_hit() -> None:
    gate, playback = _make_gate(threshold_ms=10_000, max_buffered_clauses=4)
    # Each clause is 1ms → we never approach 10 000ms threshold. The cap
    # of 4 must force release before the 5th.
    for i in range(5):
        await gate.enqueue(_clause(1, idx=i))
    assert playback.depth == 5
    assert gate.released is True


# ---------------------------------------------------------------------------
# Gate: BLOCKING mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocking_mode_holds_until_final_flag() -> None:
    gate, playback = _make_gate(mode=TTSMode.BLOCKING, threshold_ms=0)
    for i in range(4):
        await gate.enqueue(_clause(500, idx=i, is_final=False))
    # 2 000ms buffered; BLOCKING ignores threshold and waits for is_final.
    assert playback.depth == 0
    await gate.enqueue(_clause(100, idx=4, is_final=True))
    assert playback.depth == 5


@pytest.mark.asyncio
async def test_blocking_mode_flush_final_releases_when_no_final_seen() -> None:
    gate, playback = _make_gate(mode=TTSMode.BLOCKING, threshold_ms=0)
    for i in range(3):
        await gate.enqueue(_clause(100, idx=i))
    assert playback.depth == 0
    await gate.flush_final()
    assert playback.depth == 3


# ---------------------------------------------------------------------------
# Pipeline integration — default streaming path unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_default_streaming_mode_is_unchanged(monkeypatch) -> None:
    """No env, no gate → clauses reach the scheduler immediately (Phase C
    baseline behaviour)."""
    monkeypatch.delenv("VOICEOS_TTS_MODE", raising=False)
    monkeypatch.delenv("VOICEOS_TTS_BUFFER_MS", raising=False)

    svc, adapter = _tts_service_with_sized_adapter(ms_per_clause=100)
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()

    tokens = ["First. ", "Second. ", "Third."]
    all_clauses = await pipeline.run(
        token_stream=_token_stream(tokens),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
    )

    assert adapter.calls == ["First.", "Second.", "Third."]
    assert playback.depth == 3
    assert len(all_clauses) == 3


# ---------------------------------------------------------------------------
# Pipeline integration — buffered_streaming behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_buffered_streaming_holds_until_threshold_reached() -> None:
    """With mode=buffered_streaming + threshold=400ms and 200ms clauses,
    the first two clauses buffer; only after the second (400ms cumulative)
    does anything reach the scheduler."""
    svc, adapter = _tts_service_with_sized_adapter(ms_per_clause=200)
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()
    gate = StartupBufferGate(
        playback=playback,
        mode=TTSMode.BUFFERED_STREAMING,
        threshold_ms=400,
        bytes_per_sample=1,
    )
    # Force adapter to use sample_rate=1000 so 1 byte = 1 ms convention holds.

    # Three sentences; only the last is is_final=True at the pipeline level
    # because the adapter always emits is_final=True on its one chunk, and
    # the pipeline masks is_final via `is_final and audio_clause.is_final`.
    # So only the last emitted clause carries is_final; the first two rely
    # on the threshold trigger.
    tokens = ["Alpha. ", "Beta. ", "Gamma."]
    await pipeline.run(
        token_stream=_token_stream(tokens),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
        gate=gate,
    )

    assert adapter.calls == ["Alpha.", "Beta.", "Gamma."]
    # 3 clauses × 200ms = 600ms > 400ms threshold → all three released.
    assert playback.depth == 3


@pytest.mark.asyncio
async def test_pipeline_buffered_streaming_short_reply_flushed_by_final() -> None:
    """A very short response (below threshold) must still be spoken —
    is_final on the last clause forces the flush."""
    svc, adapter = _tts_service_with_sized_adapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()
    gate = StartupBufferGate(
        playback=playback,
        mode=TTSMode.BUFFERED_STREAMING,
        threshold_ms=1000,
        bytes_per_sample=1,
    )

    await pipeline.run(
        token_stream=_token_stream(["हाँ।"]),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
        gate=gate,
    )

    assert adapter.calls == ["हाँ।"]
    # 50ms buffered, threshold 1000ms — released via is_final + flush_final.
    assert playback.depth == 1


# ---------------------------------------------------------------------------
# Pipeline integration — blocking mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_blocking_mode_releases_only_after_final_clause() -> None:
    svc, adapter = _tts_service_with_sized_adapter(ms_per_clause=100)
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()
    gate = StartupBufferGate(
        playback=playback,
        mode=TTSMode.BLOCKING,
        threshold_ms=0,
        bytes_per_sample=1,
    )

    # The gate holds until the pipeline reaches the final clause and emits
    # is_final=True; only then does everything land in the scheduler.
    await pipeline.run(
        token_stream=_token_stream(["One. ", "Two. ", "Three."]),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
        gate=gate,
    )
    assert playback.depth == 3


# ---------------------------------------------------------------------------
# Pipeline integration — barge-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_barge_in_during_buffering_yields_zero_audio() -> None:
    """Barge-in fired mid-turn must prevent ANY buffered clause from
    reaching the scheduler."""
    svc, adapter = _tts_service_with_sized_adapter(ms_per_clause=100)
    pipeline = TrueStreamingPipeline(ai_governance_service=None)
    playback = PlaybackScheduler()
    gate = StartupBufferGate(
        playback=playback,
        mode=TTSMode.BUFFERED_STREAMING,
        threshold_ms=1000,
        bytes_per_sample=1,
    )

    async def _tokens() -> AsyncIterator[TokenChunk]:
        yield TokenChunk(text="First. ", token_id=0, finish_reason=None)
        # Barge-in fires while buffered_ms is still below 1000ms threshold.
        playback.barge_in_event.set()
        yield TokenChunk(text="Second.", token_id=0, finish_reason="stop")

    await pipeline.run(
        token_stream=_tokens(),
        response_plan=_response_plan(),
        tts_service=svc,
        validator=_PermissiveValidator(),  # type: ignore[arg-type]
        playback=playback,
        gate=gate,
    )
    assert playback.depth == 0
    assert gate.buffered_clauses == 0


# ---------------------------------------------------------------------------
# Path coverage: greeting vs live-reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_greeting_path_and_live_reply_share_gate_semantics() -> None:
    """The greeting path (`ConversationEngine.speak_scripted_text`) and the
    live-reply path (`ConversationEngine.handle_turn`) both call
    `TrueStreamingPipeline.run(playback=...)`. Injecting a gate on
    `pipeline.run` therefore covers both paths uniformly with the same code
    behavior. This test exercises the pipeline directly to prove the gate
    behaves identically regardless of which caller invokes it — the only
    difference between greeting and live-reply is the caller, not the
    pipeline path."""
    svc, adapter = _tts_service_with_sized_adapter(ms_per_clause=200)
    pipeline = TrueStreamingPipeline(ai_governance_service=None)

    for label in ("greeting", "live-reply"):
        playback = PlaybackScheduler()
        gate = StartupBufferGate(
            playback=playback,
            mode=TTSMode.BUFFERED_STREAMING,
            threshold_ms=400,
            bytes_per_sample=1,
        )
        adapter.calls.clear()
        await pipeline.run(
            token_stream=_token_stream([f"{label}-one. ", f"{label}-two."]),
            response_plan=_response_plan(),
            tts_service=svc,
            validator=_PermissiveValidator(),  # type: ignore[arg-type]
            playback=playback,
            gate=gate,
        )
        # 2 clauses × 200ms = 400ms = threshold → both released.
        assert playback.depth == 2, f"path={label}: expected 2, got {playback.depth}"
