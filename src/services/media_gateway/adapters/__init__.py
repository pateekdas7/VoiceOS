"""Transport adapter implementations for the Media Gateway.

Each adapter implements the TransportAdapter protocol for a specific carrier:
    TwilioWebSocketAdapter  — Twilio Media Streams (WebSocket + mu-law)
    SIPRTPAdapter           — SIP signalling + RTP media (G.711 PCMU/PCMA, G.722)

Architecture: V1 Ch3 (Media Gateway transport adapters).
"""

from src.services.media_gateway.adapters.sip_rtp import SIPRTPAdapter, parse_rtp_packet, parse_sip_invite
from src.services.media_gateway.adapters.twilio_websocket import TwilioWebSocketAdapter

__all__ = [
    "SIPRTPAdapter",
    "TwilioWebSocketAdapter",
    "parse_rtp_packet",
    "parse_sip_invite",
]
