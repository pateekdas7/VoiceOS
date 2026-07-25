"""Integration test for the Twilio Media Streams WS entrypoint (Path-A Phase 4).

Drives the REAL Starlette app (create_twilio_media_stream_app) with
synthetic Twilio Media Streams JSON messages over Starlette's TestClient
WebSocket support — real auth (valid computed HMAC signature), real
MediaGatewayService/AudioSessionManagerService/AudioPreprocessorService
(resample-only, so synthetic tone amplitude survives predictably for VAD
thresholding) /VADEndpointingService(EnergyVADModel), fake STT and
ConversationEngine (GPU/Postgres are Phase 3/2's own concerns, already
validated for real elsewhere — this test's job is proving the WS<->audio
pipeline wiring itself, end to end).

Architecture: V1 Ch3-9; Path-A consolidation Phase 4.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from collections.abc import AsyncIterator
from math import pi, sin
from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient

from src.libs.contracts.streaming import AudioClause, WordHypothesis
from src.services.audio_preprocessing.service import AudioPreprocessorService
from src.services.audio_session_manager.service import AudioSessionManagerService
from src.services.media_gateway.service import MediaGatewayService
from src.services.media_gateway.twilio_ws_entrypoint import SharedCallDependencies, create_twilio_media_stream_app

_ACCOUNT_SID = "ACtestaccount"
_AUTH_TOKEN = "test-auth-token"


def _twilio_signature(url: str) -> str:
    """Matches src/services/media_gateway/auth.py::validate_twilio_signature
    exactly (empty params dict, since a WS upgrade carries no POST body)."""
    mac = hmac.new(_AUTH_TOKEN.encode(), url.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _mulaw_media_message(chunk: int, pcm16: bytes) -> str:
    import audioop

    mulaw = audioop.lin2ulaw(pcm16, 2)
    return json.dumps(
        {
            "event": "media",
            "media": {"payload": base64.b64encode(mulaw).decode(), "chunk": str(chunk), "timestamp": str(chunk * 20)},
        }
    )


def _tone_pcm16(num_samples: int = 160, freq_hz: float = 300.0, amplitude: int = 12000, sample_rate: int = 8000) -> bytes:
    """Loud enough to clear EnergyVADModel's speech_threshold after 8kHz->16kHz resample."""
    samples = [int(amplitude * sin(2 * pi * freq_hz * i / sample_rate)) for i in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


def _silence_pcm16(num_samples: int = 160) -> bytes:
    return b"\x00\x00" * num_samples


def _make_app() -> tuple[TestClient, SharedCallDependencies]:
    conversation_engine = MagicMock()
    clause = AudioClause(audio_data=b"\x00\x00" * 2048, sample_rate=24000, text="namaste", clause_index=0, is_final=True)
    conversation_engine.handle_turn = AsyncMock(return_value=[clause])
    # Path-A Phase 6g: build_greeting() returns None when no dialogue_response
    # is wired (the real ConversationEngine's actual behavior) — makes
    # CallOrchestrator.run()'s call-start greeting a no-op for this fixture.
    conversation_engine.build_greeting = MagicMock(return_value=None)

    stt_service = MagicMock()

    async def _fake_words(*_a: object, **_kw: object) -> AsyncIterator[WordHypothesis]:
        yield WordHypothesis(word="hello", confidence=0.9, start_ms=0, end_ms=200, is_final=True)

    stt_service.transcribe_stream = MagicMock(side_effect=lambda *a, **kw: _fake_words())

    deps = SharedCallDependencies(
        account_sid=_ACCOUNT_SID,
        auth_token=_AUTH_TOKEN,
        tenant_id="tenant-1",
        media_gateway_service=MediaGatewayService(),
        audio_session_manager_service=AudioSessionManagerService(),
        audio_preprocessor=AudioPreprocessorService(enabled_stages={"resample"}),
        stt_service=stt_service,
        conversation_engine=conversation_engine,
    )
    app = create_twilio_media_stream_app(deps)
    return TestClient(app), deps


def test_accepts_connection_when_signed_against_public_tunnel_url() -> None:
    """Real-deployment case: this process runs behind a tunnel/reverse-proxy
    (e.g. a cloudflared quick tunnel for a live Twilio call), so it never
    observes the public wss:// URL Twilio actually signed against — only
    ws://testserver/... locally. SharedCallDependencies.public_ws_base_url
    must be used to reconstruct the same URL Twilio signed, or every real
    connection would fail AR-2 auth despite a genuinely valid signature."""
    client, deps = _make_app()
    deps.public_ws_base_url = "wss://random-words.trycloudflare.com"
    public_url = "wss://random-words.trycloudflare.com/twilio/media-stream"
    signature = _twilio_signature(public_url)

    with client.websocket_connect("/twilio/media-stream", headers={"x-twilio-signature": signature}) as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(
            {
                "event": "start",
                "start": {"callSid": "CA_tunnel_001", "streamSid": "MZtunnel001", "mediaFormat": {"sampleRate": 8000}},
            }
        )
        deps.conversation_engine.handle_turn.assert_not_awaited()
        ws.send_json(
            {
                "event": "stop",
                "stop": {"callSid": "CA_tunnel_001"},
            }
        )
    # No exception/rejection on connect — reaching here without the
    # "expected the server to close" failure mode proves auth succeeded.


def test_assembles_real_customer_context_when_customer_id_parameter_present() -> None:
    """Outbound trial calls (scripts/place_call002.py) pass customer_id as a
    <Stream><Parameter> so the greeting/replies can address the real
    customer by name — previously nothing here ever called start_call() at
    all, so context was always None and every greeting used an empty name."""
    client, deps = _make_app()
    deps.conversation_engine.start_call = MagicMock()
    url = "ws://testserver/twilio/media-stream"
    signature = _twilio_signature(url)

    with client.websocket_connect("/twilio/media-stream", headers={"x-twilio-signature": signature}) as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(
            {
                "event": "start",
                "start": {
                    "callSid": "CA_ctx_001",
                    "streamSid": "MZctx001",
                    "customParameters": {"customer_id": "cust-prateek-001"},
                    "mediaFormat": {"sampleRate": 8000},
                },
            }
        )
        ws.send_json({"event": "stop", "stop": {"callSid": "CA_ctx_001"}})

    deps.conversation_engine.start_call.assert_called_once()
    kwargs = deps.conversation_engine.start_call.call_args.kwargs
    assert kwargs["customer_id"] == "cust-prateek-001"
    assert kwargs["call_id"] == "CA_ctx_001"


def test_rejects_connection_with_invalid_signature() -> None:
    client, _ = _make_app()
    with client.websocket_connect(
        "/twilio/media-stream", headers={"x-twilio-signature": "not-a-real-signature"}
    ) as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(
            {
                "event": "start",
                "start": {"callSid": "CA_reject_001", "streamSid": "MZ1", "mediaFormat": {"sampleRate": 8000}},
            }
        )
        # Server closes the connection after rejecting authentication.
        try:
            ws.receive_json()
            raised = False
        except Exception:
            raised = True
    assert raised, "expected the server to close the connection on bad signature"


def test_accepts_connection_and_completes_a_turn_with_valid_signature() -> None:
    client, deps = _make_app()
    url = "ws://testserver/twilio/media-stream"
    signature = _twilio_signature(url)

    with client.websocket_connect("/twilio/media-stream", headers={"x-twilio-signature": signature}) as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(
            {
                "event": "start",
                "start": {
                    "callSid": "CA_accept_001",
                    "streamSid": "MZaccept001",
                    "customParameters": {"caller_phone": "+919876543210"},
                    "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                },
            }
        )

        # ~300ms of loud tone (clears EndpointDetector's 100ms start threshold).
        for i in range(15):
            ws.send_json(json.loads(_mulaw_media_message(i, _tone_pcm16())))

        # ~700ms of silence (clears the 600ms end threshold) to close the turn.
        for i in range(15, 50):
            ws.send_json(json.loads(_mulaw_media_message(i, _silence_pcm16())))

        # The orchestrator should have called ConversationEngine and pushed
        # at least one outbound media message back over the WebSocket.
        received_media = False
        for _ in range(20):
            msg = ws.receive_text()
            payload = json.loads(msg)
            if payload.get("event") == "media":
                received_media = True
                assert payload["streamSid"] == "MZaccept001"
                break

        ws.send_json({"event": "stop"})

    assert received_media, "expected at least one outbound Twilio media message"
    deps.conversation_engine.handle_turn.assert_awaited()
