"""Tenant isolation integration tests for the real Twilio media boundary."""

import base64
import hashlib
import hmac
import re
from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient

from src.services.audio_preprocessing.service import AudioPreprocessorService
from src.services.audio_session_manager.service import AudioSessionManagerService
from src.services.media_gateway.service import MediaGatewayService
from src.services.media_gateway.twilio_ws_entrypoint import SharedCallDependencies, create_twilio_media_stream_app

ACCOUNT = "ACtestaccount"
TOKEN = "test-auth-token"
PUBLIC = "https://voice.example.test"


def _signature(url: str, params: dict[str, str]) -> str:
    payload = (url + "".join(f"{k}{v}" for k, v in sorted(params.items()))).encode()
    return base64.b64encode(hmac.new(TOKEN.encode(), payload, hashlib.sha1).digest()).decode()


def test_outbound_number_resolves_tenant_and_wss_uses_same_tenant() -> None:
    engine = MagicMock()
    engine.start_call = MagicMock()
    engine.build_greeting = MagicMock(return_value=None)
    engine.handle_turn = AsyncMock(return_value=[])

    resolver = MagicMock()
    number = MagicMock()
    number.tenant_id = "tenant-b"
    resolver.resolve_for_call.return_value = number

    deps = SharedCallDependencies(
        account_sid=ACCOUNT,
        auth_token=TOKEN,
        tenant_id="tenant-default",
        media_gateway_service=MediaGatewayService(),
        audio_session_manager_service=AudioSessionManagerService(),
        audio_preprocessor=AudioPreprocessorService(enabled_stages={"resample"}),
        stt_service=MagicMock(),
        conversation_engine=engine,
        telephony_number_resolver=resolver,
        public_ws_base_url="wss://voice.example.test",
    )
    client = TestClient(create_twilio_media_stream_app(deps))

    params = {
        "CallSid": "CAtenant001",
        "AccountSid": ACCOUNT,
        "From": "+919900000001",
        "To": "+919900000002",
        "Direction": "outbound-api",
    }
    sig = _signature(f"{PUBLIC}/voice", params)
    response = client.post("/voice", data=params, headers={"x-twilio-signature": sig})
    assert response.status_code == 200
    match = re.search(r'name="admission_token" value="([^"]+)"', response.text)
    assert match is not None
    token = match.group(1)
    resolver.resolve_for_call.assert_called_once_with("+919900000001", direction="outbound-api")

    with client.websocket_connect("/twilio/media-stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json({
            "event": "start",
            "start": {
                "callSid": "CAtenant001",
                "streamSid": "MZtenant001",
                "accountSid": ACCOUNT,
                "customParameters": {"admission_token": token},
                "mediaFormat": {"sampleRate": 8000},
            },
        })
        ws.send_json({"event": "stop", "stop": {"callSid": "CAtenant001"}})

    engine.start_call.assert_called_once()
    assert str(engine.start_call.call_args.kwargs["tenant_id"]) == "tenant-b"


def test_unassigned_provider_number_is_rejected_before_admission() -> None:
    deps = SharedCallDependencies(
        account_sid=ACCOUNT,
        auth_token=TOKEN,
        tenant_id="tenant-default",
        media_gateway_service=MediaGatewayService(),
        audio_session_manager_service=AudioSessionManagerService(),
        audio_preprocessor=AudioPreprocessorService(enabled_stages={"resample"}),
        stt_service=MagicMock(),
        conversation_engine=MagicMock(),
        telephony_number_resolver=MagicMock(),
        public_ws_base_url="wss://voice.example.test",
    )
    deps.telephony_number_resolver.resolve_for_call.return_value = None
    client = TestClient(create_twilio_media_stream_app(deps))
    params = {
        "CallSid": "CAtenant002",
        "AccountSid": ACCOUNT,
        "From": "+919900000099",
        "To": "+919900000002",
        "Direction": "outbound-api",
    }
    sig = _signature(f"{PUBLIC}/voice", params)
    response = client.post("/voice", data=params, headers={"x-twilio-signature": sig})
    assert response.status_code == 403
    assert deps.admission_registry.size() == 0
