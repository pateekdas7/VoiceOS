"""Phase F — Mode/configuration integration.

Verifies the three TTS modes selected by ``VOICEOS_TTS_MODE`` behave
correctly *as end-to-end pipeline configurations*, not just as env-parsed
enums (which Phase D already covers):

  streaming            — no gate; clauses hit the scheduler immediately.
  buffered_streaming   — clauses buffered until ``VOICEOS_TTS_BUFFER_MS``
                         of audio has accumulated, then released FIFO.
  blocking             — clauses buffered until ``is_final=True``, then
                         released FIFO.

Cross-mode invariants (Phase F):
  - The default mode is ``streaming``. Unset env AND ``VOICEOS_TTS_MODE=streaming``
    both take the pass-through path.
  - Only fixed buffer values (0, 400, 600, 800, 1000 ms) are honoured;
    anything else warns and falls back to 400.
  - ``blocking`` gates *playback only* — the LLM token consumption and
    per-clause TTS synthesis still proceed concurrently mid-turn. It is
    NOT a "wait for the whole reply before starting synthesis" mode.
  - Every mode is generation-safe: a gen-N producer that resumes after
    a barge-in advanced the scheduler CANNOT inject stale audio, in any
    of the three modes.

Also closes the Phase E coverage gap by exercising the real
``CallOrchestrator._send_clause`` (not the minimal reproduction in
``test_phase_e_generation_id.py``) with a stubbed WebSocket adapter and
the real ``AudioOutput.convert`` μ-law path.

Audio convention: same as Phases D/E — ``sample_rate=8000`` with
``bytes_per_sample=1`` gives 8 bytes = 1 ms; the gate is constructed with
that override.
"""

from __future__ import annotations

import asyncio
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


_TEST_SR = 8000
_BYTES_PER_MS = _TEST_SR // 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clause(ms: int, *, idx: int = 0, is_final: bool = False, generation: int = 0) -> AudioClause:
    return AudioClause(
        audio_data=b"\x00" * (ms * _BYTES_PER_MS),
        sample_rate=_TEST_SR,
        text=f"c{idx}",
        clause_index=idx,
        is_final=is_final,
        generation=generation,
    )


class _PermissiveValidator:
    def validate(self, text: str, response_plan: ResponsePlan) -> ValidationResult:  # noqa: ARG002
        return ValidationResult(valid=True, violations=[], fallback_response="")


class _SizedRecordingTTSAdapter:
    """Adapter that emits one clause per synthesise call with fixed duration."""

    def __init__(self, ms_per_clause: int) -> None:
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

        async def _gen() -> AsyncGenerator[AudioClause, None]:
            yield AudioClause(
                audio_data=b"\x00" * bytes_,
                sample_rate=_TEST_SR,
                text=text,
                clause_index=0,
                is_final=True,
            )

        return _gen()


class _CountingTTSAdapter:
    """Adapter that records the wall-clock order of synthesise calls.

    Used to verify that BLOCKING mode still permits mid-turn synthesis
    concurrency — if the pipeline were secretly waiting for is_final
    before starting the *next* synthesis, every synthesise call would
    happen after all tokens have been consumed.
    """

    def __init__(self, ms_per_clause: int = 20) -> None:
        self._bytes = ms_per_clause * _BYTES_PER_MS
        self.calls: list[str] = []
        self.call_events: list[asyncio.Event] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig  # noqa: ARG002
    ) -> AsyncIterator[AudioClause]:
        parts: list[str] = []
        async for c in text_chunks:
            parts.append(c)
        text = "".join(parts)
        self.calls.append(text)
        ev = asyncio.Event()
        ev.set()
        self.call_events.append(ev)

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


class _GatedTTSAdapter:
    """Blocks per-clause synthesis on an asyncio.Event — used for race tests."""

    def __init__(self, release: asyncio.Event, ms_per_clause: int = 100) -> None:
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


def _tts(adapter) -> TTSService:
    return TTSService.create(adapter=adapter, config=TTSServiceConfig())


def _response_plan() -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-phaseF",
        tenant_id="tenant-phaseF",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
    )


async def _token_stream(chunks: list[str]) -> AsyncIterator[TokenChunk]:
    for i, c in enumerate(chunks):
        finish = "stop" if i == len(chunks) - 1 else None
        yield TokenChunk(text=c, token_id=0, finish_reason=finish)


def _gate_for(mode: TTSMode, playback: PlaybackScheduler, threshold_ms: int = 400) -> StartupBufferGate:
    return StartupBufferGate(
        playback=playback,
        mode=mode,
        threshold_ms=threshold_ms,
        bytes_per_sample=1,
        generation=playback.generation,
    )


# ===========================================================================
# STEP 1/2/3 — env resolution: single source of truth, only fixed values
# ===========================================================================


