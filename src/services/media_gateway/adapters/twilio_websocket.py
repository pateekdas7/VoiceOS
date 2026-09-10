"""Twilio Media Streams WebSocket adapter for the Media Gateway.

Handles inbound audio from Twilio's Media Streams protocol.  Twilio opens a
WebSocket to this service when a call is answered; it then streams a sequence
of JSON messages:

  connected  — WebSocket handshake confirmation (no audio)
  start      — call metadata (AccountSid, CallSid, codec, sample rate)
  media      — one audio frame per message (base64 mu-law payload)
  stop       — call ended; no more media

Auth-before-allocation (AR-2): the TwilioWebSocketAdapter validates the
X-Twilio-Signature HMAC-SHA1 header before any session resource is created.

Architecture: V1 Ch3 (Media Gateway — Twilio transport);
              V6 Ch4 AR-2 (auth-before-allocation).
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.libs.contracts.events.audio_events import AudioSessionStarted
from src.libs.contracts.primitives import CallId, TenantId
from src.services.media_gateway import metrics
from src.services.media_gateway.auth import ConnectionAuthenticator
from src.services.media_gateway.protocol import (
    ADAPTER_TYPE_TWILIO,
    AuthResult,
    TransportAdapter,
)

# Standard Twilio Media Streams audio format (G.711 mu-law, 8 kHz, mono).
_TWILIO_AUDIO_CONFIG = AudioConfig(
    sample_rate=SampleRate.RATE_8K,
    encoding=Encoding.MULAW,
    channels=1,
    frame_duration_ms=20,
)

# RTP timestamp increment per 20 ms frame at 8 kHz = 160 samples.
_RTP_TS_INCREMENT: int = 160

# Sentinel pushed to the message queue to signal orderly stream termination.
_STOP_SENTINEL: None = None


class TwilioWebSocketAdapter(TransportAdapter):
    """Adapter for Twilio Media Streams inbound audio.

    The WebSocket server layer (added in a later deployment sprint) calls
    ``put_message()`` for each Twilio JSON message it receives.
    This adapter decodes the messages and exposes them as an async stream
    of ``AudioFrame`` objects via ``receive_frame()``.

    Usage::

        adapter = TwilioWebSocketAdapter(
            account_sid="ACxxx",
            auth_token="...",
            tenant_id=TenantId("tenant-uuid"),
        )
        auth = await adapter.authenticate(credentials)
        if auth.success:
            await adapter.connect()
            async for frame in adapter.receive_frame():
                # hand frame to AudioSessionManager
                ...

    Architecture: V1 Ch3 (Twilio transport); V6 Ch4 AR-2.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        tenant_id: TenantId,
        *,
        call_id: CallId | None = None,
    ) -> None:
        """
        Args:
            account_sid: The Twilio AccountSid this adapter expects.
            auth_token:  Twilio auth token for HMAC-SHA1 signature validation.
            tenant_id:   Owning tenant (AR-8).
            call_id:     Optional pre-assigned CallId (generated from stream SID
                         if not provided).
        """
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._tenant_id = tenant_id
        self._call_id: CallId | None = call_id

        self._authenticator = ConnectionAuthenticator()
        self._authenticated = False
        self._connected = False

        # Inbound message queue (populated by put_message from WebSocket server).
        self._message_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        # Outbound encoded frames (consumed by the WebSocket server layer).
        self._outbound_queue: asyncio.Queue[bytes] = asyncio.Queue()

        # Domain events emitted during the session lifecycle.
        self._events: list[AudioSessionStarted] = []

        # RTP sequencing state.
        self._seq: int = 0
        self._rtp_ts: int = 0

        # Stream metadata populated on 'start' message.
        self._stream_sid: str | None = None
        self._caller_phone: str = ""

    # ------------------------------------------------------------------
    # TransportAdapter protocol
    # ------------------------------------------------------------------

    async def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        """Validate carrier credentials before admitting the session (AR-2).

        Two supported credential shapes:

        * Admission-verified (Twilio Media Streams WSS path).
          The `/twilio/media-stream` entrypoint runs the two-stage
          admission protocol (see admission.py) — HTTP `/voice` mints a
          single-use token bound to (CallSid, AccountSid, tenant_id)
          which the WSS handler verifies + consumes against the
          AdmissionRegistry BEFORE calling admit_adapter. On success it
          hands us credentials with `admission_verified="true"` — a
          private in-process signal set only after that registry consume
          succeeded. This branch re-checks
          `account_sid == expected_account_sid` as defense in depth and
          returns success. Twilio never sends X-Twilio-Signature on the
          WebSocket upgrade itself, so HMAC on WSS is not defined and
          always failed against real Twilio traffic before this
          two-stage protocol was in place.

        * Legacy HMAC signature (HTTP webhook / existing unit tests).
          Credentials WITHOUT `admission_verified="true"` fall through
          to the historic HMAC-SHA1 path, keeping every existing unit
          test in tests/unit/services/test_media_gateway.py green and
          keeping the primitive available for any future HTTP-signed
          transport.

        Sets ``_authenticated = True`` on success so connect() may proceed.
        """
        if credentials.get("admission_verified") == "true":
            expected = credentials.get("expected_account_sid", "")
            got = credentials.get("account_sid", "")
            if expected and got and expected != got:
                return AuthResult(
                    success=False,
                    reason=f"account_sid_mismatch: got {got!r}",
                )
            self._authenticated = True
            return AuthResult(success=True)

        result = self._authenticator.authenticate_twilio(credentials)
        if result.success:
            self._authenticated = True
        return result

    async def connect(self) -> None:
        """Mark the adapter ready to receive media.

        The actual WebSocket connection is managed by the server layer.
        This method validates that authentication was completed first (AR-2)
        and records the session start.

        Raises:
            RuntimeError: If called before a successful authenticate().
        """
        if not self._authenticated:
            raise RuntimeError(
                "connect() called before authenticate() succeeded — violates AR-2 (auth-before-allocation)"
            )
        self._connected = True

    def receive_frame(self) -> AsyncIterator[AudioFrame]:
        """Return an async iterator of decoded AudioFrames from the Twilio stream.

        Pulls messages from the internal queue (populated by put_message).
        Terminates when a 'stop' Twilio message is received or disconnect() is
        called (signalled by the None sentinel in the queue).

        Returns:
            AsyncIterator yielding one AudioFrame per Twilio 'media' message.
        """
        return self._frame_stream()

    async def send_frame(self, frame: AudioFrame) -> None:
        """Encode a PCM frame as a Twilio outbound media message.

        Encodes the frame payload as base64 and enqueues it as a Twilio
        'media' JSON message for the WebSocket server layer to transmit.

        Args:
            frame: PCM AudioFrame to transmit to the Twilio carrier.
        """
        payload = base64.b64encode(frame.pcm_data).decode()
        msg = json.dumps(
            {
                "event": "media",
                # Twilio requires streamSid on every outbound media message
                # (https://www.twilio.com/docs/voice/media-streams/websocket-messages#send-media-to-twilio) —
                # omitted here prior to this fix, which would have caused a
                # real Twilio carrier to reject every outbound playback
                # frame. Populated from the 'start' message _handle_start()
                # parses before any send_frame() call is possible in
                # practice (media only flows after a session has started).
                "streamSid": self._stream_sid or "",
                # Twilio Media Streams outbound expects ONLY {event, streamSid,
                # media.payload}. Sending extra fields (encoding, sampleRate,
                # channels) in the media object triggers Twilio error 31951
                # ("Stream Protocol Invalid message") and every outbound frame
                # is silently dropped — verified via Twilio Monitor alerts on
                # call CA3104d62bfb28af9d3fd9938515dbdc2a (2026-08-23).
                "media": {
                    "payload": payload,
                },
            }
        ).encode()
        await self._outbound_queue.put(msg)

    async def disconnect(self) -> None:
        """Signal the frame stream to terminate and mark adapter disconnected."""
        self._connected = False
        await self._message_queue.put(_STOP_SENTINEL)

    # ------------------------------------------------------------------
    # Twilio-specific public interface
    # ------------------------------------------------------------------

    def put_message(self, msg: dict[str, Any] | None) -> None:
        """Push a raw Twilio Media Streams message into the processing queue.

        Called by the WebSocket server layer for each JSON message received on
        the WebSocket.  Pass None to signal stream termination (same as
        calling disconnect()).

        This method is synchronous (uses put_nowait) so it can be called from
        any context without awaiting.
        """
        self._message_queue.put_nowait(msg)

    def drain_outbound(self) -> bytes | None:
        """Return one pending outbound message, or None if the queue is empty.

        Called by the WebSocket server layer to pull encoded audio for
        transmission to the Twilio carrier.
        """
        try:
            return self._outbound_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    @property
    def events(self) -> list[AudioSessionStarted]:
        """Domain events emitted during this session's lifecycle."""
        return list(self._events)

    @property
    def call_id(self) -> CallId | None:
        """CallId assigned to this session (available after 'start' message)."""
        return self._call_id

    @property
    def stream_sid(self) -> str | None:
        """Twilio StreamSid (available after 'start' message; required on
        every outbound media message per Twilio's Media Streams protocol)."""
        return self._stream_sid

    @property
    def is_connected(self) -> bool:
        """True between connect() and disconnect()."""
        return self._connected

    # ------------------------------------------------------------------
    # Internal message processing
    # ------------------------------------------------------------------

    async def _frame_stream(self) -> AsyncGenerator[AudioFrame, None]:
        """Async generator: pull messages from queue and yield AudioFrames."""
        while True:
            msg = await self._message_queue.get()

            if msg is None:
                # Sentinel: clean stream termination.
                return

            event_type = msg.get("event", "")

            if event_type == "connected":
                # Protocol acknowledgement — no media content.
                pass

            elif event_type == "start":
                self._handle_start(msg)

            elif event_type == "media":
                frame = self._handle_media(msg)
                if frame is not None:
                    yield frame

            elif event_type == "stop":
                # Carrier closed the stream; signal normal termination.
                return

    def _handle_start(self, msg: dict[str, Any]) -> None:
        """Process a Twilio 'start' message and emit AudioSessionStarted."""
        start = msg.get("start", {})
        self._stream_sid = start.get("streamSid", "")
        import logging as _lg
        _lg.getLogger("voiceos.twilio_ws").info("CALL_DIAG: stream_sid=%r callSid=%r", self._stream_sid, start.get("callSid", ""))

        # Use the Twilio CallSid as the CallId if none was pre-assigned.
        if self._call_id is None:
            self._call_id = CallId(start.get("callSid", self._stream_sid or "unknown"))

        # Extract caller phone from the customParameters or default to "unknown".
        custom = start.get("customParameters", {})
        self._caller_phone = custom.get("caller_phone", "unknown")

        media_format = start.get("mediaFormat", {})
        encoding = media_format.get("encoding", "audio/x-mulaw")
        sample_rate = int(media_format.get("sampleRate", 8000))
        channels = int(media_format.get("channels", 1))

        event = AudioSessionStarted(
            tenant_id=self._tenant_id,
            call_id=self._call_id,
            caller_phone=self._caller_phone,
            encoding=encoding,
            sample_rate=sample_rate,
            channels=channels,
        )
        self._events.append(event)

    def _handle_media(self, msg: dict[str, Any]) -> AudioFrame | None:
        """Decode a Twilio 'media' message into an AudioFrame."""
        media = msg.get("media", {})
        payload_b64 = media.get("payload", "")
        if not payload_b64:
            return None

        try:
            pcm_data = base64.b64decode(payload_b64)
        except Exception:
            return None

        if not pcm_data:
            return None

        # Parse chunk and timestamp from message.
        try:
            chunk = int(media.get("chunk", self._seq))
        except (TypeError, ValueError):
            chunk = self._seq

        try:
            timestamp_ms = int(media.get("timestamp", 0))
            rtp_ts = timestamp_ms * 8  # 8 kHz: 1 ms = 8 samples
        except (TypeError, ValueError):
            rtp_ts = self._rtp_ts

        frame = AudioFrame(
            pcm_data=pcm_data,
            seq=chunk,
            rtp_ts=rtp_ts,
            recv_ts=time.time(),
            config=_TWILIO_AUDIO_CONFIG,
        )

        self._seq = chunk + 1
        self._rtp_ts = rtp_ts + _RTP_TS_INCREMENT
        metrics.record_bytes_received(len(pcm_data), ADAPTER_TYPE_TWILIO)

        return frame
