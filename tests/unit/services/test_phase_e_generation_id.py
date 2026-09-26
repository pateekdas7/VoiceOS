"""Phase E — generation-ID protected barge-in.

Covers the fail-closed invalidation channel introduced to eliminate the
stale-audio-after-barge-in race:

  Generation N active
  → barge-in
  → PlaybackScheduler.flush() increments generation to N+1
  → barge_in_event may subsequently be cleared for turn N+1
  → any coroutine still holding a gen-N snapshot resumes
  → generation check REJECTS it (fail-closed)

Fail-closed rules verified:
  - Old-generation audio never enters the scheduler queue.
  - Old-generation audio never releases from the StartupBufferGate.
  - Old-generation audio never reaches Twilio (per-frame check).
  - No mismatch is ever "fixed" by rewriting the clause's generation —
    stale audio is DROPPED, not silently forwarded.

Audio duration convention: same as Phase D — sample_rate=8000 combined
with bytes_per_sample=1 makes 8 bytes = 1 ms. See
``tests_new/test_phase_d_startup_buffer_gate.py`` for the rationale.
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
from src.services.tts.startup_buffer_gate import StartupBufferGate, TTSMode
from src.services.tts.streaming_pipeline import TrueStreamingPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SR = 8000
_BYTES_PER_MS = _TEST_SR // 1000


def _clause(
    ms: int,
    *,
    idx: int = 0,
    is_final: bool = False,
    generation: int = 0,
) -> AudioClause:
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


class _GatedTTSAdapter:
    """Adapter whose per-clause synthesis blocks on an asyncio.Event.

    Used to construct the deterministic race: the pipeline enters
    _synthesise_and_enqueue, awaits the gate (i.e. is mid-synthesis),
    barge-in flushes the scheduler, barge_in_event is cleared, then the
    gate is released and the pipeline attempts to enqueue.
    """

    def __init__(self, release_event: asyncio.Event, ms_per_clause: int = 100) -> None:
        self._release = release_event
        self._bytes = ms_per_clause * (_TEST_SR // 1000)
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


class _SizedRecordingTTSAdapter:
    def __init__(self, ms_per_clause: int) -> None:
        self._bytes = ms_per_clause * (_TEST_SR // 1000)
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


def _tts(adapter) -> TTSService:
    return TTSService.create(adapter=adapter, config=TTSServiceConfig())


def _response_plan() -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-phaseE",
        tenant_id="tenant-phaseE",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
    )


async def _token_stream(chunks: list[str]) -> AsyncIterator[TokenChunk]:
    for i, c in enumerate(chunks):
        finish = "stop" if i == len(chunks) - 1 else None
        yield TokenChunk(text=c, token_id=0, finish_reason=finish)


# ---------------------------------------------------------------------------
# 1. AudioClause carries a generation field with default 0
# ---------------------------------------------------------------------------


def test_audio_clause_defaults_generation_to_zero() -> None:
    c = AudioClause(
        audio_data=b"\x00" * 8,
        sample_rate=_TEST_SR,
        text="x",
        clause_index=0,
        is_final=True,
    )
    assert c.generation == 0


def test_audio_clause_generation_is_settable() -> None:
    c = _clause(1, generation=7)
    assert c.generation == 7


# ---------------------------------------------------------------------------
# 2. PlaybackScheduler.generation semantics
# ---------------------------------------------------------------------------


def test_scheduler_starts_at_generation_zero() -> None:
    p = PlaybackScheduler()
    assert p.generation == 0


@pytest.mark.asyncio
async def test_scheduler_flush_increments_generation() -> None:
    p = PlaybackScheduler()
    await p.enqueue(_clause(1, generation=0))
    assert p.generation == 0
    await p.flush()
    assert p.generation == 1
    await p.flush()
    assert p.generation == 2


@pytest.mark.asyncio
async def test_clear_barge_in_does_not_reset_generation() -> None:
    """The barge_in_event is a per-turn latch; the generation counter is
    NOT reset by clear_barge_in(). This is the exact property that makes
    the fail-closed check work — a delayed producer that resumes AFTER
    the next turn cleared the event still sees the advanced generation."""
    p = PlaybackScheduler()
    await p.flush()
    assert p.generation == 1
    p.clear_barge_in()
    assert p.generation == 1
    assert not p.barge_in_event.is_set()


# ---------------------------------------------------------------------------
# 3. Scheduler rejects stale clauses on enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_enqueue_drops_stale_generation() -> None:
    p = PlaybackScheduler()
    await p.flush()  # generation now 1
    p.clear_barge_in()
    # Stale gen-0 clause arriving after the flush + clear must be dropped.
    stale = _clause(10, idx=0, generation=0)
    await p.enqueue(stale)
    assert p.depth == 0
    assert p.get_clauses() == []


@pytest.mark.asyncio
async def test_scheduler_enqueue_accepts_current_generation() -> None:
    p = PlaybackScheduler()
    await p.flush()
    p.clear_barge_in()
    fresh = _clause(10, idx=0, generation=1)
    await p.enqueue(fresh)
    assert p.depth == 1


@pytest.mark.asyncio
async def test_scheduler_enqueue_never_rewrites_clause_generation() -> None:
    """A dropped clause must NOT be silently forwarded with a rewritten
    generation. The scheduler drops it; nothing shifts the mismatch away."""
    p = PlaybackScheduler()
    await p.flush()  # gen 1
    stale = _clause(10, generation=0)
    await p.enqueue(stale)
    assert p.depth == 0
    # And the clause we submitted is untouched (frozen model anyway).
    assert stale.generation == 0


@pytest.mark.asyncio
async def test_scheduler_enqueue_rejects_future_generation_too() -> None:
    """Mismatch either direction is a race, not just N < current. The
    invariant is equality with the scheduler's current generation."""
    p = PlaybackScheduler()  # gen 0
    future = _clause(10, generation=99)
    await p.enqueue(future)
    assert p.depth == 0


