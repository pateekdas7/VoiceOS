"""Audio Session Manager — per-call media session management.

Receives raw audio frames from the Media Gateway, compensates for network
jitter and packet loss, maintains the session clock, and outputs a clean,
time-aligned audio stream for the Audio Preprocessing Pipeline.

Public API:
    AudioSessionManagerService  — registry + lifecycle manager
    AudioSession                — per-call state machine
    SessionState                — lifecycle state enumeration
    AdaptiveJitterBuffer        — reordering + overflow protection
    PacketLossConcealer         — G.711/G.722 PLC synthesis
    SessionClock                — RTP timestamp → wall-clock mapping

Architecture: V1 Ch4 (Audio Session Manager);
              DocSuite-02 (AudioSessionManager ↔ AudioPreprocessor interface).
"""

from __future__ import annotations

from src.services.audio_session_manager.clock import SessionClock
from src.services.audio_session_manager.jitter_buffer import AdaptiveJitterBuffer
from src.services.audio_session_manager.plc import PacketLossConcealer
from src.services.audio_session_manager.service import AudioSessionManagerService
from src.services.audio_session_manager.session import AudioSession, SessionState

__all__ = [
    "AdaptiveJitterBuffer",
    "AudioSession",
    "AudioSessionManagerService",
    "PacketLossConcealer",
    "SessionClock",
    "SessionState",
]
