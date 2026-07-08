"""Adaptive Prosody Engine package.

Maps EmpathyConfig (from EmpathyPlanner) to VoiceConfig (for TTS).

Architecture: V1 Ch20 (Adaptive Prosody Engine).
"""

from .engine import AdaptiveProsodyEngine

__all__ = ["AdaptiveProsodyEngine"]