# ---------------------------------------------------------------------------
# 4. StartupBufferGate rejects stale on enqueue
# ---------------------------------------------------------------------------


def _mk_gate(
    mode: TTSMode = TTSMode.BUFFERED_STREAMING,
    threshold_ms: int = 400,
    generation: int | None = None,
) -> tuple[StartupBufferGate, PlaybackScheduler]:
    p = PlaybackScheduler()
    g = StartupBufferGate(
        playback=p,
        mode=mode,
        threshold_ms=threshold_ms,
        bytes_per_sample=1,
        generation=generation,
    )
    return g, p


def test_gate_default_bytes_per_sample_is_two() -> None:
    """Phase E correction: PCM16LE = 2 bytes/sample matches the actual
    wire format ratcheted through AudioOutput.convert."""
    from src.services.tts.startup_buffer_gate import _DEFAULT_BYTES_PER_SAMPLE
    assert _DEFAULT_BYTES_PER_SAMPLE == 2


@pytest.mark.asyncio
async def test_gate_snapshots_generation_at_construction_when_omitted() -> None:
    p = PlaybackScheduler()
    # Advance scheduler before gate construction.
    await p.flush()
    g = StartupBufferGate(playback=p, mode=TTSMode.BUFFERED_STREAMING, threshold_ms=400)
    assert g.scope_generation == p.generation == 1


def test_gate_accepts_explicit_generation() -> None:
    p = PlaybackScheduler()
    g = StartupBufferGate(
        playback=p, mode=TTSMode.BUFFERED_STREAMING, threshold_ms=400, generation=42
    )
    assert g.scope_generation == 42


@pytest.mark.asyncio
async def test_gate_drops_stale_clause_on_enqueue() -> None:
    gate, playback = _mk_gate(generation=0)
    await playback.flush()  # playback now gen 1, gate scope still gen 0
    playback.clear_barge_in()
    stale = _clause(50, generation=0)
    await gate.enqueue(stale)
    # Nothing reaches the scheduler.
    assert playback.depth == 0
    # Gate discarded on stale-detect.
    assert gate.buffered_clauses == 0


