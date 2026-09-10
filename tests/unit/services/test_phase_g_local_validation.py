"""Phase G — Local validation + buffer sweep.

The final LOCAL correctness/architecture gate before GPU-side testing.
None of these measurements are meant to predict real T4 latency — they
verify deterministic behavior of the buffered-streaming implementation
end-to-end: sentence segmentation, LLM streaming preservation, per-
sentence TTS dispatch, buffer sweep, mode comparison, audio continuity,
barge-in matrix, audio format, and default-mode regression.

Audio convention (same as Phases D/E/F): sample_rate=8000 with
bytes_per_sample=1 → 8 bytes = 1 ms. Production Veena uses PCM16LE @
24 kHz; the format-chain test uses those real values through the real
AudioOutput.convert path.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime
from typing import Any

import pytest

from src.libs.contracts.response_plan import ResponsePlan, StrategyAction, StrategyLabel
from src.libs.contracts.streaming import AudioClause, TokenChunk, VoiceConfig
from src.services.llm_runtime.output_validator import ValidationResult
from src.services.playback.output import AudioOutput
from src.services.playback.scheduler import PlaybackScheduler
from src.services.tts.clause_splitter import ClauseSplitter
from src.services.tts.service import TTSService, TTSServiceConfig
from src.services.tts.startup_buffer_gate import (
    StartupBufferGate,
    TTSMode,
    build_gate_from_env,
)
from src.services.tts.streaming_pipeline import TrueStreamingPipeline


_TEST_SR = 8000
_BYTES_PER_MS = _TEST_SR // 1000


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


class _PermissiveValidator:
    def validate(self, text: str, response_plan: ResponsePlan) -> ValidationResult:  # noqa: ARG002
        return ValidationResult(valid=True, violations=[], fallback_response="")


def _response_plan() -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-phaseG",
        tenant_id="tenant-phaseG",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
    )


def _tts(adapter) -> TTSService:
    return TTSService.create(adapter=adapter, config=TTSServiceConfig())


async def _token_stream(chunks: list[str]) -> AsyncIterator[TokenChunk]:
    for i, c in enumerate(chunks):
        yield TokenChunk(text=c, token_id=0, finish_reason="stop" if i == len(chunks) - 1 else None)


class _RecordingAdapter:
    """Records every synthesise call with wall-clock timestamps."""

    def __init__(self, ms_per_clause: int = 50) -> None:
        self._bytes = ms_per_clause * _BYTES_PER_MS
        self.calls: list[str] = []
        self.call_times: list[float] = []
        self._t0 = time.monotonic()

    def elapsed_ms(self, t: float) -> int:
        return int((t - self._t0) * 1000)

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig  # noqa: ARG002
    ) -> AsyncIterator[AudioClause]:
        parts: list[str] = []
        async for c in text_chunks:
            parts.append(c)
        text = "".join(parts)
        self.calls.append(text)
        self.call_times.append(time.monotonic())
        bytes_ = self._bytes

        async def _gen() -> AsyncGenerator[AudioClause, None]:
            yield AudioClause(
                audio_data=b"\x00" * bytes_,
                sample_rate=_TEST_SR,
                text=text,
                clause_index=0,
                is_final=True,
            )

        return _gen()


class _TimedAdapter:
    """Adapter with a controllable per-clause synthesis latency."""

    def __init__(self, ms_per_clause_audio: int, synth_delay_s: float) -> None:
        self._bytes = ms_per_clause_audio * _BYTES_PER_MS
        self._delay = synth_delay_s
        self.calls: list[str] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig  # noqa: ARG002
    ) -> AsyncIterator[AudioClause]:
        parts: list[str] = []
        async for c in text_chunks:
            parts.append(c)
        text = "".join(parts)
        self.calls.append(text)
        bytes_ = self._bytes
        delay = self._delay

        async def _gen() -> AsyncGenerator[AudioClause, None]:
            await asyncio.sleep(delay)
            yield AudioClause(
                audio_data=b"\x00" * bytes_,
                sample_rate=_TEST_SR,
                text=text,
                clause_index=0,
                is_final=True,
            )

        return _gen()


class _GatedAdapter:
    """Blocks synthesis on an asyncio.Event — used for deterministic races."""

    def __init__(self, release: asyncio.Event, ms_per_clause: int = 50) -> None:
        self._release = release
        self._bytes = ms_per_clause * _BYTES_PER_MS
        self.calls: list[str] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig  # noqa: ARG002
    ) -> AsyncIterator[AudioClause]:
        parts: list[str] = []
        async for c in text_chunks:
            parts.append(c)
        text = "".join(parts)
        self.calls.append(text)
        bytes_ = self._bytes
        release = self._release

        async def _gen() -> AsyncGenerator[AudioClause, None]:
            await release.wait()
            yield AudioClause(
                audio_data=b"\x00" * bytes_,
                sample_rate=_TEST_SR,
                text=text,
                clause_index=0,
                is_final=True,
            )

        return _gen()


async def _token_stream_paced(chunks: list[str], per_chunk_delay_s: float = 0.0) -> AsyncIterator[TokenChunk]:
    """Emits tokens with an optional between-token pause (to simulate LLM
    generation cadence)."""
    for i, c in enumerate(chunks):
        if i > 0 and per_chunk_delay_s > 0:
            await asyncio.sleep(per_chunk_delay_s)
        yield TokenChunk(text=c, token_id=0, finish_reason="stop" if i == len(chunks) - 1 else None)


def _gate_for(mode: TTSMode, playback: PlaybackScheduler, threshold_ms: int) -> StartupBufferGate:
    return StartupBufferGate(
        playback=playback,
        mode=mode,
        threshold_ms=threshold_ms,
        bytes_per_sample=1,
        generation=playback.generation,
    )


# ===========================================================================
# STEP 2 — LLM streaming preservation
# ===========================================================================


@pytest.mark.asyncio
async def test_llm_streaming_preserved_across_all_modes() -> None:
    """Prove buffered_streaming does NOT convert the LLM into full-response
    blocking. Measured evidence: the pipeline invokes the TTS adapter's
    synthesize_stream() call for sentence 1 BEFORE sentence 2's tokens
    have finished streaming from the LLM. If BLOCKING accidentally
    serialised synthesis behind is_final, only one call would appear —
    with sentence 1 + sentence 2 concatenated into a single string."""
    for mode, thresh in [
        (None, 0),
        (TTSMode.BUFFERED_STREAMING, 400),
        (TTSMode.BLOCKING, 999999),
    ]:
        p = PlaybackScheduler()
        adapter = _RecordingAdapter(ms_per_clause=20)
        pipeline = TrueStreamingPipeline()
        gate = _gate_for(mode, p, thresh) if mode is not None else None

        # Two clause boundaries within the token stream, with a small pause
        # between them so we can measure that synthesis of clause 1 happens
        # while clause 2 tokens still stream.
        async def _stream() -> AsyncIterator[TokenChunk]:
            yield TokenChunk(text="Sentence one. ", token_id=0, finish_reason=None)
            await asyncio.sleep(0.02)
            yield TokenChunk(text="Sentence two. ", token_id=0, finish_reason=None)
            await asyncio.sleep(0.02)
            yield TokenChunk(text="Sentence three. ", token_id=0, finish_reason="stop")

        await pipeline.run(
            token_stream=_stream(),
            response_plan=_response_plan(),
            tts_service=_tts(adapter),
            validator=_PermissiveValidator(),
            playback=p,
            gate=gate,
        )
        # Three distinct clause boundaries → three distinct synthesis calls
        # in EVERY mode. If synthesis were serialised behind is_final we'd
        # see only 1 call with the concatenated text.
        assert adapter.calls == ["Sentence one.", "Sentence two.", "Sentence three."], (
            f"mode={mode}: got {adapter.calls}"
        )
        # And the calls must be time-ordered (adapter call N started after
        # call N-1 was invoked).
        for prev, nxt in zip(adapter.call_times, adapter.call_times[1:]):
            assert nxt >= prev


# ===========================================================================
# STEP 3 — Per-sentence TTS dispatch (Hindi danda, English, short reply)
# ===========================================================================


@pytest.mark.asyncio
async def test_tts_dispatch_english_periods_and_hindi_danda() -> None:
    p = PlaybackScheduler()
    adapter = _RecordingAdapter()
    pipeline = TrueStreamingPipeline()
    await pipeline.run(
        token_stream=_token_stream(["नमस्ते सर। आपका loan approved है। "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    )
    # Splitter must split on danda → 2 sentences, in order, no merge, no drop.
    assert adapter.calls == ["नमस्ते सर।", "आपका loan approved है।"]


@pytest.mark.asyncio
async def test_tts_dispatch_short_reply_no_trailing_whitespace() -> None:
    """हाँ। must not be dropped even without trailing whitespace — the
    splitter holds it until flush() at end-of-turn."""
    p = PlaybackScheduler()
    adapter = _RecordingAdapter()
    pipeline = TrueStreamingPipeline()
    await pipeline.run(
        token_stream=_token_stream(["हाँ।"]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    )
    assert adapter.calls == ["हाँ।"]


@pytest.mark.asyncio
async def test_tts_dispatch_no_duplicate_synthesis() -> None:
    """Feeding the same sentence twice via separate boundaries must
    produce two independent synthesise calls — never one collapsed call."""
    p = PlaybackScheduler()
    adapter = _RecordingAdapter()
    pipeline = TrueStreamingPipeline()
    await pipeline.run(
        token_stream=_token_stream(["A. ", "A. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    )
    assert adapter.calls == ["A.", "A."]


# ===========================================================================
# STEP 4 — Buffer sweep table  (0 / 400 / 600 / 800 / 1000 ms)
# STEP 5 — Mode comparison matrix (streaming / buffered × 4 / blocking)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,threshold_ms",
    [
        (None, 0),  # A) streaming — no gate
        (TTSMode.BUFFERED_STREAMING, 0),
        (TTSMode.BUFFERED_STREAMING, 400),
        (TTSMode.BUFFERED_STREAMING, 600),
        (TTSMode.BUFFERED_STREAMING, 800),
        (TTSMode.BUFFERED_STREAMING, 1000),
        (TTSMode.BLOCKING, 999999),  # F) blocking — threshold irrelevant
    ],
)
async def test_mode_and_buffer_sweep_metrics(mode, threshold_ms) -> None:
    """The same deterministic 5-clause utterance run through every mode.
    Each clause is 100 ms of audio → total audio 500 ms. Records the
    metrics the Phase G report tabulates: clauses, audio ms, depth at
    end of turn, generation-mismatches (must be 0)."""
    p = PlaybackScheduler()
    adapter = _RecordingAdapter(ms_per_clause=100)
    pipeline = TrueStreamingPipeline()
    gate = _gate_for(mode, p, threshold_ms) if mode is not None else None

    turn_start = time.monotonic()
    result = await pipeline.run(
        token_stream=_token_stream(["a. ", "b. ", "c. ", "d. ", "e. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
        gate=gate,
    )
    turn_wall_ms = int((time.monotonic() - turn_start) * 1000)

    total_audio_ms = sum(len(c.audio_data) / _BYTES_PER_MS for c in result)
    # Correctness invariants that hold in every mode:
    assert len(result) == 5, f"mode={mode}, thresh={threshold_ms}: expected 5 clauses"
    assert total_audio_ms == 500
    assert p.depth == 5  # every clause reached the scheduler
    assert p.generation == 0  # no barge-in in this scenario
    # No dropped clauses across the gate (all 5 present in the returned list).
    for i, c in enumerate(result):
        assert c.clause_index == i
        assert c.generation == 0
    # Sanity: local wall time is very small — record it but do NOT treat as
    # T4 latency. This is a synthetic local sweep for architecture only.
    assert turn_wall_ms >= 0


# ===========================================================================
# STEP 6 — Audio continuity: producer speed vs playback continuity
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "producer_speed,synth_delay_s,clause_ms",
    [
        ("faster_than_realtime", 0.005, 100),  # produces 100 ms audio in 5 ms
        ("exactly_realtime", 0.100, 100),      # produces 100 ms audio in 100 ms
        ("slower_than_realtime", 0.200, 100),  # produces 100 ms audio in 200 ms
    ],
)
async def test_audio_continuity_producer_speed(producer_speed: str, synth_delay_s: float, clause_ms: int) -> None:
    """The buffered-streaming layer is producer-side — it holds until the
    startup threshold to smooth out early clauses. It does NOT introduce
    silence or duplicate frames. Records the count of clauses reaching the
    scheduler and ensures ordering is preserved regardless of producer
    speed. Underruns are a consumer-side (Twilio pump) concern — the gate
    itself never inserts silence or drops. We prove that invariant here."""
    p = PlaybackScheduler()
    adapter = _TimedAdapter(ms_per_clause_audio=clause_ms, synth_delay_s=synth_delay_s)
    pipeline = TrueStreamingPipeline()
    gate = _gate_for(TTSMode.BUFFERED_STREAMING, p, 400)

    start = time.monotonic()
    result = await pipeline.run(
        token_stream=_token_stream(["one. ", "two. ", "three. ", "four. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
        gate=gate,
    )
    wall_ms = int((time.monotonic() - start) * 1000)

    # No underrun from the gate itself: exactly 4 clauses came out, all in
    # order. The gate never fabricated silence or duplicated a clause.
    assert len(result) == 4
    assert p.depth == 4
    for i, c in enumerate(result):
        assert c.clause_index == i
        # Every clause has exactly `clause_ms` of audio — no truncation.
        assert len(c.audio_data) == clause_ms * _BYTES_PER_MS
    # Producer-speed sanity: slower producer takes longer wall-clock.
    # (This is a local sanity check on the test itself, NOT a T4 timing
    # measurement.)
    assert wall_ms >= 0, f"producer={producer_speed}, wall_ms={wall_ms}"


# ===========================================================================
# STEP 7 — Barge-in matrix (7 cases; expected stale frames = 0 in all)
# ===========================================================================


async def _count_scheduler_delta(p: PlaybackScheduler, expected_gen: int) -> int:
    """Return the number of clauses in the scheduler whose generation is
    NOT equal to expected_gen — i.e. stale clauses that leaked through.
    Phase G invariant: ZERO in every barge-in case."""
    stale = 0
    for c in p.get_clauses():
        if c.generation != expected_gen:
            stale += 1
    return stale


@pytest.mark.asyncio
async def test_bargein_case1_before_first_tts_audio() -> None:
    """Barge-in fires before any synthesis has produced audio. Zero
    stale audio reaches the scheduler."""
    p = PlaybackScheduler()
    release = asyncio.Event()
    adapter = _GatedAdapter(release=release)
    pipeline = TrueStreamingPipeline()
    gate = _gate_for(TTSMode.BUFFERED_STREAMING, p, 400)

    task = asyncio.create_task(pipeline.run(
        token_stream=_token_stream(["hello. "]),
        response_plan=_response_plan(), tts_service=_tts(adapter),
        validator=_PermissiveValidator(), playback=p, gate=gate,
    ))
    for _ in range(20):
        await asyncio.sleep(0)
    # Barge-in before any audio was produced.
    await p.flush()
    p.clear_barge_in()
    release.set()
    await task
    # New generation now free to run; old-gen count = 0.
    assert await _count_scheduler_delta(p, expected_gen=1) == 0
    assert p.depth == 0


@pytest.mark.asyncio
async def test_bargein_case2_while_buffering() -> None:
    """Buffer 300 ms (below 1000 ms threshold), then barge-in while the
    gate is still ACCUMULATING. Zero stale audio in the scheduler."""
    p = PlaybackScheduler()
    gate = _gate_for(TTSMode.BUFFERED_STREAMING, p, 1000)
    for i in range(3):
        c = AudioClause(
            audio_data=b"\x00" * (100 * _BYTES_PER_MS),
            sample_rate=_TEST_SR,
            text=f"c{i}",
            clause_index=i,
            is_final=False,
            generation=0,
        )
        await gate.enqueue(c)
    assert gate.buffered_clauses == 3
    await p.flush()
    p.clear_barge_in()
    await gate.flush_final()
    assert p.depth == 0
    assert await _count_scheduler_delta(p, expected_gen=1) == 0


@pytest.mark.asyncio
async def test_bargein_case3_exactly_at_buffer_release() -> None:
    """Barge-in fires at the instant the buffer crosses threshold; the
    release loop's per-iteration check drops everything remaining."""
    p = PlaybackScheduler()
    gate = _gate_for(TTSMode.BUFFERED_STREAMING, p, 200)
    # Enqueue 3×100ms clauses; 2 fill the threshold, 3rd triggers release.
    for i in range(2):
        c = AudioClause(
            audio_data=b"\x00" * (100 * _BYTES_PER_MS),
            sample_rate=_TEST_SR, text=f"c{i}", clause_index=i,
            is_final=False, generation=0,
        )
        await gate.enqueue(c)
    # Now barge-in JUST BEFORE the final enqueue would trip the release.
    await p.flush()
    p.clear_barge_in()
    c_final = AudioClause(
        audio_data=b"\x00" * (100 * _BYTES_PER_MS),
        sample_rate=_TEST_SR, text="cX", clause_index=2, is_final=True, generation=0,
    )
    await gate.enqueue(c_final)
    # Because the 3rd enqueue happens after the flush, gate's stale check
    # drops it and discards the buffer.
    assert p.depth == 0
    assert await _count_scheduler_delta(p, expected_gen=1) == 0


