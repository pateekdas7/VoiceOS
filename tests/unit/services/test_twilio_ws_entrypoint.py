"""Unit tests for the Twilio Media Streams WebSocket entrypoint (Path-A Phase 4).

Tests CallOrchestrator's orchestration logic directly with fakes (event
handling, turn loop, mu-law conversion) — deterministic, no real DSP/VAD
thresholds involved. See test_twilio_ws_entrypoint_integration.py for a
synthetic-audio dry run through the real Starlette app.

Architecture: V1 Ch3-9; Path-A consolidation Phase 4.
"""

from __future__ import annotations

import audioop
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.libs.contracts.events.audio_events import BargeinDetected, VADSpeechEnd, VADSpeechStart
from src.libs.contracts.primitives import TenantId
from src.libs.contracts.streaming import AudioClause
from src.libs.contracts.turn import TurnInput, TurnRole
from src.services.media_gateway.twilio_ws_entrypoint import (
    CallOrchestrator,
    SharedCallDependencies,
    _mulaw_frame_to_pcm16le,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orchestrator() -> CallOrchestrator:
    """A CallOrchestrator wired with fakes, bypassing CallOrchestrator.create()
    (which requires real auth) so orchestration logic can be tested in
    isolation from the auth/admission mechanics (already covered by
    tests/unit/services/test_media_gateway.py)."""
    from src.services.audio_session_manager.service import AudioSessionManagerService
    from src.services.vad_endpointing.vad_engine import EnergyVADModel, VADEngine

    audio_session_manager_service = AudioSessionManagerService()
    deps = SharedCallDependencies(
        account_sid="ACtest",
        auth_token="tok",
        tenant_id="tenant-1",
        media_gateway_service=MagicMock(),
        audio_session_manager_service=audio_session_manager_service,
        audio_preprocessor=MagicMock(),
        stt_service=MagicMock(),
        conversation_engine=MagicMock(),
    )
    adapter = MagicMock()
    adapter.send_frame = AsyncMock()

    orch = CallOrchestrator(
        call_id="call-1",
        tenant_id="tenant-1",
        adapter=adapter,
        deps=deps,
        vad_engine=VADEngine(model=EnergyVADModel()),
    )
    return orch


def _tenant() -> TenantId:
    return TenantId("tenant-1")


# ---------------------------------------------------------------------------
# Mu-law <-> PCM16LE conversion
# ---------------------------------------------------------------------------


def test_mulaw_frame_decoded_to_pcm16le() -> None:
    mulaw_config = AudioConfig(sample_rate=SampleRate.RATE_8K, encoding=Encoding.MULAW, channels=1)
    raw_pcm = audioop.lin2ulaw(bytes([0x10, 0x00] * 80), 2)  # arbitrary PCM16LE, encoded to mu-law
    frame = AudioFrame(pcm_data=raw_pcm, seq=0, rtp_ts=0, recv_ts=0.0, config=mulaw_config)

    decoded = _mulaw_frame_to_pcm16le(frame)

    assert decoded.config.encoding == Encoding.PCM16LE
    assert decoded.config.sample_rate == SampleRate.RATE_8K
    assert decoded.pcm_data == audioop.ulaw2lin(raw_pcm, 2)


# ---------------------------------------------------------------------------
# VAD event handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speech_start_opens_a_turn_queue() -> None:
    orch = _make_orchestrator()
    assert orch._turn_active is False

    await orch._handle_vad_event(VADSpeechStart(tenant_id=_tenant(), call_id="call-1", start_ms=0, energy_db=-10.0))

    assert orch._turn_active is True
    assert orch._current_turn_queue is not None
    assert orch._turn_ready.is_set()


@pytest.mark.asyncio
async def test_speech_start_ignored_if_turn_already_active() -> None:
    orch = _make_orchestrator()
    await orch._handle_vad_event(VADSpeechStart(tenant_id=_tenant(), call_id="call-1", start_ms=0, energy_db=-10.0))
    first_queue = orch._current_turn_queue

    await orch._handle_vad_event(VADSpeechStart(tenant_id=_tenant(), call_id="call-1", start_ms=50, energy_db=-8.0))

    assert orch._current_turn_queue is first_queue  # unchanged — no new turn started mid-turn


@pytest.mark.asyncio
async def test_speech_end_closes_turn_queue_with_sentinel() -> None:
    orch = _make_orchestrator()
    await orch._handle_vad_event(VADSpeechStart(tenant_id=_tenant(), call_id="call-1", start_ms=0, energy_db=-10.0))
    queue = orch._current_turn_queue
    assert queue is not None

    await orch._handle_vad_event(
        VADSpeechEnd(tenant_id=_tenant(), call_id="call-1", start_ms=0, end_ms=800, duration_ms=800)
    )

    assert orch._turn_active is False
    sentinel = queue.get_nowait()
    assert sentinel is None


@pytest.mark.asyncio
async def test_frames_route_into_active_turn_queue() -> None:
    orch = _make_orchestrator()
    await orch._handle_vad_event(VADSpeechStart(tenant_id=_tenant(), call_id="call-1", start_ms=0, energy_db=-10.0))
    queue = orch._current_turn_queue
    assert queue is not None

    frame = AudioFrame(
        pcm_data=b"\x00\x00" * 160,
        seq=0,
        rtp_ts=0,
        recv_ts=0.0,
        config=AudioConfig(sample_rate=SampleRate.RATE_16K, encoding=Encoding.PCM16LE),
    )
    await queue.put(frame)
    got = queue.get_nowait()
    assert got is frame


@pytest.mark.asyncio
async def test_bargein_flushes_playback_and_resets_session_state() -> None:
    orch = _make_orchestrator()
    orch._playback = AsyncMock()
    orch._playback.flush = AsyncMock()
    orch._playback.clear_barge_in = MagicMock()
    orch._session = MagicMock()

    await orch._handle_vad_event(
        BargeinDetected(tenant_id=_tenant(), call_id="call-1", detected_at_ms=100, playback_seq=2)
    )

    orch._playback.flush.assert_awaited_once()
    orch._playback.clear_barge_in.assert_called_once()
    orch._session.on_barge_in.assert_called_once()
    orch._session.on_barge_in_end.assert_called_once()


# ---------------------------------------------------------------------------
# Turn loop: STT -> DialogueManager -> ConversationEngine -> outbound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_turns_processes_one_turn_and_sends_audio_out() -> None:
    orch = _make_orchestrator()

    async def _fake_word_stream(*_a: object, **_kw: object) -> AsyncIterator[object]:
        from src.libs.contracts.streaming import WordHypothesis

        yield WordHypothesis(word="namaste", confidence=0.9, start_ms=0, end_ms=300, is_final=True)

    # STTService.transcribe_stream() is `async def` (it awaits the adapter
    # and returns the resulting async generator), so the real call site
    # awaits it -- AsyncMock(return_value=...) mirrors that: the mock call
    # itself is awaitable and resolves to the async generator, whereas a
    # plain MagicMock(return_value=...) returns it un-awaitable.
    orch._deps.stt_service.transcribe_stream = AsyncMock(return_value=_fake_word_stream())

    # 1920 B PCM16@24kHz = 960 samples = 40 ms → resample to 8 kHz = 320
    # samples → 320 μ-law bytes = exactly two 160-byte Twilio frames.
    # (Any payload that resamples to a non-multiple of 160 μ-law bytes
    # would emit ⌊n/160⌋ frames plus a carry — see Gate 1 framing fix.)
    clause = AudioClause(audio_data=b"\x00\x00" * 960, sample_rate=24000, text="hi", clause_index=0, is_final=True)
    orch._deps.conversation_engine.handle_turn = AsyncMock(return_value=[clause])

    # Trigger exactly one turn, then stop the loop.
    await orch._handle_vad_event(VADSpeechStart(tenant_id=_tenant(), call_id="call-1", start_ms=0, energy_db=-10.0))
    assert orch._current_turn_queue is not None
    orch._current_turn_queue.put_nowait(None)  # immediately end the (empty-audio) turn

    async def _stop_after_one() -> None:
        await orch._run_turns_one_iteration()
        orch._closing = True

    await _stop_after_one()

    orch._deps.conversation_engine.handle_turn.assert_awaited_once()
    assert orch._adapter.send_frame.await_count == 2
    for call in orch._adapter.send_frame.call_args_list:
        f: AudioFrame = call.args[0]
        assert f.config.encoding == Encoding.MULAW
        assert len(f.pcm_data) == 160, f"non-160-byte μ-law payload emitted: {len(f.pcm_data)}"


@pytest.mark.asyncio
async def test_empty_transcript_turn_skips_conversation_engine() -> None:
    orch = _make_orchestrator()

    async def _empty_word_stream(*_a: object, **_kw: object) -> AsyncIterator[object]:
        return
        yield  # pragma: no cover - makes this an async generator

    orch._deps.stt_service.transcribe_stream = AsyncMock(return_value=_empty_word_stream())
    orch._deps.conversation_engine.handle_turn = AsyncMock()

    await orch._handle_vad_event(VADSpeechStart(tenant_id=_tenant(), call_id="call-1", start_ms=0, energy_db=-10.0))
    assert orch._current_turn_queue is not None
    orch._current_turn_queue.put_nowait(None)

    await orch._run_turns_one_iteration()

    orch._deps.conversation_engine.handle_turn.assert_not_awaited()
    orch._adapter.send_frame.assert_not_awaited()


# ---------------------------------------------------------------------------
# Call-start greeting (Path-A Phase 6g)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_speak_greeting_is_noop_when_no_dialogue_response_wired() -> None:
    """ConversationEngine.build_greeting() returns None when it has no
    dialogue_response wired (pre-Phase-6g behavior) — the greeting call
    must be a clean no-op, not an error."""
    orch = _make_orchestrator()
    orch._deps.conversation_engine.build_greeting = MagicMock(return_value=None)
    orch._deps.conversation_engine.speak_scripted_text = AsyncMock()

    await orch._speak_greeting()

    orch._deps.conversation_engine.speak_scripted_text.assert_not_awaited()
    orch._adapter.send_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_clauses_drains_playback_queue_across_many_turns() -> None:
    """Regression test for a real bug found by Path-A Phase 7's live dry run:
    ConversationEngine's TrueStreamingPipeline enqueues every synthesised
    clause into PlaybackScheduler (for barge-in tracking), but nothing ever
    dequeued them — so a real call accumulated queue depth monotonically
    across turns and eventually hit RI-3's bounded-queue guard (max_depth
    512, ~43s of cumulative audio) and crashed. Simulates what the real
    pipeline does (enqueue, then hand the same clauses to _send_clauses) for
    enough turns that the old code would have violated RI-3; asserts the
    queue returns to empty after each turn instead."""
    orch = _make_orchestrator()

    for turn in range(20):
        clauses = [AudioClause(audio_data=b"\x00\x00" * 160, sample_rate=24000, text=f"turn {turn}", clause_index=i, is_final=(i == 29)) for i in range(30)]
        for clause in clauses:
            await orch._playback.enqueue(clause)  # what TrueStreamingPipeline._synthesise_and_enqueue() does

        await orch._send_clauses(clauses)

        assert orch._playback.depth == 0, f"queue not drained after turn {turn} — would eventually hit RI-3"


@pytest.mark.asyncio
async def test_speak_greeting_synthesizes_and_sends_when_wired() -> None:
    """_speak_greeting() now streams: it drains self._playback concurrently
    with synthesis instead of awaiting a returned clause list (see its
    docstring — buffering the whole ~19s greeting before any audio went out
    was a real bug found via a live Call-002 trial). The fake
    speak_scripted_text must therefore enqueue into self._playback as its
    real TrueStreamingPipeline counterpart does, not just return a list."""
    orch = _make_orchestrator()
    # 1920 B PCM16@24kHz = 40 ms → 320 μ-law bytes = exactly two 160-byte
    # Twilio frames. Anything smaller resamples below 160 μ-law bytes and
    # (correctly, per the Gate 1 framing fix) emits zero frames — carried
    # forward instead — which would defeat the purpose of this test.
    clause = AudioClause(audio_data=b"\x00\x00" * 960, sample_rate=24000, text="namaste", clause_index=0, is_final=True)
    orch._deps.conversation_engine.build_greeting = MagicMock(return_value="Namaste sir, main Kavya bol rahi hoon.")

    async def _fake_speak_scripted_text(text: str, playback: object, tts_mode=None) -> list[AudioClause]:
        await playback.enqueue(clause)  # type: ignore[attr-defined]
        return [clause]

    orch._deps.conversation_engine.speak_scripted_text = AsyncMock(side_effect=_fake_speak_scripted_text)

    await orch._speak_greeting()

    orch._deps.conversation_engine.speak_scripted_text.assert_awaited_once()
    assert orch._deps.conversation_engine.speak_scripted_text.call_args.args[0] == "Namaste sir, main Kavya bol rahi hoon."
    assert orch._adapter.send_frame.await_count == 2
    for call in orch._adapter.send_frame.call_args_list:
        f: AudioFrame = call.args[0]
        assert len(f.pcm_data) == 160, f"non-160-byte μ-law payload emitted: {len(f.pcm_data)}"


@pytest.mark.asyncio
async def test_run_does_not_hang_forever_when_greeting_tts_call_hangs() -> None:
    """Regression test for a real bug found by a live Call-002 trial: when
    the GPU node was unreachable, _speak_greeting()'s TTS call hung
    indefinitely, and since it previously ran *before* run()'s try/finally
    with no timeout, the whole call hung forever, the turn-processing
    pipeline never started (the customer got no response to anything they
    said), and cleanup (media_gateway_service.release_adapter()) never ran.

    Simulates the hang with an AsyncMock that sleeps far longer than
    greeting_timeout_s, and asserts run() moves on well before that full
    duration and still performs cleanup once cancelled from outside (the
    turn-processing loop itself blocks forever on real customer audio in
    this fake setup, same as it always does until the adapter's inbound
    stream ends -- this test only needs to prove the greeting no longer
    blocks that loop from ever starting)."""
    import asyncio
    import time

    orch = _make_orchestrator()
    orch._deps.greeting_timeout_s = 0.05
    orch._deps.conversation_engine.build_greeting = MagicMock(return_value="Namaste sir...")

    async def _hang_forever(*_a: object, **_kw: object) -> list[AudioClause]:
        await asyncio.sleep(10.0)
        return []

    orch._deps.conversation_engine.speak_scripted_text = AsyncMock(side_effect=_hang_forever)
    orch._deps.media_gateway_service.release_adapter = AsyncMock()

    async def _empty_frames() -> AsyncIterator[AudioFrame]:
        return
        yield  # pragma: no cover — makes this an async generator

    orch._adapter.receive_frame = MagicMock(return_value=_empty_frames())
    orch._adapter.drain_outbound = MagicMock(return_value=None)
    fake_websocket = AsyncMock()

    start = time.monotonic()
    # _pump_inbound() finishes immediately (the fake adapter's frame stream
    # is empty) and sets self._closing -- so _run_turns()/_pump_outbound()
    # exit their loops and run() returns normally. The outer timeout here
    # only guards against the actual regression (the greeting hang blocking
    # everything for the full simulated 10s).
    await asyncio.wait_for(orch.run(fake_websocket), timeout=2.0)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"greeting hang blocked run() for {elapsed:.2f}s — timeout was not honored"
    orch._deps.conversation_engine.speak_scripted_text.assert_awaited_once()
    orch._deps.media_gateway_service.release_adapter.assert_awaited_once()


# ---------------------------------------------------------------------------
# Repeat-intent watchdog (Sprint-add: greeting-time interrupt on "kya bola")
# ---------------------------------------------------------------------------


def _make_watchdog_frame() -> AudioFrame:
    """A single non-empty preprocessed frame, plausibly what
    AudioPreprocessor.process_frame() would emit (PCM16LE @ 16 kHz, 20 ms)."""
    cfg = AudioConfig(sample_rate=SampleRate.RATE_16K, encoding=Encoding.PCM16LE, channels=1)
    # 20 ms @ 16 kHz mono = 320 samples * 2 bytes = 640 bytes
    return AudioFrame(pcm_data=b"\x00\x01" * 320, seq=0, rtp_ts=0, recv_ts=0.0, config=cfg)


@pytest.mark.asyncio
async def test_repeat_intent_watchdog_releases_protection_on_kya_bola() -> None:
    """Watchdog polls STT ~500ms and, on detecting a repeat-intent keyword,
    calls PlaybackScheduler.clear_protection() and returns."""
    import asyncio

    from src.libs.contracts.streaming import WordHypothesis

    orch = _make_orchestrator()

    # Stub PlaybackScheduler.clear_protection so we can assert on it.
    orch._playback = MagicMock()
    orch._playback.clear_protection = MagicMock()

    # STT mock returns a "kya bola" hypothesis.
    async def _repeat_word_stream(*_a: object, **_kw: object) -> AsyncIterator[WordHypothesis]:
        yield WordHypothesis(word="kya", confidence=0.9, start_ms=0, end_ms=200, is_final=False)
        yield WordHypothesis(word="bola", confidence=0.9, start_ms=200, end_ms=400, is_final=True)

    # Fresh generator per call (transcribe_stream is invoked once per tick).
    orch._deps.stt_service.transcribe_stream = AsyncMock(side_effect=lambda *_a, **_kw: _repeat_word_stream())

    # The watchdog clears the ring buffer on entry (defensive against
    # stale frames from a prior call), so prime it *after* the watchdog
    # has started — the very first 500 ms sleep is our chance to inject.
    async def _prime_after_start() -> None:
        await asyncio.sleep(0.1)
        orch._greeting_audio_buffer.append(_make_watchdog_frame())

    primer = asyncio.create_task(_prime_after_start())
    try:
        # Run the watchdog with a bounded timeout — it should return on
        # first match well within this window.
        await asyncio.wait_for(orch._repeat_intent_watchdog(), timeout=3.0)
    finally:
        primer.cancel()
        try:
            await primer
        except (asyncio.CancelledError, Exception):
            pass

    orch._playback.clear_protection.assert_called_once()


@pytest.mark.asyncio
async def test_repeat_intent_watchdog_ignores_non_matching_transcript() -> None:
    """Non-matching STT output must NOT trigger clear_protection() — the
    greeting continues undisturbed."""
    import asyncio

    from src.libs.contracts.streaming import WordHypothesis

    orch = _make_orchestrator()

    orch._playback = MagicMock()
    orch._playback.clear_protection = MagicMock()

    async def _neutral_word_stream(*_a: object, **_kw: object) -> AsyncIterator[WordHypothesis]:
        yield WordHypothesis(word="haan", confidence=0.9, start_ms=0, end_ms=200, is_final=False)
        yield WordHypothesis(word="bilkul", confidence=0.9, start_ms=200, end_ms=400, is_final=True)

    orch._deps.stt_service.transcribe_stream = AsyncMock(side_effect=lambda *_a, **_kw: _neutral_word_stream())

    async def _prime_after_start() -> None:
        await asyncio.sleep(0.1)
        orch._greeting_audio_buffer.append(_make_watchdog_frame())

    primer = asyncio.create_task(_prime_after_start())

    # Watchdog will never match — cancel it after a couple of poll cycles.
    task = asyncio.create_task(orch._repeat_intent_watchdog())
    await asyncio.sleep(1.2)  # ~2 poll ticks
    task.cancel()
    primer.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    orch._playback.clear_protection.assert_not_called()
