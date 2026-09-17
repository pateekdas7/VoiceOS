"""Integration test for the Twilio Media Streams WS entrypoint.

Drives the REAL Starlette app (create_twilio_media_stream_app) end to end
through the two-stage admission protocol introduced to replace the
broken X-Twilio-Signature-on-WSS design:

    1. HTTP POST /voice with a valid Twilio-style signature over the POSTed
       form body → server mints a single-use admission token bound to
       (CallSid, AccountSid, tenant_id) and returns TwiML containing
       <Stream><Parameter name="admission_token" value="…"/>.
    2. Extract the admission token from the TwiML.
    3. Open WSS /twilio/media-stream; send `start` with the admission
       token in customParameters; the server verifies+consumes the token
       and admits the call.

Real auth + real MediaGatewayService/AudioSessionManagerService/
AudioPreprocessorService(resample-only) + real
VADEndpointingService(EnergyVADModel). Fake STT and ConversationEngine
(GPU / Postgres are their own suites' concerns) — this test's job is
proving the WS<->audio pipeline wiring itself, end to end, under the
new admission protocol.

Architecture: V1 Ch3-9; Phase I Gate 3 (two-stage admission).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
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
_PUBLIC_HTTP_BASE = "https://tunnel.example.com"
_PUBLIC_WS_BASE = "wss://tunnel.example.com"


def _twilio_signature(url: str, params: dict[str, str]) -> str:
    """Fabricate the X-Twilio-Signature Twilio would send for this POST.

    Matches src/services/media_gateway/auth.py::validate_twilio_signature —
    HMAC-SHA1 over URL + alphabetically-sorted param key/value pairs.
    """
    sorted_pairs = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    payload = (url + sorted_pairs).encode()
    mac = hmac.new(_AUTH_TOKEN.encode(), payload, hashlib.sha1)
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
    conversation_engine.build_greeting = MagicMock(return_value=None)

    stt_service = MagicMock()

    async def _fake_words(*_a: object, **_kw: object) -> AsyncIterator[WordHypothesis]:
        yield WordHypothesis(word="hello", confidence=0.9, start_ms=0, end_ms=200, is_final=True)

    stt_service.transcribe_stream = AsyncMock(side_effect=lambda *a, **kw: _fake_words())

    deps = SharedCallDependencies(
        account_sid=_ACCOUNT_SID,
        auth_token=_AUTH_TOKEN,
        tenant_id="tenant-1",
        media_gateway_service=MediaGatewayService(),
        audio_session_manager_service=AudioSessionManagerService(),
        audio_preprocessor=AudioPreprocessorService(enabled_stages={"resample"}),
        stt_service=stt_service,
        conversation_engine=conversation_engine,
        public_ws_base_url=_PUBLIC_WS_BASE,
    )
    app = create_twilio_media_stream_app(deps)
    return TestClient(app), deps


def _default_call_params(call_sid: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    p = {
        "CallSid": call_sid,
        "AccountSid": _ACCOUNT_SID,
        "From": "+19999999999",
        "To": "+18888888888",
        "CallStatus": "in-progress",
        "ApiVersion": "2010-04-01",
        "Direction": "outbound-api",
    }
    if extra:
        p.update(extra)
    return p


def _mint_admission_token(client: TestClient, call_sid: str, *, extra_params: dict[str, str] | None = None) -> str:
    """POST /voice with a valid signature and extract the admission token
    from the returned TwiML — exactly what a real Twilio → server exchange
    produces before the WSS upgrade."""
    params = _default_call_params(call_sid, extra_params)
    url = f"{_PUBLIC_HTTP_BASE}/voice"
    sig = _twilio_signature(url, params)
    resp = client.post("/voice", data=params, headers={"x-twilio-signature": sig})
    assert resp.status_code == 200, resp.text
    m = re.search(r'name="admission_token"\s+value="([^"]+)"', resp.text)
    assert m is not None, f"no admission_token in TwiML: {resp.text!r}"
    return m.group(1)


def test_accepts_connection_when_admission_token_matches_minted_call() -> None:
    """The public-URL/signature reconstruction still works — /voice signs
    against public_ws_base_url's HTTP equivalent so Twilio's signature
    validates behind a tunnel — and the emitted TwiML then admits the
    WSS upgrade for the same CallSid."""
    client, deps = _make_app()

    token = _mint_admission_token(client, "CA_tunnel_001")

    with client.websocket_connect("/twilio/media-stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(
            {
                "event": "start",
                "start": {
                    "callSid": "CA_tunnel_001",
                    "streamSid": "MZtunnel001",
                    "accountSid": _ACCOUNT_SID,
                    "customParameters": {"admission_token": token},
                    "mediaFormat": {"sampleRate": 8000},
                },
            }
        )
        deps.conversation_engine.handle_turn.assert_not_awaited()
        ws.send_json({"event": "stop", "stop": {"callSid": "CA_tunnel_001"}})


def test_assembles_real_customer_context_when_customer_id_parameter_present() -> None:
    """Outbound trials pass customer_id via <Stream><Parameter>. The
    /voice→WSS admission path still forwards other <Parameter>s, so
    ConversationEngine.start_call receives the customer_id and the
    real CustomerContext (name, etc.) is used for greetings/replies."""
    client, deps = _make_app()
    deps.conversation_engine.start_call = MagicMock()

    token = _mint_admission_token(client, "CA_ctx_001")

    with client.websocket_connect("/twilio/media-stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(
            {
                "event": "start",
                "start": {
                    "callSid": "CA_ctx_001",
                    "streamSid": "MZctx001",
                    "accountSid": _ACCOUNT_SID,
                    "customParameters": {
                        "admission_token": token,
                        "customer_id": "cust-prateek-001",
                    },
                    "mediaFormat": {"sampleRate": 8000},
                },
            }
        )
        ws.send_json({"event": "stop", "stop": {"callSid": "CA_ctx_001"}})

    deps.conversation_engine.start_call.assert_called_once()
    kwargs = deps.conversation_engine.start_call.call_args.kwargs
    assert kwargs["customer_id"] == "cust-prateek-001"
    assert kwargs["call_id"] == "CA_ctx_001"


def test_rejects_wss_without_admission_token() -> None:
    """The new admission protocol replaces the (broken) X-Twilio-Signature
    check on WSS. A WebSocket that presents no admission_token — no
    matter what headers it sends — must be rejected before any
    orchestrator resource is allocated."""
    client, _ = _make_app()
    try:
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
            ws.receive_json()
            raised = False
    except Exception:
        raised = True
    assert raised, "expected the server to close the connection with no admission_token"


def test_accepts_admitted_connection_and_completes_a_turn() -> None:
    client, deps = _make_app()

    token = _mint_admission_token(client, "CA_accept_001")

    with client.websocket_connect("/twilio/media-stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(
            {
                "event": "start",
                "start": {
                    "callSid": "CA_accept_001",
                    "streamSid": "MZaccept001",
                    "accountSid": _ACCOUNT_SID,
                    "customParameters": {
                        "admission_token": token,
                        "caller_phone": "+919876543210",
                    },
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
