"""SIP/RTP transport adapter for the Media Gateway.

Handles inbound audio from SIP carriers using G.711 (PCMU/PCMA) or G.722.
The adapter:
  1. Parses a SIP INVITE message to extract signalling and SDP media parameters.
  2. Authenticates the caller's From header against an allow-list.
  3. Binds a UDP socket on the negotiated RTP port to receive media.
  4. Reads raw RTP packets, strips the 12-byte RTP header, and emits AudioFrames.

RTP header layout (RFC 3550, fixed part):
  0               1               2               3
  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |V=2|P|X|  CC   |M|     PT      |       sequence number         |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |                           timestamp                           |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |           synchronisation source (SSRC) identifier           |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

Payload types (RFC 3551):
  0  = PCMU (G.711 mu-law)
  8  = PCMA (G.711 A-law)
  9  = G.722

Architecture: V1 Ch3.9 (SIP/RTP ingress); V6 Ch4 AR-2.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.libs.contracts.events.audio_events import AudioSessionStarted
from src.libs.contracts.primitives import CallId, TenantId
from src.services.media_gateway import metrics
from src.services.media_gateway.auth import ConnectionAuthenticator
from src.services.media_gateway.protocol import (
    ADAPTER_TYPE_SIP_RTP,
    AuthResult,
    SIPInviteParams,
    TransportAdapter,
)

# RTP fixed header length in bytes (RFC 3550 §5.1).
_RTP_HEADER_LEN: int = 12

# Map from RTP payload type to VoiceOS Encoding.
_PT_TO_ENCODING: dict[int, Encoding] = {
    0: Encoding.MULAW,  # PCMU — G.711 mu-law
    8: Encoding.ALAW,  # PCMA — G.711 A-law
}

# Map from RTP payload type to SampleRate.
_PT_TO_SAMPLE_RATE: dict[int, SampleRate] = {
    0: SampleRate.RATE_8K,
    8: SampleRate.RATE_8K,
    9: SampleRate.RATE_8K,  # G.722 nominally 8 kHz RTP clock
}

# Max UDP datagram size for RTP (practical limit; RFC 3550 allows up to 65535).
_MAX_RTP_DATAGRAM: int = 4096


# ---------------------------------------------------------------------------
# SIP INVITE parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SIPHeaders:
    """Raw headers extracted from a SIP message."""

    from_header: str
    to_header: str
    call_id: str
    contact: str
    content_type: str


def parse_sip_invite(sip_message: str) -> SIPInviteParams:
    """Parse a SIP INVITE message and return its negotiated parameters.

    Parses the request line, headers, and SDP body using simple text processing
    (no external SIP library required for Sprint-004).

    Args:
        sip_message: Raw SIP INVITE text (CRLF or LF line endings).

    Returns:
        SIPInviteParams with From, Call-ID, Contact, RTP port, and codec.

    Raises:
        ValueError: If the message is not a valid SIP INVITE.
    """
    if not sip_message.strip():
        raise ValueError("Empty SIP message")

    # Normalise line endings (SIP uses CRLF; allow bare LF for test convenience).
    normalised = sip_message.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalised.split("\n")

    first_line = lines[0].strip()
    if not first_line.upper().startswith("INVITE "):
        raise ValueError(f"Not a SIP INVITE: {first_line!r}")

    headers: dict[str, str] = {}
    sdp_body_lines: list[str] = []
    in_body = False

    for line in lines[1:]:
        if not in_body and line == "":
            in_body = True
            continue
        if in_body:
            sdp_body_lines.append(line)
        else:
            if ": " in line:
                key, _, value = line.partition(": ")
                headers[key.lower().strip()] = value.strip()

    from_header = headers.get("from", "")
    call_id = headers.get("call-id", "")
    contact = headers.get("contact", "")

    # Parse SDP body for RTP port and codec.
    rtp_port = 0
    codec = "PCMU"
    for sdp_line in sdp_body_lines:
        stripped = sdp_line.strip()
        if stripped.startswith("m=audio "):
            # e.g. "m=audio 12345 RTP/AVP 0 8"
            parts = stripped.split()
            if len(parts) >= 2:
                try:
                    rtp_port = int(parts[1])
                except ValueError:
                    pass
            # First payload type determines the primary codec.
            if len(parts) >= 4:
                try:
                    pt = int(parts[3])
                    codec = "PCMU" if pt == 0 else "PCMA" if pt == 8 else "G722"
                except ValueError:
                    pass

    return SIPInviteParams(
        from_header=from_header,
        call_id=call_id,
        contact=contact,
        rtp_port=rtp_port,
        codec=codec,
    )


# ---------------------------------------------------------------------------
# RTP packet parser
# ---------------------------------------------------------------------------


def parse_rtp_packet(data: bytes, config: AudioConfig) -> AudioFrame | None:
    """Strip the RTP header and wrap the payload in an AudioFrame.

    Args:
        data:   Raw UDP datagram bytes.
        config: AudioConfig derived from SDP negotiation.

    Returns:
        AudioFrame, or None if the packet is too short or has an unsupported PT.
    """
    if len(data) < _RTP_HEADER_LEN:
        return None

    # Unpack the fixed 12-byte RTP header.
    first_word, seq, timestamp = struct.unpack("!HHI", data[:8])
    # Bits 0-1 = version (must be 2), bits 9-15 = payload type.
    version = (first_word >> 14) & 0x3
    payload_type = first_word & 0x7F

    if version != 2:
        return None

    if payload_type not in _PT_TO_ENCODING:
        # Unsupported codec — drop the packet.
        return None

    # Account for CSRC list after fixed header (CC field in bits 4-7 of word 0).
    cc = (first_word >> 8) & 0xF
    header_len = _RTP_HEADER_LEN + cc * 4
    if len(data) <= header_len:
        return None

    pcm_data = data[header_len:]

    return AudioFrame(
        pcm_data=pcm_data,
        seq=seq,
        rtp_ts=timestamp,
        recv_ts=time.time(),
        config=config,
    )


# ---------------------------------------------------------------------------
# SIPRTPAdapter
# ---------------------------------------------------------------------------


class SIPRTPAdapter(TransportAdapter):
    """Adapter for SIP/RTP inbound audio.

    The SIP signalling layer (SIP registrar / B2BUA — future sprints) calls
    ``process_invite()`` with the raw SIP INVITE text, then calls
    ``authenticate()`` and ``connect()`` to open the RTP media socket.

    Usage::

        adapter = SIPRTPAdapter(tenant_id=TenantId("t1"))
        params = adapter.process_invite(raw_invite_text)
        auth = await adapter.authenticate({"from_header": params.from_header,
                                           "allowed_from_prefix": "sip:+91"})
        if auth.success:
            await adapter.connect()
            async for frame in adapter.receive_frame():
                ...

    Architecture: V1 Ch3.9 (SIP/RTP ingress); V6 Ch4 AR-2.
    """

    def __init__(
        self,
        tenant_id: TenantId,
        *,
        call_id: CallId | None = None,
    ) -> None:
        """
        Args:
            tenant_id: Owning tenant (AR-8).
            call_id:   Optional pre-assigned CallId (derived from SIP Call-ID if absent).
        """
        self._tenant_id = tenant_id
        self._call_id: CallId | None = call_id

        self._authenticator = ConnectionAuthenticator()
        self._authenticated = False
        self._connected = False
        self._stopped = False

        self._invite_params: SIPInviteParams | None = None
        self._audio_config: AudioConfig | None = None
        self._rtp_socket: socket.socket | None = None

        self._events: list[AudioSessionStarted] = []

    # ------------------------------------------------------------------
    # SIP-specific public interface
    # ------------------------------------------------------------------

    def process_invite(self, sip_message: str) -> SIPInviteParams:
        """Parse a raw SIP INVITE and store the negotiated parameters.

        Must be called before authenticate() and connect().

        Args:
            sip_message: Full SIP INVITE text (headers + SDP body).

        Returns:
            The parsed SIPInviteParams (also stored on self._invite_params).

        Raises:
            ValueError: If the message is not a valid SIP INVITE.
        """
        params = parse_sip_invite(sip_message)
        self._invite_params = params

        if self._call_id is None:
            self._call_id = CallId(params.call_id or "unknown")

        # Build AudioConfig from SDP codec negotiation.
        encoding = Encoding.MULAW if params.codec in ("PCMU", "MULAW") else Encoding.ALAW
        self._audio_config = AudioConfig(
            sample_rate=SampleRate.RATE_8K,
            encoding=encoding,
            channels=1,
            frame_duration_ms=20,
        )

        return params

    # ------------------------------------------------------------------
    # TransportAdapter protocol
    # ------------------------------------------------------------------

    async def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        """Validate the SIP From header against the caller allow-list.

        Args:
            credentials: Must contain from_header (the SIP From value) and
                         allowed_from_prefix (empty = accept all).

        Returns:
            AuthResult with success=True when the From header is allowed.
        """
        result = self._authenticator.authenticate_sip(credentials)
        if result.success:
            self._authenticated = True
        return result

    async def connect(self) -> None:
        """Bind a UDP socket on the negotiated RTP port.

        Parses and stores the invited RTP port from the SDP.  Sets
        ``_connected = True`` and emits AudioSessionStarted.

        Raises:
            RuntimeError: If called before authenticate() or process_invite().
        """
        if not self._authenticated:
            raise RuntimeError(
                "connect() called before authenticate() succeeded — violates AR-2 (auth-before-allocation)"
            )
        if self._invite_params is None:
            raise RuntimeError("process_invite() must be called before connect()")

        # Bind a non-blocking UDP socket on the negotiated RTP port.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("0.0.0.0", self._invite_params.rtp_port))

        # If port 0 was used (OS assigns), update invite_params with actual port.
        actual_port = sock.getsockname()[1]
        if self._invite_params.rtp_port == 0:
            self._invite_params = SIPInviteParams(
                from_header=self._invite_params.from_header,
                call_id=self._invite_params.call_id,
                contact=self._invite_params.contact,
                rtp_port=actual_port,
                codec=self._invite_params.codec,
            )

        self._rtp_socket = sock
        self._connected = True

        # Emit session-started event.
        event = AudioSessionStarted(
            tenant_id=self._tenant_id,
            call_id=self._call_id or CallId("unknown"),
            caller_phone=self._invite_params.from_header,
            encoding=self._invite_params.codec.lower(),
            sample_rate=SampleRate.RATE_8K.value,
            channels=1,
        )
        self._events.append(event)

    def receive_frame(self) -> AsyncIterator[AudioFrame]:
        """Return an async iterator of AudioFrames read from the RTP socket."""
        return self._rtp_frame_stream()

    async def send_frame(self, frame: AudioFrame) -> None:
        """Send an outbound AudioFrame as a raw RTP packet (stub — Sprint-017+).

        Outbound RTP transmission (agent TTS audio) requires SSRC negotiation
        and a target address from the SIP dialog, which are established in
        later sprints.  This method is a no-op in Sprint-004.
        """
        # Outbound RTP transmission is implemented in Sprint-017.
        # The method signature is required by the TransportAdapter protocol.

    async def disconnect(self) -> None:
        """Close the RTP socket and terminate the frame stream."""
        self._stopped = True
        self._connected = False
        if self._rtp_socket is not None:
            try:
                self._rtp_socket.close()
            except OSError:
                pass
            self._rtp_socket = None
        if self._invite_params is not None:
            metrics.record_session_released(adapter_type=ADAPTER_TYPE_SIP_RTP)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def invite_params(self) -> SIPInviteParams | None:
        """The parsed SIP INVITE parameters (available after process_invite())."""
        return self._invite_params

    @property
    def rtp_port(self) -> int:
        """The actual RTP port bound by connect() (0 if not yet connected)."""
        if self._rtp_socket is not None:
            try:
                return int(self._rtp_socket.getsockname()[1])
            except OSError:
                return 0
        return 0

    @property
    def is_connected(self) -> bool:
        """True between connect() and disconnect()."""
        return self._connected

    @property
    def events(self) -> list[AudioSessionStarted]:
        """Domain events emitted during this session's lifecycle."""
        return list(self._events)

    @property
    def call_id(self) -> CallId | None:
        """The CallId assigned to this session."""
        return self._call_id

    # ------------------------------------------------------------------
    # Internal RTP stream
    # ------------------------------------------------------------------

    async def _rtp_frame_stream(self) -> AsyncGenerator[AudioFrame, None]:
        """Async generator: read UDP datagrams from the RTP socket, yield frames."""
        if self._rtp_socket is None:
            raise RuntimeError("Not connected — call connect() first")

        config = self._audio_config or AudioConfig(
            sample_rate=SampleRate.RATE_8K,
            encoding=Encoding.MULAW,
            channels=1,
            frame_duration_ms=20,
        )

        loop = asyncio.get_event_loop()
        while not self._stopped:
            try:
                data = await loop.sock_recv(self._rtp_socket, _MAX_RTP_DATAGRAM)
            except OSError:
                # Socket was closed (disconnect called) or read error.
                break

            frame = parse_rtp_packet(data, config)
            if frame is None:
                continue

            metrics.record_bytes_received(len(frame.pcm_data), ADAPTER_TYPE_SIP_RTP)
            yield frame