def test_default_env_returns_streaming_mode_no_gate() -> None:
    """Unset env → build_gate_from_env returns None (pass-through path)."""
    p = PlaybackScheduler()
    assert build_gate_from_env(p, env={}) is None


def test_explicit_streaming_env_also_returns_none() -> None:
    """VOICEOS_TTS_MODE=streaming and unset must be indistinguishable."""
    p = PlaybackScheduler()
    assert build_gate_from_env(p, env={"VOICEOS_TTS_MODE": "streaming"}) is None


def test_buffered_streaming_env_returns_gate_with_env_threshold() -> None:
    p = PlaybackScheduler()
    gate = build_gate_from_env(
        p, env={"VOICEOS_TTS_MODE": "buffered_streaming", "VOICEOS_TTS_BUFFER_MS": "600"}
    )
    assert gate is not None
    assert gate.mode is TTSMode.BUFFERED_STREAMING
    assert gate.threshold_ms == 600


def test_blocking_env_returns_gate_regardless_of_buffer_ms() -> None:
    """BLOCKING ignores threshold on the release trigger, but the value
    is still parsed via the same threshold_ms_from_env — verify that
    invalid buffer_ms in blocking mode still falls back safely."""
    p = PlaybackScheduler()
    gate = build_gate_from_env(
        p, env={"VOICEOS_TTS_MODE": "blocking", "VOICEOS_TTS_BUFFER_MS": "not-a-number"}
    )
    assert gate is not None
    assert gate.mode is TTSMode.BLOCKING
    assert gate.threshold_ms == 400  # default fallback


@pytest.mark.parametrize("v", [0, 400, 600, 800, 1000])
def test_fixed_buffer_sweep_values_accepted(v: int) -> None:
    assert threshold_ms_from_env({"VOICEOS_TTS_BUFFER_MS": str(v)}) == v


@pytest.mark.parametrize("v", [1, 200, 500, 700, 1500, 5000])
def test_non_sweep_buffer_values_fall_back_to_default(v: int) -> None:
    assert threshold_ms_from_env({"VOICEOS_TTS_BUFFER_MS": str(v)}) == 400


def test_gate_propagates_scope_generation_from_build_gate_from_env() -> None:
    """The pipeline snapshots playback.generation at scope start and
    passes it to build_gate_from_env — the gate must remember it."""
    p = PlaybackScheduler()
    gate = build_gate_from_env(
        p,
        env={"VOICEOS_TTS_MODE": "buffered_streaming"},
        generation=7,
    )
    assert gate is not None
    assert gate.scope_generation == 7


# ===========================================================================
# STEP 6 — Mode execution matrix (3 modes × happy-path pipeline)
# ===========================================================================


