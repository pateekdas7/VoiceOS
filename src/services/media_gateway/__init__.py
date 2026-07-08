"""Media Gateway service — telephony ingress boundary of VoiceOS.

Receives inbound audio from telephony carriers (Twilio WebSocket, SIP/RTP)
and emits structured AudioFrame streams to the Audio Session Manager.

Auth-before-allocation (AR-2) is enforced: no session is allocated until the
transport connection has been authenticated by ConnectionAuthenticator.

Architecture: V1 Ch3 (Media Gateway); V6 Ch4 AR-2.

Public surface:
    MediaGatewayService  — lifecycle manager and adapter registry
    TransportAdapter     — abstract protocol for carrier adapters
    AuthResult           — authentication outcome value type
    SessionGate          — session admission and tracking
    TwilioWebSocketAdapter
    SIPRTPAdapter
"""

from src.services.media_gateway.protocol import AuthResult, SIPInviteParams, TransportAdapter
from src.services.media_gateway.service import MediaGatewayService
from src.services.media_gateway.session_gate import SessionGate

__all__ = [
    "AuthResult",
    "MediaGatewayService",
    "SIPInviteParams",
    "SessionGate",
    "TransportAdapter",
]