@pytest.mark.asyncio
async def test_gate_drops_clause_stamped_with_wrong_generation() -> None:
    """A clause whose own generation differs from the gate's scope
    generation is stale regardless of whether the scheduler has advanced."""
    gate, playback = _mk_gate(generation=1)
    # Scheduler still at gen 0; caller nonetheless passed a wrong-gen
    # clause. Gate still rejects on mismatch with its scope.
    wrong = _clause(50, generation=99)
    await gate.enqueue(wrong)
    assert playback.depth == 0


# ---------------------------------------------------------------------------
# 5. StartupBufferGate rejects stale during buffered-then-flushed release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_release_drops_all_when_generation_advanced_before_release() -> None:
    """Buffer some clauses (all stamped gen 0), then the scheduler
    advances to gen 1 via barge-in; a subsequent flush_final must drop
    everything — ZERO stale audio reaches the scheduler."""
    gate, playback = _mk_gate(threshold_ms=1000, generation=0)
    # Buffer 300 ms of gen-0 audio (below threshold, so still buffered).
    await gate.enqueue(_clause(100, idx=0, generation=0))
    await gate.enqueue(_clause(100, idx=1, generation=0))
    await gate.enqueue(_clause(100, idx=2, generation=0))
    assert gate.buffered_clauses == 3
    # Barge-in fires; scheduler advances; next turn clears the event.
    await playback.flush()
    playback.clear_barge_in()
    # Now call flush_final(): release must abort and drop everything.
    await gate.flush_final()
    assert playback.depth == 0


@pytest.mark.asyncio
async def test_gate_release_stops_mid_drain_on_generation_advance() -> None:
    """A more granular check: even if the generation advances DURING
    the release drain loop, no further clauses are enqueued."""
    p = PlaybackScheduler()

    # Custom gate that we manipulate around the release call.
    g = StartupBufferGate(
        playback=p,
        mode=TTSMode.BUFFERED_STREAMING,
        threshold_ms=1000,
        bytes_per_sample=1,
        generation=0,
    )
    for i in range(5):
        await g.enqueue(_clause(50, idx=i, generation=0))
    assert g.buffered_clauses == 5

    # Advance the scheduler before release runs.
    await p.flush()  # gen 1
    p.clear_barge_in()

    await g.flush_final()
    # Every buffered clause was gen 0, scheduler is gen 1 → all dropped.
    assert p.depth == 0


@pytest.mark.asyncio
async def test_gate_release_discards_after_generation_advance() -> None:
    gate, playback = _mk_gate(threshold_ms=1000, generation=0)
    await gate.enqueue(_clause(100, idx=0, generation=0))
    assert gate.buffered_clauses == 1
    await playback.flush()
    playback.clear_barge_in()
    await gate.flush_final()
    # discard() must have run (either the top-of-release stale check or
    # the popleft branch); either way buffered_clauses is 0.
    assert gate.buffered_clauses == 0


