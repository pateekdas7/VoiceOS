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

    orch._deps.stt_service.transcribe_stream = MagicMock(return_value=_fake_word_stream())

    clause = AudioClause(audio_data=b"\x00" * 4096, sample_rate=24000, text="hi", clause_index=0, is_final=True)
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
    orch._adapter.send_frame.assert_awaited_once()
    sent_frame: AudioFrame = orch._adapter.send_frame.call_args.args[0]
    assert sent_frame.config.encoding == Encoding.MULAW


@pytest.mark.asyncio
async def test_empty_transcript_turn_skips_conversation_engine() -> None:
    orch = _make_orchestrator()

    async def _empty_word_stream(*_a: object, **_kw: object) -> AsyncIterator[object]:
        return
        yield  # pragma: no cover - makes this an async generator

    orch._deps.stt_service.transcribe_stream = MagicMock(return_value=_empty_word_stream())
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
async def test_speak_greeting_synthesizes_and_sends_when_wired() -> None:
    orch = _make_orchestrator()
    clause = AudioClause(audio_data=b"\x00\x00" * 160, sample_rate=24000, text="namaste", clause_index=0, is_final=True)
    orch._deps.conversation_engine.build_greeting = MagicMock(return_value="Namaste sir, main Kavya bol rahi hoon.")
    orch._deps.conversation_engine.speak_scripted_text = AsyncMock(return_value=[clause])

    await orch._speak_greeting()

    orch._deps.conversation_engine.speak_scripted_text.assert_awaited_once()
    assert orch._deps.conversation_engine.speak_scripted_text.call_args.args[0] == "Namaste sir, main Kavya bol rahi hoon."
    orch._adapter.send_frame.assert_awaited_once()