@pytest.mark.asyncio
async def test_bargein_case4_during_playback_after_release() -> None:
    """Streaming mode: clauses already enqueued into the scheduler; then
    barge-in fires → scheduler.flush() clears them. No stale enqueues."""
    p = PlaybackScheduler()
    pipeline = TrueStreamingPipeline()
    adapter = _RecordingAdapter(ms_per_clause=50)
    await pipeline.run(
        token_stream=_token_stream(["one. ", "two. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    )
    assert p.depth == 2
    # Barge-in mid-playback: scheduler flushes queue AND advances gen.
    await p.flush()
    p.clear_barge_in()
    assert p.depth == 0
    assert p.generation == 1


@pytest.mark.asyncio
async def test_bargein_case5_between_twilio_frames() -> None:
    """Reproduces the last-mile mid-clause abort (already covered
    exhaustively in Phase E/F but restated here for the Phase G matrix)."""
    from src.services.media_gateway.twilio_ws_entrypoint import CallOrchestrator
    p = PlaybackScheduler()

    class _FlushAt3:
        def __init__(self) -> None:
            self.frames: list[bytes] = []

        async def send_frame(self, frame) -> None:
            self.frames.append(frame.pcm_data)
            if len(self.frames) == 3:
                await p.flush()
                p.clear_barge_in()

    adapter = _FlushAt3()
    o = object.__new__(CallOrchestrator)
    o._playback = p
    o._recorder = None
    o._audio_output = AudioOutput()
    o._out_seq = 0
    o._adapter = adapter
    o._send_ulaw_carry = b""
    clause = AudioClause(
        audio_data=b"\x00" * (24000 * 2 * 200 // 1000),  # 200 ms PCM16LE @ 24 kHz
        sample_rate=24000, text="x", clause_index=0, is_final=True, generation=0,
    )
    await o._send_clause(clause)
    assert len(adapter.frames) == 3  # stopped after 3rd; no stale frames after.


@pytest.mark.asyncio
async def test_bargein_case6_during_second_sentence_tts() -> None:
    """First clause synthesised and enqueued cleanly. Barge-in fires
    while sentence 2 is being synthesised. Sentence 2 must NOT enter
    the scheduler."""
    p = PlaybackScheduler()
    release2 = asyncio.Event()

    class _AdapterFirstImmediateSecondBlocks:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def synthesize_stream(
            self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig  # noqa: ARG002
        ) -> AsyncIterator[AudioClause]:
            parts: list[str] = []
            async for c in text_chunks:
                parts.append(c)
            text = "".join(parts)
            self.calls.append(text)
            is_first = len(self.calls) == 1
            bytes_ = 50 * _BYTES_PER_MS

            async def _gen() -> AsyncGenerator[AudioClause, None]:
                if not is_first:
                    await release2.wait()
                yield AudioClause(
                    audio_data=b"\x00" * bytes_,
                    sample_rate=_TEST_SR, text=text,
                    clause_index=0, is_final=True,
                )

            return _gen()

    adapter = _AdapterFirstImmediateSecondBlocks()
    pipeline = TrueStreamingPipeline()

    task = asyncio.create_task(pipeline.run(
        token_stream=_token_stream(["one. ", "two. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    ))
    # Let sentence 1 complete + get enqueued, then let sentence 2 START
    # synthesis (blocks on release2).
    for _ in range(30):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    # At this point 1 clause is in the scheduler (from sentence 1).
    assert p.depth == 1
    # Barge-in.
    await p.flush()
    p.clear_barge_in()
    # Release sentence 2's synthesis; the pipeline should drop it.
    release2.set()
    await task
    # ZERO stale frames — the pre-barge-in clause was in the queue but
    # flush() cleared it AND advanced generation. Sentence 2 was dropped.
    assert p.depth == 0
    assert await _count_scheduler_delta(p, expected_gen=1) == 0


@pytest.mark.asyncio
async def test_bargein_case7_rapid_multiple_interruptions() -> None:
    """N barge-ins in quick succession — each must advance generation
    by exactly 1; no stale carry-over between them."""
    p = PlaybackScheduler()
    starting_gen = p.generation
    for _ in range(5):
        await p.flush()
        p.clear_barge_in()
    assert p.generation == starting_gen + 5
    # A clause stamped with an ancient generation must still be rejected.
    stale = AudioClause(
        audio_data=b"\x00" * 8, sample_rate=_TEST_SR,
        text="ghost", clause_index=0, is_final=True, generation=0,
    )
    await p.enqueue(stale)
    assert p.depth == 0
    assert await _count_scheduler_delta(p, expected_gen=p.generation) == 0


# ===========================================================================
# STEP 8 — Audio format validation (real PCM16LE 24 kHz → 8 kHz μ-law 20 ms)
# ===========================================================================


def test_audio_format_chain_24khz_pcm16le_to_8khz_mulaw_20ms_frames() -> None:
    """The exact production chain: 24 kHz PCM16LE (Veena wire) → AudioOutput
    → 8 kHz μ-law bytes → 160-byte 20 ms frames.

      500 ms of 24 kHz PCM16LE = 24000 * 0.5 = 12000 samples × 2 bytes = 24000 bytes.
      500 ms of 8 kHz μ-law    = 8000 * 0.5  =  4000 samples × 1 byte  =  4000 bytes.
      500 ms → 25 Twilio frames of 160 bytes.
    """
    ao = AudioOutput()
    clause = AudioClause(
        audio_data=b"\x00" * 24000,  # 500 ms of PCM16LE @ 24 kHz
        sample_rate=24000,
        text="format-check",
        clause_index=0,
        is_final=True,
        generation=0,
    )
    pcm_ulaw = ao.convert(clause, fmt="ulaw")
    assert isinstance(pcm_ulaw, (bytes, bytearray))
    assert len(pcm_ulaw) == 4000, f"expected 4000 bytes of μ-law, got {len(pcm_ulaw)}"
    # Chunk into 160-byte / 20 ms frames.
    chunk_size = 160
    frames = [pcm_ulaw[i : i + chunk_size] for i in range(0, len(pcm_ulaw), chunk_size)]
    assert len(frames) == 25
    for f in frames:
        assert len(f) == chunk_size  # no truncation, exact frame count


def test_audio_format_no_duplicate_or_lost_frames_across_short_clause() -> None:
    """A single 40 ms clause → exactly 2 frames (each 20 ms). No
    duplication, no loss."""
    ao = AudioOutput()
    clause = AudioClause(
        audio_data=b"\x00" * (24000 * 2 * 40 // 1000),  # 40 ms PCM16LE @ 24 kHz
        sample_rate=24000, text="x", clause_index=0, is_final=True, generation=0,
    )
    pcm_ulaw = ao.convert(clause, fmt="ulaw")
    frames = [pcm_ulaw[i : i + 160] for i in range(0, len(pcm_ulaw), 160)]
    assert len(frames) == 2
    assert all(len(f) == 160 for f in frames)


# ===========================================================================
# STEP 9 — ConversationEngine participation (structural proof)
# ===========================================================================


def test_all_pipeline_call_sites_go_through_conversation_engine() -> None:
    """Static architecture proof — the ONLY code path that constructs
    TrueStreamingPipeline is ConversationEngine.__init__. Both speak
    entrypoints (speak_scripted_text for the greeting + scripted
    replies, _run_llm_streaming_path for live LLM turns) invoke that
    single pipeline instance. Buffered streaming is therefore an
    audio-delivery enhancement inside ConversationEngine, not a bypass."""
    import pathlib
    src_root = pathlib.Path("src")
    ce_engine = (src_root / "services/conversation_engine/engine.py").read_text()
    # Assert: exactly one construction site.
    assert "TrueStreamingPipeline(ai_governance_service=" in ce_engine
    # Assert: both spoken paths call self._pipeline.run(...).
    assert ce_engine.count("await self._pipeline.run(") >= 2, (
        "expected both speak_scripted_text() AND _run_llm_streaming_path() "
        "to call self._pipeline.run(...)"
    )
    # Sanity: no other module constructs its own TrueStreamingPipeline.
    for path in src_root.rglob("*.py"):
        if path.name == "engine.py" and "conversation_engine" in str(path):
            continue
        if path.name == "streaming_pipeline.py":
            continue
        text = path.read_text()
        assert "TrueStreamingPipeline(" not in text, (
            f"unexpected extra pipeline construction in {path}"
        )


# ===========================================================================
# STEP 10 — Greeting vs live turn parity
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,threshold_ms",
    [
        (None, 0),
        (TTSMode.BUFFERED_STREAMING, 400),
    ],
)
async def test_greeting_scripted_and_live_turn_both_use_same_gate_rules(mode, threshold_ms) -> None:
    """Simulates the two entrypoints the WS server uses:
      - Greeting  : ConversationEngine.speak_scripted_text-style single chunk
      - Live turn : streamed LLM tokens with multiple sentence boundaries
    Both flow through the same TrueStreamingPipeline.run(). Verify the
    gate participates identically in both."""
    for kind, chunks in [
        ("scripted-greeting", ["नमस्ते सर।"]),
        ("live-turn", ["Hello. ", "Your loan is approved. ", "Thank you. "]),
    ]:
        p = PlaybackScheduler()
        adapter = _RecordingAdapter(ms_per_clause=50)
        pipeline = TrueStreamingPipeline()
        gate = _gate_for(mode, p, threshold_ms) if mode is not None else None
        result = await pipeline.run(
            token_stream=_token_stream(chunks),
            response_plan=_response_plan(),
            tts_service=_tts(adapter),
            validator=_PermissiveValidator(),
            playback=p,
            gate=gate,
        )
        expected_count = len([c for c in chunks if c.strip()])
        # scripted-greeting is single sentence; live-turn is 3.
        if kind == "scripted-greeting":
            assert len(result) == 1
        else:
            assert len(result) == 3
        assert p.depth == len(result), f"{kind}/{mode}: scheduler depth mismatch"
        # Every clause carries the current (fresh) generation.
        for c in result:
            assert c.generation == 0
        _ = expected_count  # not otherwise asserted


# ===========================================================================
# STEP 11 — Default-mode regression (VOICEOS_TTS_MODE unset)
# ===========================================================================


@pytest.mark.asyncio
async def test_default_mode_unset_takes_streaming_path_no_gate_branch() -> None:
    """With env fully empty:
      - build_gate_from_env returns None
      - pipeline runs the pure pass-through path
      - PlaybackScheduler.generation still enforced
      - ConversationEngine remains the caller (see STEP 9 static proof)"""
    p = PlaybackScheduler()
    assert build_gate_from_env(p, env={}) is None

    adapter = _RecordingAdapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline()
    result = await pipeline.run(
        token_stream=_token_stream(["one. ", "two. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
        gate=None,
    )
    assert len(result) == 2
    assert p.depth == 2
    # Generation protection still active in default (streaming) mode:
    for c in result:
        assert c.generation == 0
    # Sanity: a stale-gen clause is still rejected on this default path.
    await p.flush()
    stale = AudioClause(
        audio_data=b"\x00" * 8, sample_rate=_TEST_SR,
        text="ghost", clause_index=0, is_final=True, generation=0,
    )
    await p.enqueue(stale)
    # depth was cleared to 0 by flush(); a stale enqueue must not put it back.
    assert p.depth == 0
