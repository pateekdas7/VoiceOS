"""TransportAdapter protocol — the carrier-agnostic abstraction for the Media Gateway.

Defines the abstract contract that every transport adapter (Twilio WebSocket,
SIP/RTP, future carriers) must satisfy. The MediaGatewayService depends only
on this interface — it is never coupled to a specific adapter implementation.

Architecture: V1 Ch3 (Media Gateway — TransportAdapter protocol);
              V6 Ch4 (AR-2: auth-before-allocation).
"""

from __future__ import annotations

import socket
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field

from src.libs.contracts.audio import AudioFrame

# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthResult:
    """The outcome of a transport authentication attempt.

    Architecture: V1 Ch3.3 (auth-before-allocation); V6 Ch4 AR-2.
    """

    success: bool
    """True when the carrier credentials are valid and the session may proceed."""

    reason: str | None = None
    """Human-readable explanation.  Present when success=False; None on success."""


@dataclass(frozen=True)
class SIPInviteParams:
    """Parsed fields extracted from a SIP INVITE request.

    Produced by SIPRTPAdapter.process_invite() and consumed by authenticate()
    and connect() to open the RTP media socket on the negotiated port.

    Architecture: V1 Ch3.9 (SIP/RTP ingress).
    """

    from_header: str
    """Raw SIP From header value (e.g. '<sip:+919876543210@carrier.example>')."""

    call_id: str
    """SIP Call-ID header — uniquely identifies this call across all SIP messages."""

    contact: str
    """SIP Contact header — carrier's address for response routing."""

    rtp_port: int
    """UDP port from the SDP m=audio line where carrier will send RTP media."""

    codec: str = "PCMU"
    """Negotiated codec from SDP (default PCMU = G.711 mu-law)."""


@dataclass
class AdmittedSession:
    """A call session that has passed authentication and been allocated resources.

    Tracked by SessionGate; released on disconnect or timeout.

    Architecture: V1 Ch3.5 (SessionGate); V6 Ch4 AR-2.
    """

    call_id: str
    """Unique call identifier."""

    tenant_id: str
    """Owning tenant (AR-8 — every resource is tenant-scoped)."""

    adapter_type: str
    """Adapter that owns this session: 'twilio' | 'sip_rtp'."""

    admitted_at: float
    """Wall-clock epoch seconds when the session was admitted."""

    rtp_port: int = 0
    """RTP port allocated for the session (SIP/RTP sessions only)."""

    socket: socket.socket | None = field(default=None, repr=False)
    """UDP socket bound to rtp_port (SIP/RTP sessions only). Not repr'd for safety."""


# ---------------------------------------------------------------------------
# TransportAdapter abstract base class
# ---------------------------------------------------------------------------


class TransportAdapter(ABC):
    """Abstract base for all Media Gateway transport adapters.

    Every carrier integration (Twilio WebSocket, SIP/RTP, future carriers)
    implements this interface. The MediaGatewayService depends only on this
    abstract contract — it never imports concrete adapter classes.

    Lifecycle contract (enforced by SessionGate / AR-2):
        1. authenticate() — credentials validated BEFORE any resource allocation.
        2. connect()      — opens transport ONLY after AuthResult.success is True.
        3. receive_frame() / send_frame() — bidirectional media exchange.
        4. disconnect()   — orderly teardown; releases socket / WebSocket connection.

    Architecture: V1 Ch3 (Media Gateway); V6 Ch4 AR-2 (auth-before-allocation).
    """

    @abstractmethod
    async def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        """Validate carrier credentials before any resource allocation.

        Must complete before connect() is called. The SessionGate enforces this
        ordering — it will not call connect() unless AuthResult.success is True.

        Args:
            credentials: Carrier-specific credential dict.
                Twilio: account_sid, auth_token, url, params (JSON str), x_twilio_signature.
                SIP:    expected_from_prefix (allowed caller pattern).

        Returns:
            AuthResult with success=True on valid credentials.
        """

    @abstractmethod
    async def connect(self) -> None:
        """Open the transport connection after successful authentication.

        For TwilioWebSocketAdapter: marks the session ready for media streaming.
        For SIPRTPAdapter: binds the UDP socket on the negotiated RTP port.
        Emits AudioSessionStarted domain event.

        Raises:
            RuntimeError: If called before a successful authenticate().
        """

    @abstractmethod
    def receive_frame(self) -> AsyncIterator[AudioFrame]:
        """Return an async iterator that yields inbound AudioFrames.

        The iterator runs until disconnect() is called or the carrier closes
        the connection. Frames are delivered in arrival order; the jitter buffer
        in AudioSessionManager (Sprint-005) handles reordering.

        Callers:
            async for frame in adapter.receive_frame():
                process(frame)
        """

    @abstractmethod
    async def send_frame(self, frame: AudioFrame) -> None:
        """Push an outbound AudioFrame to the carrier.

        Used for agent TTS audio (Sprint-017+). The frame must be encoded in
        the wire format expected by the carrier (mu-law for Twilio/G.711).

        Args:
            frame: PCM audio frame to encode and transmit.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Orderly teardown — close socket/WebSocket, release resources."""


# ---------------------------------------------------------------------------
# Adapter type registry
# ---------------------------------------------------------------------------

ADAPTER_TYPE_TWILIO = "twilio"
ADAPTER_TYPE_SIP_RTP = "sip_rtp"
"""Stable adapter type identifiers used in metrics labels and log fields."""


def _make_async_gen(
    adapter: TransportAdapter,
) -> AsyncGenerator[AudioFrame, None]:
    """Type-helper: call receive_frame() and return as AsyncGenerator."""
    return adapter.receive_frame()  # type: ignore[return-value]
