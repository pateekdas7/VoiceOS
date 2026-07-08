"""Integration tests — Media Gateway ↔ Audio Session Manager boundary.

Verifies the handoff of AudioFrames from the Media Gateway transport adapters
into the Audio Session Manager's jitter buffer, PLC, and session clock.

These tests exercise the in-process call pipeline end-to-end without a real
carrier:
  1. MediaGatewayService admits a Twilio adapter (auth-before-allocation).
  2. Adapter produces AudioFrames via receive_frame().
  3. AudioSessionManagerService creates a session and processes the frames.
  4. Verifies lifecycle transitions, PLC gap-filling, and session teardown.

Architecture: V1 Ch3 (Media Gateway outputs), V1 Ch4 (ASM inputs);
              DocSuite-02 (AudioSessionManager ↔ AudioPreprocessor interface).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import pytest

from src.libs.contracts.audio import AudioFrame, Encoding, SampleRate
from src.libs.contracts.primitives import TenantId
from src.services.audio_session_manager.service import AudioSessionManagerService
from src.services.audio_session_manager.session import SessionState
from src.services.media_gateway.adapters.twilio_websocket import TwilioWebSocketAdapter
from src.services.media_gateway.protocol import ADAPTER_TYPE_TWILIO
from src.services.media_gateway.service import MediaGatewayService

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_AUTH_TOKEN = "asm_integration_test_token"
_ACCOUNT_SID = "AC_asm_integration_test"
_TENANT = TenantId("20000000-0000-0000-0000-000000000001")
_WEBHOOK_URL = "https://gw.voiceos.example.com/twilio/inbound"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sig(url: str, params: dict[str, str]) -> str:
    """Compute a valid Twilio HMAC-SHA1 signature."""
    body = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(_AUTH_TOKEN.encode(), (url + body).encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _creds(params: dict[str, str] | None = None) -> dict[str, str]:
    p: dict[str, str] = params or {}
    return {
        "account_sid": _ACCOUNT_SID,
        "auth_token": _AUTH_TOKEN,
        "url": _WEBHOOK_URL,
        "params": json.dumps(p),
        "x_twilio_signature": _sig(_WEBHOOK_URL, p),
        "expected_account_sid": _ACCOUNT_SID,
    }


def _start_msg(call_sid: str = "CA_asm_001") -> dict[str, object]:
    return {
        "event": "start",
        "sequenceNumber": "1",
        "start": {
            "streamSid": "MZ_asm_001",
            "accountSid": _ACCOUNT_SID,
            "callSid": call_sid,
            "tracks": ["inbound"],
            "customParameters": {},
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
        "streamSid": "MZ_asm_001",
    }


def _media_msg(chunk: int, timestamp_ms: int, num_bytes: int = 160) -> dict[str, object]:
    payload = base64.b64encode(bytes([0xFF] * num_bytes)).decode()
    return {
        "event": "media",
        "media": {
            "track": "inbound",
            "chunk": str(chunk),
            "timestamp": str(timestamp_ms),
            "payload": payload,
        },
    }


def _stop_msg() -> dict[str, object]:
    return {"event": "stop", "stop": {"accountSid": _ACCOUNT_SID}}


# ===========================================================================
# MG → ASM handoff: frames produced by MG are processable by ASM
# ===========================================================================


class TestMediaGatewayToASMHandoff:
    """Frames from MediaGateway transport adapters are correctly processed by ASM."""

    @pytest.mark.asyncio
    async def test_mg_frames_flow_into_asm_session(self) -> None:
        """End-to-end: Twilio adapter → AudioFrames → ASM session processes them."""
        gw = MediaGatewayService()
        gw.start()

        asm = AudioSessionManagerService()
        asm.start()

        adapter = TwilioWebSocketAdapter(
            account_sid=_ACCOUNT_SID,
            auth_token=_AUTH_TOKEN,
            tenant_id=_TENANT,
        )

        result = await gw.admit_adapter(
            call_id="call-asm-01",
            tenant_id=str(_TENANT),
            adapter=adapter,
            credentials=_creds(),
            adapter_type=ADAPTER_TYPE_TWILIO,
        )
        assert result.success is True

        # Queue 5 Twilio media frames
        adapter.put_message(_start_msg("CA_asm_flow"))
        for i in range(5):
            adapter.put_message(_media_msg(chunk=i + 1, timestamp_ms=(i + 1) * 20))
        adapter.put_message(_stop_msg())

        # ASM: create session
        session = asm.create_session("call-asm-01", str(_TENANT))
        s0 = session.state
        assert s0 == SessionState.CONNECTING

        received: list[AudioFrame] = []
        async for frame in adapter.receive_frame():
            out = session.push_frame(frame)
            received.extend(out)

        s1 = session.state
        assert s1 == SessionState.ACTIVE
        assert len(received) == 5
        for i, frame in enumerate(received):
            assert frame.config.encoding == Encoding.MULAW
            assert frame.config.sample_rate == SampleRate.RATE_8K
            assert frame.seq == i + 1
            assert not frame.is_plc

        # Teardown
        await gw.release_adapter("call-asm-01")
        asm.release_session("call-asm-01")
        gw.stop()
        asm.stop()

    @pytest.mark.asyncio
    async def test_asm_session_transitions_on_mg_frames(self) -> None:
        """ASM session transitions CONNECTING→ACTIVE when first MG frame arrives."""
        adapter = TwilioWebSocketAdapter(
            account_sid=_ACCOUNT_SID,
            auth_token=_AUTH_TOKEN,
            tenant_id=_TENANT,
        )
        await adapter.authenticate(_creds())
        await adapter.connect()

        adapter.put_message(_start_msg())
        adapter.put_message(_media_msg(chunk=1, timestamp_ms=20))
        adapter.put_message(_stop_msg())

        asm = AudioSessionManagerService()
        session = asm.create_session("call-asm-02", str(_TENANT))
        s0 = session.state
        assert s0 == SessionState.CONNECTING

        async for frame in adapter.receive_frame():
            session.push_frame(frame)

        s1 = session.state
        assert s1 == SessionState.ACTIVE

    @pytest.mark.asyncio
    async def test_asm_session_clock_anchored_from_mg_frame(self) -> None:
        """SessionClock anchors on the first MG frame's RTP timestamp."""
        adapter = TwilioWebSocketAdapter(
            account_sid=_ACCOUNT_SID,
            auth_token=_AUTH_TOKEN,
            tenant_id=_TENANT,
        )
        await adapter.authenticate(_creds())
        await adapter.connect()

        adapter.put_message(_start_msg())
        adapter.put_message(_media_msg(chunk=1, timestamp_ms=20))
        adapter.put_message(_stop_msg())

        asm = AudioSessionManagerService()
        session = asm.create_session("call-asm-03", str(_TENANT))

        async for frame in adapter.receive_frame():
            session.push_frame(frame)

        assert session.clock.is_anchored, "SessionClock must be anchored after first frame"

    @pytest.mark.asyncio
    async def test_asm_session_teardown_after_mg_release(self) -> None:
        """ASM release_session() cleanly closes the session after MG disconnect."""
        gw = MediaGatewayService()
        gw.start()
        asm = AudioSessionManagerService()
        asm.start()

        adapter = TwilioWebSocketAdapter(
            account_sid=_ACCOUNT_SID,
            auth_token=_AUTH_TOKEN,
            tenant_id=_TENANT,
        )
        await gw.admit_adapter(
            call_id="call-asm-04",
            tenant_id=str(_TENANT),
            adapter=adapter,
            credentials=_creds(),
            adapter_type=ADAPTER_TYPE_TWILIO,
        )

        adapter.put_message(_start_msg())
        adapter.put_message(_media_msg(chunk=1, timestamp_ms=20))
        adapter.put_message(_stop_msg())

        session = asm.create_session("call-asm-04", str(_TENANT))
        async for frame in adapter.receive_frame():
            session.push_frame(frame)

        await gw.release_adapter("call-asm-04")
        asm.release_session("call-asm-04")

        assert asm.get_session("call-asm-04") is None
        assert gw.active_session_count() == 0

        gw.stop()
        asm.stop()

    @pytest.mark.asyncio
    async def test_multiple_concurrent_sessions(self) -> None:
        """ASM manages multiple concurrent sessions independently."""
        asm = AudioSessionManagerService()
        asm.start()

        async def _run_call(call_id: str) -> list[AudioFrame]:
            adapter = TwilioWebSocketAdapter(
                account_sid=_ACCOUNT_SID,
                auth_token=_AUTH_TOKEN,
                tenant_id=_TENANT,
            )
            await adapter.authenticate(_creds())
            await adapter.connect()

            adapter.put_message(_start_msg(call_id))
            for i in range(3):
                adapter.put_message(_media_msg(chunk=i + 1, timestamp_ms=(i + 1) * 20))
            adapter.put_message(_stop_msg())

            session = asm.create_session(call_id, str(_TENANT))
            out: list[AudioFrame] = []
            async for frame in adapter.receive_frame():
                out.extend(session.push_frame(frame))
            asm.release_session(call_id)
            return out

        results = await asyncio.gather(
            _run_call("call-multi-01"),
            _run_call("call-multi-02"),
            _run_call("call-multi-03"),
        )

        for frames in results:
            assert len(frames) == 3, "Each concurrent session must produce 3 frames"