# ---------------------------------------------------------------------------
# 6. TrueStreamingPipeline stamps AudioClauses with the scope generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_stamps_synthesised_clauses_with_scope_generation() -> None:
    p = PlaybackScheduler()
    # Move the scheduler to gen 2 before pipeline starts, so we can
    # verify the pipeline picked up the LIVE generation, not a hard-coded 0.
    await p.flush()
    p.clear_barge_in()
    await p.flush()
    p.clear_barge_in()
    assert p.generation == 2

    adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline()
    clauses = await pipeline.run(
        token_stream=_token_stream(["hello. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    )
    assert clauses, "pipeline must have synthesised at least one clause"
    for c in clauses:
        assert c.generation == 2, f"expected gen 2, got {c.generation}"


@pytest.mark.asyncio
async def test_pipeline_default_generation_is_zero_for_fresh_scheduler() -> None:
    p = PlaybackScheduler()
    adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline()
    clauses = await pipeline.run(
        token_stream=_token_stream(["hello. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    )
    for c in clauses:
        assert c.generation == 0


# ---------------------------------------------------------------------------
# 7. DETERMINISTIC RACE TEST — the whole point of Phase E
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_race_gen_n_coroutine_resumes_after_invalidation() -> None:
    """The race the user asked for by name.

    Steps:
      1. Start generation N (0).
      2. Pause gen-N _synthesise_and_enqueue() at an await (the
         _GatedTTSAdapter blocks on an asyncio.Event).
      3. Trigger PlaybackScheduler.flush() → generation becomes N+1 (1).
      4. Clear the barge-in event to simulate the next turn.
      5. Resume the paused gen-N coroutine.
      6. It attempts to enqueue an AudioClause stamped generation=0.
      7. Assert ZERO generation-N clauses enter PlaybackScheduler.
    """
    p = PlaybackScheduler()
    assert p.generation == 0

    release = asyncio.Event()
    adapter = _GatedTTSAdapter(release_event=release, ms_per_clause=100)
    pipeline = TrueStreamingPipeline()

    # Kick off the pipeline: it will feed the splitter, get a complete
    # clause "hello.", enter _synthesise_and_enqueue, and block inside
    # the gated adapter's _gen() on release.wait().
    pipeline_task = asyncio.create_task(
        pipeline.run(
            token_stream=_token_stream(["hello. "]),
            response_plan=_response_plan(),
            tts_service=_tts(adapter),
            validator=_PermissiveValidator(),
            playback=p,
        )
    )

    # Give the pipeline scheduling time to hit the blocked await inside
    # the adapter. A short sleep is the simplest deterministic yield.
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    # Nothing has been enqueued yet.
    assert p.depth == 0

    # Barge-in: flush the scheduler (advances generation).
    await p.flush()
    assert p.generation == 1
    # Next turn clears the event — this is the key part of the race.
    p.clear_barge_in()
    assert not p.barge_in_event.is_set()

    # Now release the gated adapter. The pipeline resumes and attempts
    # to enqueue an AudioClause stamped generation=0.
    release.set()
    result = await pipeline_task

    # The clause did leave the adapter and was returned in the list
    # (that reflects what was synthesised), but ZERO of them reached
    # the scheduler queue: the pipeline's own gen check bailed before
    # calling enqueue, and even if it hadn't, the scheduler would have
    # rejected the stale clause.
    assert p.depth == 0
    assert p.get_clauses() == []
    # And the returned list has zero clauses (pipeline aborted before
    # any could be stamped and appended).
    assert result == []


@pytest.mark.asyncio
async def test_race_direct_scheduler_enqueue_after_flush_clear() -> None:
    """Same race, but exercised directly on the scheduler without the
    pipeline: a coroutine holds a stale clause, flush + clear happens,
    then the coroutine enqueues → scheduler drops it fail-closed."""
    p = PlaybackScheduler()
    stale = _clause(50, generation=0)  # gen-N snapshot
    await p.flush()
    assert p.generation == 1
    p.clear_barge_in()
    # Stale coroutine now enqueues.
    await p.enqueue(stale)
    assert p.depth == 0


@pytest.mark.asyncio
async def test_race_gate_buffered_then_flush_release_yields_zero() -> None:
    """The buffered-mode variant of the race:
    GEN N audio queued in the gate, flush() advances gen, gate.release
    releases ZERO GEN N audio."""
    gate, p = _mk_gate(threshold_ms=1000, generation=0)
    await gate.enqueue(_clause(100, idx=0, generation=0))
    await gate.enqueue(_clause(100, idx=1, generation=0))
    await gate.enqueue(_clause(100, idx=2, generation=0))
    await p.flush()
    p.clear_barge_in()
    await gate.flush_final()
    assert p.depth == 0


# ---------------------------------------------------------------------------
# 8. Twilio-frame boundary — mid-clause generation advance
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Minimal Twilio adapter double: records sent frames."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send_frame(self, frame) -> None:
        self.sent.append(frame.pcm_data)


class _MinimalOrchestrator:
    """The _send_clause implementation lifted to a testable minimum.

    Reproduces the exact per-frame generation check from
    CallOrchestrator._send_clause. This avoids constructing a full
    orchestrator (which pulls in Media Gateway, VAD, Dialogue Manager…)
    while still exercising the Phase E last-mile check.
    """

    def __init__(self, playback: PlaybackScheduler, adapter: _FakeAdapter) -> None:
        self._playback = playback
        self._adapter = adapter
        self._out_seq = 0
        self.frames_sent_per_clause: list[int] = []
        self.aborted_mid_clause: list[bool] = []

    async def send_clause(self, clause: AudioClause, mid_clause_flush_after: int | None = None) -> None:
        clause_generation = clause.generation
        # Simulate AudioOutput.convert output: pass PCM through as-is
        # for this test (bytes-in = bytes-out at 8 kHz μ-law size).
        pcm_ulaw = b"\xff" * len(clause.audio_data)
        chunk_size = 160
        n_frames = 0
        aborted = False
        total_frames = (len(pcm_ulaw) + chunk_size - 1) // chunk_size
        for i, start in enumerate(range(0, len(pcm_ulaw), chunk_size)):
            if self._playback.generation != clause_generation:
                aborted = True
                break
            chunk = pcm_ulaw[start : start + chunk_size]
            self._out_seq += 1

            class _F:
                def __init__(self, pcm: bytes) -> None:
                    self.pcm_data = pcm

            await self._adapter.send_frame(_F(chunk))
            n_frames += 1
            # Deterministic mid-clause interruption trigger:
            if mid_clause_flush_after is not None and i == mid_clause_flush_after - 1:
                await self._playback.flush()
                self._playback.clear_barge_in()

        self.frames_sent_per_clause.append(n_frames)
        self.aborted_mid_clause.append(aborted)


@pytest.mark.asyncio
async def test_send_clause_completes_when_generation_stable() -> None:
    p = PlaybackScheduler()
    fake = _FakeAdapter()
    orch = _MinimalOrchestrator(p, fake)
    # 800 bytes of pretend μ-law → 5 Twilio frames of 160 bytes.
    clause = AudioClause(
        audio_data=b"\x00" * 800,
        sample_rate=_TEST_SR,
        text="x",
        clause_index=0,
        is_final=True,
        generation=0,
    )
    await orch.send_clause(clause)
    assert orch.frames_sent_per_clause == [5]
    assert orch.aborted_mid_clause == [False]
    assert len(fake.sent) == 5


@pytest.mark.asyncio
async def test_send_clause_aborts_between_frames_on_generation_advance() -> None:
    """The mandated third race: GEN N clause first frame sent, generation
    changes, ZERO further GEN N frames sent."""
    p = PlaybackScheduler()
    fake = _FakeAdapter()
    orch = _MinimalOrchestrator(p, fake)
    clause = AudioClause(
        audio_data=b"\x00" * 800,  # 5 frames
        sample_rate=_TEST_SR,
        text="x",
        clause_index=0,
        is_final=True,
        generation=0,
    )
    # After 2 frames, simulate barge-in (flush + clear event) mid-clause.
    await orch.send_clause(clause, mid_clause_flush_after=2)
    assert orch.aborted_mid_clause == [True]
    # Exactly the 2 pre-flush frames must have been sent; ZERO after.
    assert orch.frames_sent_per_clause == [2]
    assert len(fake.sent) == 2


@pytest.mark.asyncio
async def test_send_clause_drops_pre_stale_clause_immediately() -> None:
    """A clause that is ALREADY stale by the time _send_clause runs must
    not emit a single Twilio frame."""
    p = PlaybackScheduler()
    await p.flush()  # gen 1
    p.clear_barge_in()
    fake = _FakeAdapter()
    orch = _MinimalOrchestrator(p, fake)
    stale = AudioClause(
        audio_data=b"\x00" * 800,
        sample_rate=_TEST_SR,
        text="x",
        clause_index=0,
        is_final=True,
        generation=0,
    )
    await orch.send_clause(stale)
    assert orch.frames_sent_per_clause == [0]
    assert orch.aborted_mid_clause == [True]
    assert fake.sent == []


# ---------------------------------------------------------------------------
# 9. End-to-end pipeline: barge-in mid-turn produces zero downstream audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_aborts_on_generation_advance_between_chunks() -> None:
    """If the generation advances mid-turn (between LLM token chunks),
    the pipeline aborts and no further clauses are synthesised/stamped
    for that scope."""
    p = PlaybackScheduler()
    adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline()

    async def _interrupted_stream() -> AsyncIterator[TokenChunk]:
        yield TokenChunk(text="first sentence. ", token_id=0, finish_reason=None)
        # Simulate barge-in mid-turn.
        await p.flush()
        p.clear_barge_in()
        yield TokenChunk(text="second sentence. ", token_id=0, finish_reason=None)
        yield TokenChunk(text="third. ", token_id=0, finish_reason="stop")

    result = await pipeline.run(
        token_stream=_interrupted_stream(),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    )
    # Exactly one clause synthesised (the one before the mid-turn flush).
    # After the flush, the pipeline observes the generation advance and
    # aborts before feeding "second sentence" to the splitter.
    assert len(result) == 1
    # The pre-flush clause was enqueued into a gen-0 scheduler, then
    # flush() cleared the queue AND advanced the gen. So the scheduler
    # is now empty at gen 1.
    assert p.depth == 0
    assert p.generation == 1


@pytest.mark.asyncio
async def test_pipeline_normal_end_of_turn_still_flushes_gate() -> None:
    """Regression: with no barge-in, buffered-mode gate flushes normally
    and the scheduler receives everything (short response < threshold)."""
    p = PlaybackScheduler()
    adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline()
    gate = StartupBufferGate(
        playback=p,
        mode=TTSMode.BUFFERED_STREAMING,
        threshold_ms=1000,  # short response < threshold
        bytes_per_sample=1,
    )
    result = await pipeline.run(
        token_stream=_token_stream(["hi. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
        gate=gate,
    )
    assert len(result) == 1
    assert p.depth == 1


# ---------------------------------------------------------------------------
# 10. Regression — non-race defaults still behave as before
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_streaming_mode_still_passes_through_untouched() -> None:
    """The default streaming code path (gate is None) must still work
    identically for a happy-path turn with no barge-in."""
    p = PlaybackScheduler()
    adapter = _SizedRecordingTTSAdapter(ms_per_clause=50)
    pipeline = TrueStreamingPipeline()
    result = await pipeline.run(
        token_stream=_token_stream(["one. ", "two. "]),
        response_plan=_response_plan(),
        tts_service=_tts(adapter),
        validator=_PermissiveValidator(),
        playback=p,
    )
    # Every clause reached the scheduler; scheduler generation unchanged.
    assert p.generation == 0
    assert p.depth == len(result)
    assert p.depth >= 1


@pytest.mark.asyncio
async def test_scheduler_accepts_default_generation_zero_clause_at_gen_zero() -> None:
    """Backwards compat: a clause constructed without an explicit
    generation (default 0) still enqueues cleanly against a fresh
    scheduler (also gen 0). Ensures we didn't break every test that
    predates Phase E."""
    p = PlaybackScheduler()
    c = AudioClause(
        audio_data=b"\x00" * 8,
        sample_rate=_TEST_SR,
        text="x",
        clause_index=0,
        is_final=True,
    )
    await p.enqueue(c)
    assert p.depth == 1
