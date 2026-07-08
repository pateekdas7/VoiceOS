"""Integration tests for the Media Gateway service (Sprint-004).

Required integration test (Sprint-004.md §Required Tests):
  test_twilio_websocket_full_flow — fake Twilio WebSocket sends
  connected+start+media+stop messages; AudioFrames are received by
  an integration test consumer.

These tests exercise the full in-process flow without a real carrier
connection.  They DO require asyncio (pytest-asyncio in auto mode).

Architecture: V6 Ch9 (Testing Standards); V1 Ch3 (Media Gateway).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import pytest

from src.libs.contracts.audio import AudioFrame, Encoding, SampleRate
from src.libs.contracts.primitives import CallId, TenantId
from src.services.media_gateway.adapters.sip_rtp import SIPRTPAdapter, parse_sip_invite
from src.services.media_gateway.adapters.twilio_websocket import TwilioWebSocketAdapter
from src.services.media_gateway.protocol import ADAPTER_TYPE_TWILIO, SIPInviteParams
from src.services.media_gateway.service import MediaGatewayService
from src.services.media_gateway.session_gate import SessionGate

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_AUTH_TOKEN = "integration_test_auth_token"
_TEST_ACCOUNT_SID = "AC_integration_test"
_TEST_TENANT = TenantId("10000000-0000-0000-0000-000000000001")
_WEBHOOK_URL = "https://gw.voiceos.example.com/twilio/inbound"

_SAMPLE_SIP_INVITE_INTEGRATION = """\
INVITE sip:voiceos@gw.example.com SIP/2.0\r
Via: SIP/2.0/UDP carrier.example.com:5060;branch=z9hG4bKintegration\r
Max-Forwards: 70\r
To: VoiceOS <sip:voiceos@gw.example.com>\r
From: Integration Test <sip:+919876543210@carrier.example.com>;tag=int_tag\r
Call-ID: integration-call-001@carrier.example.com\r
CSeq: 1 INVITE\r
Contact: <sip:+919876543210@carrier.example.com>\r
Content-Type: application/sdp\r
Content-Length: 120\r
\r
v=0\r
o=- 0 0 IN IP4 carrier.example.com\r
s=Integration Test\r
c=IN IP4 carrier.example.com\r
t=0 0\r
m=audio 0 RTP/AVP 0\r
a=rtpmap:0 PCMU/8000\r
"""


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_sig(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Compute a valid Twilio HMAC-SHA1 signature."""
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    payload = (url + sorted_params).encode()
    mac = hmac.new(auth_token.encode(), payload, hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _make_credentials(params: dict[str, str] | None = None) -> dict[str, str]:
    """Build a valid Twilio credentials dict for integration tests."""
    p = params or {}
    sig = _make_sig(_TEST_AUTH_TOKEN, _WEBHOOK_URL, p)
    return {
        "account_sid": _TEST_ACCOUNT_SID,
        "auth_token": _TEST_AUTH_TOKEN,
        "url": _WEBHOOK_URL,
        "params": json.dumps(p),
        "x_twilio_signature": sig,
        "expected_account_sid": _TEST_ACCOUNT_SID,
    }


def _mulaw_frame_msg(chunk: int, timestamp_ms: int, num_bytes: int = 160) -> dict:  # type: ignore[type-arg]
    """Build a Twilio 'media' message dict with mu-law silence payload."""
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


def _start_msg(call_sid: str = "CA_integration_001", stream_sid: str = "MZ_stream_001") -> dict:  # type: ignore[type-arg]
    """Build a Twilio 'start' message dict."""
    return {
        "event": "start",
        "sequenceNumber": "1",
        "start": {
            "streamSid": stream_sid,
            "accountSid": _TEST_ACCOUNT_SID,
            "callSid": call_sid,
            "tracks": ["inbound"],
            "customParameters": {"caller_phone": "+919876543210"},
            "mediaFormat": {
                "encoding": "audio/x-mulaw",
                "sampleRate": 8000,
                "channels": 1,
            },
        },
        "streamSid": stream_sid,
    }


# ===========================================================================
# TestTwilioWebSocketFullFlow — satisfies Sprint-004 required integration test
# ===========================================================================


class TestTwilioWebSocketFullFlow:
    """Integration test: complete Twilio Media Streams message sequence."""

    @pytest.mark.asyncio
    async def test_twilio_websocket_full_flow(self) -> None:
        """Required integration test: connected+start+media+stop → AudioFrames collected.

        Simulates a Twilio WebSocket sending the full sequence of messages for
        a 5-frame call.  Verifies that the integration test consumer receives
        the correct number of AudioFrames with the correct properties.
        """
        adapter = TwilioWebSocketAdapter(
            account_sid=_TEST_ACCOUNT_SID,
            auth_token=_TEST_AUTH_TOKEN,
            tenant_id=_TEST_TENANT,
        )

        # Step 1: authenticate (AR-2 — must succeed before any frames are produced).
        creds = _make_credentials()
        auth_result = await adapter.authenticate(creds)
        assert auth_result.success is True, f"Auth failed: {auth_result.reason}"

        # Step 2: connect the adapter.
        await adapter.connect()
        assert adapter.is_connected is True

        # Step 3: simulate Twilio sending the full message sequence.
        # connected → start → 5 x media → stop
        adapter.put_message({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        adapter.put_message(_start_msg(call_sid="CA_integration_001"))
        for i in range(5):
            adapter.put_message(_mulaw_frame_msg(chunk=i + 1, timestamp_ms=(i + 1) * 20))
        adapter.put_message({"event": "stop", "stop": {"accountSid": _TEST_ACCOUNT_SID}})

        # Step 4: consume the frame stream.
        frames: list[AudioFrame] = []
        async for frame in adapter.receive_frame():
            frames.append(frame)

        # Step 5: assert correct outcome.
        assert len(frames) == 5, f"Expected 5 frames, got {len(frames)}"
        for i, frame in enumerate(frames):
            assert frame.config.encoding == Encoding.MULAW
            assert frame.config.sample_rate == SampleRate.RATE_8K
            assert frame.config.channels == 1
            assert len(frame.pcm_data) == 160
            assert frame.seq == i + 1
            assert frame.recv_ts > 0.0

    @pytest.mark.asyncio
    async def test_twilio_full_flow_emits_session_started_event(self) -> None:
        """AC-4: AudioSessionStarted is emitted during the full flow."""
        adapter = TwilioWebSocketAdapter(
            account_sid=_TEST_ACCOUNT_SID,
            auth_token=_TEST_AUTH_TOKEN,
            tenant_id=_TEST_TENANT,
        )
        creds = _make_credentials()
        await adapter.authenticate(creds)
        await adapter.connect()

        adapter.put_message({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        adapter.put_message(_start_msg(call_sid="CA_evt_001"))
        adapter.put_message(_mulaw_frame_msg(chunk=1, timestamp_ms=20))
        adapter.put_message({"event": "stop", "stop": {}})

        async for _ in adapter.receive_frame():
            pass

        events = adapter.events
        assert len(events) == 1
        assert events[0].event_type == "audio.session.started"
        assert events[0].call_id == CallId("CA_evt_001")
        assert events[0].encoding == "audio/x-mulaw"
        assert events[0].sample_rate == 8000

    @pytest.mark.asyncio
    async def test_twilio_full_flow_disconnect_terminates_stream(self) -> None:
        """disconnect() terminates the frame stream mid-flow."""
        adapter = TwilioWebSocketAdapter(
            account_sid=_TEST_ACCOUNT_SID,
            auth_token=_TEST_AUTH_TOKEN,
            tenant_id=_TEST_TENANT,
        )
        creds = _make_credentials()
        await adapter.authenticate(creds)
        await adapter.connect()

        # Push frames but do not push stop — disconnect terminates.
        for i in range(3):
            adapter.put_message(_mulaw_frame_msg(chunk=i, timestamp_ms=i * 20))

        # Disconnect after producing 3 frames.
        async def _producer() -> None:
            await asyncio.sleep(0.01)
            await adapter.disconnect()

        frames: list[AudioFrame] = []

        async def _consumer() -> None:
            async for frame in adapter.receive_frame():
                frames.append(frame)

        await asyncio.gather(_producer(), _consumer())

        # All 3 pre-queued frames should be consumed before disconnect closes the stream.
        assert len(frames) == 3

    @pytest.mark.asyncio
    async def test_twilio_full_flow_with_service(self) -> None:
        """MediaGatewayService orchestrates admit + full message flow end-to-end."""
        gw = MediaGatewayService()
        gw.start()

        creds = _make_credentials()
        adapter = TwilioWebSocketAdapter(
            account_sid=_TEST_ACCOUNT_SID,
            auth_token=_TEST_AUTH_TOKEN,
            tenant_id=_TEST_TENANT,
        )

        result = await gw.admit_adapter(
            call_id="call-int-01",
            tenant_id=str(_TEST_TENANT),
            adapter=adapter,
            credentials=creds,
            adapter_type=ADAPTER_TYPE_TWILIO,
        )
        assert result.success is True
        assert gw.active_session_count() == 1

        # Push messages after admission.
        adapter.put_message(_start_msg(call_sid="CA_svc_001"))
        adapter.put_message(_mulaw_frame_msg(chunk=1, timestamp_ms=20))
        adapter.put_message({"event": "stop", "stop": {}})

        retrieved_adapter = gw.get_adapter("call-int-01")
        assert retrieved_adapter is adapter

        frames: list[AudioFrame] = []
        async for frame in adapter.receive_frame():
            frames.append(frame)
        assert len(frames) == 1

        await gw.release_adapter("call-int-01")
        assert gw.active_session_count() == 0

    @pytest.mark.asyncio
    async def test_twilio_auth_failure_rejected_by_service(self) -> None:
        """AC-2: Service rejects adapter with invalid credentials (no session admitted)."""
        gw = MediaGatewayService()
        adapter = TwilioWebSocketAdapter(
            account_sid=_TEST_ACCOUNT_SID,
            auth_token=_TEST_AUTH_TOKEN,
            tenant_id=_TEST_TENANT,
        )
        bad_creds = {
            "account_sid": _TEST_ACCOUNT_SID,
            "auth_token": _TEST_AUTH_TOKEN,
            "url": _WEBHOOK_URL,
            "params": "{}",
            "x_twilio_signature": "completely_wrong",
            "expected_account_sid": _TEST_ACCOUNT_SID,
        }
        result = await gw.admit_adapter(
            call_id="call-bad-01",
            tenant_id=str(_TEST_TENANT),
            adapter=adapter,
            credentials=bad_creds,
            adapter_type=ADAPTER_TYPE_TWILIO,
        )
        assert result.success is False
        assert gw.active_session_count() == 0
        assert gw.get_adapter("call-bad-01") is None


# ===========================================================================
# SIP/RTP integration flow
# ===========================================================================


class TestSIPRTPFlow:
    """SIP/RTP adapter: INVITE parsing → auth → connect → session admitted."""

    def test_sip_invite_parse_integration(self) -> None:
        """parse_sip_invite returns correct SIPInviteParams for integration fixture."""
        params = parse_sip_invite(_SAMPLE_SIP_INVITE_INTEGRATION)
        assert isinstance(params, SIPInviteParams)
        assert params.rtp_port == 0  # m=audio 0 means OS-assigned
        assert params.codec == "PCMU"
        assert "+919876543210" in params.from_header
        assert params.call_id == "integration-call-001@carrier.example.com"

    @pytest.mark.asyncio
    async def test_sip_adapter_auth_success(self) -> None:
        """SIPRTPAdapter authenticates a valid From header."""
        adapter = SIPRTPAdapter(tenant_id=_TEST_TENANT)
        adapter.process_invite(_SAMPLE_SIP_INVITE_INTEGRATION)

        result = await adapter.authenticate(
            {
                "from_header": adapter.invite_params.from_header if adapter.invite_params else "",
                "allowed_from_prefix": "Integration",
            }
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_sip_adapter_connect_binds_socket(self) -> None:
        """connect() binds a real UDP socket on an OS-assigned port."""
        adapter = SIPRTPAdapter(tenant_id=_TEST_TENANT)
        # Use rtp_port=0 so the OS assigns an available port.
        invite = _SAMPLE_SIP_INVITE_INTEGRATION
        adapter.process_invite(invite)

        await adapter.authenticate(
            {"from_header": "Integration Test <sip:+919876543210@carrier.example.com>", "allowed_from_prefix": ""}
        )
        await adapter.connect()

        assert adapter.is_connected is True
        # OS assigns a real port (> 0).
        assert adapter.rtp_port > 0

        await adapter.disconnect()
        assert adapter.is_connected is False

    @pytest.mark.asyncio
    async def test_sip_adapter_emits_session_started_event(self) -> None:
        """AC-4: AudioSessionStarted emitted after SIP connect()."""
        adapter = SIPRTPAdapter(tenant_id=_TEST_TENANT)
        adapter.process_invite(_SAMPLE_SIP_INVITE_INTEGRATION)

        await adapter.authenticate(
            {"from_header": "Integration Test <sip:+919876543210@carrier.example.com>", "allowed_from_prefix": ""}
        )
        await adapter.connect()

        events = adapter.events
        assert len(events) == 1
        assert events[0].event_type == "audio.session.started"

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_sip_connect_without_auth_raises(self) -> None:
        """connect() without prior auth raises RuntimeError (AR-2)."""
        adapter = SIPRTPAdapter(tenant_id=_TEST_TENANT)
        adapter.process_invite(_SAMPLE_SIP_INVITE_INTEGRATION)
        with pytest.raises(RuntimeError, match="AR-2"):
            await adapter.connect()

    @pytest.mark.asyncio
    async def test_sip_connect_without_invite_raises(self) -> None:
        """connect() without process_invite() raises RuntimeError."""
        adapter = SIPRTPAdapter(tenant_id=_TEST_TENANT)
        adapter._authenticated = True  # bypass auth for this specific test
        with pytest.raises(RuntimeError, match="process_invite"):
            await adapter.connect()


# ===========================================================================
# SessionGate integration
# ===========================================================================


class TestSessionGateIntegration:
    """SessionGate correctly manages multi-tenant session tracking."""

    def test_multi_call_admission_and_release(self) -> None:
        """Multiple calls can be admitted and released independently."""
        gate = SessionGate()
        gate.admit(call_id="c1", tenant_id="t1", adapter_type=ADAPTER_TYPE_TWILIO)
        gate.admit(call_id="c2", tenant_id="t1", adapter_type=ADAPTER_TYPE_TWILIO)
        gate.admit(call_id="c3", tenant_id="t2", adapter_type=ADAPTER_TYPE_TWILIO)

        assert gate.session_count() == 3
        assert gate.tenant_session_count("t1") == 2

        gate.release("c1")
        assert gate.session_count() == 2
        assert gate.tenant_session_count("t1") == 1
        assert not gate.is_admitted("c1")
        assert gate.is_admitted("c2")

    def test_reject_increments_and_session_count_unchanged(self) -> None:
        """Rejecting a call does not change the admitted session count."""
        gate = SessionGate()
        gate.admit(call_id="c1", tenant_id="t1", adapter_type=ADAPTER_TYPE_TWILIO)
        gate.reject(call_id="c-bad", reason="auth_failed", adapter_type=ADAPTER_TYPE_TWILIO)
        # Only c1 is admitted; c-bad was rejected.
        assert gate.session_count() == 1
        assert not gate.is_admitted("c-bad")

    def test_session_metadata_stored_correctly(self) -> None:
        """AdmittedSession carries all expected metadata fields."""
        gate = SessionGate()
        session = gate.admit(
            call_id="c-meta",
            tenant_id="t-meta",
            adapter_type=ADAPTER_TYPE_TWILIO,
            rtp_port=49172,
        )
        assert session.call_id == "c-meta"
        assert session.tenant_id == "t-meta"
        assert session.adapter_type == ADAPTER_TYPE_TWILIO
        assert session.admitted_at > 0
        assert session.rtp_port == 49172