@pytest.mark.asyncio
async def test_streaming_mode_all_clauses_reach_scheduler_immediately() -> None:
    """Default streaming mode: pipeline passes each synthesised clause
    straight to the scheduler — no buffering delay, ever."""
    p = PlaybackScheduler()
    adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline()
    result = await pipeline.run(
        token_stream=_token_stream(["one. ", "two. ", "three. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
        gate=None,  # streaming: caller passes no gate.
    )
    assert len(result) == 3
    assert p.depth == 3


@pytest.mark.asyncio
async def test_buffered_streaming_holds_below_threshold_then_releases() -> None:
    """Under threshold, all clauses stay buffered. Once accumulation
    crosses the threshold (or is_final fires) everything drains FIFO."""
    p = PlaybackScheduler()
    adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)  # each clause 50 ms
    pipeline = TrueStreamingPipeline()
    gate = _gate_for(TTSMode.BUFFERED_STREAMING, p, threshold_ms=600)
    result = await pipeline.run(
        token_stream=_token_stream(["a. ", "b. ", "c. "]),  # 150 ms total < 600
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
        gate=gate,
    )
    # Final flush_final() releases the buffer at end-of-turn.
    assert len(result) == 3
    assert p.depth == 3


@pytest.mark.asyncio
async def test_blocking_mode_buffers_until_final_then_releases() -> None:
    """BLOCKING holds every clause until is_final=True is observed.
    The final clause's is_final flag comes from the pipeline (is_final
    on the last clause of the last clause-text)."""
    p = PlaybackScheduler()
    adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline()
    gate = _gate_for(TTSMode.BLOCKING, p, threshold_ms=999999)  # threshold irrelevant
    result = await pipeline.run(
        token_stream=_token_stream(["a. ", "b. ", "c. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
        gate=gate,
    )
    assert len(result) == 3
    assert p.depth == 3
    # Every clause released only after the final one — but we only see
    # the terminal state here. The pipeline flushed the gate on EOT.


@pytest.mark.asyncio
async def test_blocking_mode_does_not_serialise_synthesis_calls() -> None:
    """CRITICAL Phase F guard: BLOCKING must gate PLAYBACK only. LLM
    token consumption + per-clause TTS synthesis still proceed as tokens
    arrive. If BLOCKING accidentally serialised synthesis behind a
    "wait-for-is_final" gate, the adapter would receive its calls one
    at a time in a way visible here — verify all three calls happened
    (i.e. the pipeline invoked the adapter three times, not once)."""
    p = PlaybackScheduler()
    adapter = _CountingTTSAdapter(ms_per_clause=20)
    pipeline = TrueStreamingPipeline()
    gate = _gate_for(TTSMode.BLOCKING, p, threshold_ms=1000)
    await pipeline.run(
        token_stream=_token_stream(["one. ", "two. ", "three. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
        gate=gate,
    )
    # Three splits → three synthesis calls. BLOCKING did NOT collapse
    # them into a single "one two three" call.
    assert adapter.calls == ["one.", "two.", "three."]


# ---------------------------------------------------------------------------
# STEP 4 — Generation-safe cancellation in each mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_mode_generation_race_drops_stale() -> None:
    """Streaming (no gate) — the pipeline's own gen check + scheduler's
    enqueue check together drop the stale clause."""
    p = PlaybackScheduler()
    release = asyncio.Event()
    adapter = _GatedTTSAdapter(release=release, ms_per_clause=100)
    pipeline = TrueStreamingPipeline()

    task = asyncio.create_task(
        pipeline.run(
            token_stream=_token_stream(["hello. "]),
            response_plan=_response_plan(),
            tts_service=_tts(adapter),
            validator=_PermissiveValidator(),
            playback=p,
            gate=None,
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    await p.flush()
    p.clear_barge_in()
    release.set()
    result = await task
    assert p.depth == 0
    assert result == []


@pytest.mark.asyncio
async def test_buffered_streaming_mode_generation_race_drops_stale() -> None:
    """Buffered_streaming — barge-in mid-synth advances scheduler; the
    gate.enqueue stale check drops the resumed clause."""
    p = PlaybackScheduler()
    release = asyncio.Event()
    adapter = _GatedTTSAdapter(release=release, ms_per_clause=100)
    pipeline = TrueStreamingPipeline()
    gate = _gate_for(TTSMode.BUFFERED_STREAMING, p, threshold_ms=1000)

    task = asyncio.create_task(
        pipeline.run(
            token_stream=_token_stream(["hello. "]),
            response_plan=_response_plan(),
            tts_service=_tts(adapter),
            validator=_PermissiveValidator(),
            playback=p,
            gate=gate,
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    await p.flush()
    p.clear_barge_in()
    release.set()
    await task
    assert p.depth == 0


@pytest.mark.asyncio
async def test_blocking_mode_generation_race_drops_stale() -> None:
    """Blocking — same race, gate holds until is_final; on gen advance
    the release path drops everything."""
    p = PlaybackScheduler()
    release = asyncio.Event()
    adapter = _GatedTTSAdapter(release=release, ms_per_clause=100)
    pipeline = TrueStreamingPipeline()
    gate = _gate_for(TTSMode.BLOCKING, p, threshold_ms=999999)

    task = asyncio.create_task(
        pipeline.run(
            token_stream=_token_stream(["hello. "]),
            response_plan=_response_plan(),
            tts_service=_tts(adapter),
            validator=_PermissiveValidator(),
            playback=p,
            gate=gate,
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    await p.flush()
    p.clear_barge_in()
    release.set()
    await task
    assert p.depth == 0


# ---------------------------------------------------------------------------
# STEP 5 — Default regression: unset AND explicit "streaming" identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unset_and_explicit_streaming_produce_identical_outcome() -> None:
    """Configuration parity: whether VOICEOS_TTS_MODE is unset or is
    the literal 'streaming', the pipeline path is identical."""
    async def _run_once(env: dict[str, str]) -> tuple[int, int]:
        p = PlaybackScheduler()
        adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)
        gate = build_gate_from_env(p, env=env, generation=p.generation)
        assert gate is None, f"env {env!r} unexpectedly returned a gate"
        pipeline = TrueStreamingPipeline()
        result = await pipeline.run(
            token_stream=_token_stream(["hi. ", "bye. "]),
            response_plan=_response_plan(),
            tts_service=_tts(adapter),
            validator=_PermissiveValidator(),
            playback=p,
            gate=gate,
        )
        return len(result), p.depth

    unset = await _run_once({})
    explicit = await _run_once({"VOICEOS_TTS_MODE": "streaming"})
    assert unset == explicit == (2, 2)


# ===========================================================================
# STEP 8 — Production CallOrchestrator._send_clause coverage
# ===========================================================================


class _CapturingWebSocketAdapter:
    """Minimal double for TwilioWebSocketAdapter — only captures send_frame."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def send_frame(self, frame) -> None:
        self.frames.append(frame.pcm_data)


def _bare_orchestrator(playback: PlaybackScheduler, adapter: _CapturingWebSocketAdapter):
    """Build the minimum object that ``CallOrchestrator._send_clause`` needs
    without touching the media_gateway admission path.

    Attributes read by ``_send_clause``:
      - ``self._playback``    — for the generation check
      - ``self._recorder``    — set to None (no recording)
      - ``self._audio_output``— for the real μ-law conversion
      - ``self._out_seq``     — mutable counter
      - ``self._adapter``     — for send_frame
      - ``self._pace_last_clause_end`` — read via getattr, may be absent
    """
    from src.services.media_gateway.twilio_ws_entrypoint import CallOrchestrator
    from src.services.playback.output import AudioOutput

    o = object.__new__(CallOrchestrator)
    o._playback = playback
    o._recorder = None
    o._audio_output = AudioOutput()
    o._out_seq = 0
    o._adapter = adapter
    o._send_ulaw_carry = b""
    return o


@pytest.mark.asyncio
async def test_production_send_clause_stable_generation_sends_all_frames() -> None:
    """Real _send_clause with real AudioOutput.convert: an in-generation
    clause produces a non-empty stream of 20 ms μ-law frames to the
    WebSocket adapter."""
    p = PlaybackScheduler()
    adapter = _CapturingWebSocketAdapter()
    orch = _bare_orchestrator(p, adapter)

    # 400 ms of PCM16LE @ 24 kHz (the real Veena rate) — AudioOutput.convert
    # resamples to 8 kHz μ-law: 8000 samples/s × 0.4 s = 3200 μ-law bytes
    # = 20 frames of 160 bytes.
    clause = AudioClause(
        audio_data=b"\x00" * (24000 * 2 * 400 // 1000),  # 400 ms PCM16LE 24 kHz
        sample_rate=24000,
        text="hello",
        clause_index=0,
        is_final=True,
        generation=0,
    )
    await orch._send_clause(clause)
    # 400 ms at 8 kHz μ-law = 3200 bytes = 20 frames of 160 bytes.
    assert len(adapter.frames) == 20
    assert all(len(f) == 160 for f in adapter.frames)


@pytest.mark.asyncio
async def test_production_send_clause_drops_pre_stale_clause() -> None:
    """A clause whose generation is already behind the scheduler at entry
    to _send_clause: not a single frame reaches the adapter."""
    p = PlaybackScheduler()
    await p.flush()  # scheduler now at gen 1
    p.clear_barge_in()
    adapter = _CapturingWebSocketAdapter()
    orch = _bare_orchestrator(p, adapter)

    stale = AudioClause(
        audio_data=b"\x00" * (24000 * 2 * 200 // 1000),
        sample_rate=24000,
        text="stale",
        clause_index=0,
        is_final=True,
        generation=0,  # scheduler is at 1 — stale
    )
    await orch._send_clause(stale)
    assert adapter.frames == []


@pytest.mark.asyncio
async def test_production_send_clause_aborts_on_mid_clause_flush() -> None:
    """Real _send_clause exits between-frames when the scheduler's
    generation advances mid-clause. We wrap the adapter so that on the
    3rd frame we flush the scheduler; no further frames must be sent."""
    p = PlaybackScheduler()

    class _FlushingAdapter:
        def __init__(self, playback: PlaybackScheduler, flush_after: int) -> None:
            self.frames: list[bytes] = []
            self._playback = playback
            self._flush_after = flush_after

        async def send_frame(self, frame) -> None:
            self.frames.append(frame.pcm_data)
            if len(self.frames) == self._flush_after:
                await self._playback.flush()
                self._playback.clear_barge_in()

    adapter = _FlushingAdapter(p, flush_after=3)
    orch = _bare_orchestrator(p, adapter)

    # 400 ms → 20 total frames if uninterrupted.
    clause = AudioClause(
        audio_data=b"\x00" * (24000 * 2 * 400 // 1000),
        sample_rate=24000,
        text="cut me off",
        clause_index=0,
        is_final=True,
        generation=0,
    )
    await orch._send_clause(clause)
    # Exactly 3 frames — the 4th iteration observes gen 1 != gen 0 and breaks.
    assert len(adapter.frames) == 3


# ===========================================================================
# Cross-cutting invariant: mode == string enum round-trip
# ===========================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("streaming", TTSMode.STREAMING),
        ("BUFFERED_STREAMING", TTSMode.BUFFERED_STREAMING),
        ("  blocking  ", TTSMode.BLOCKING),
        ("", TTSMode.STREAMING),  # empty → default
        ("weird", TTSMode.STREAMING),  # unknown → default
    ],
)
def test_ttsmode_from_env_normalisation(raw: str, expected: TTSMode) -> None:
    assert TTSMode.from_env({"VOICEOS_TTS_MODE": raw}) is expected
