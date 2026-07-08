"""EmpathyPlanner — tone, pacing, and language adaptation for VoiceOS.

Architecture: V2 Ch14.
"""

from .engine import EmpathyPlanner
from .labels import EmpathyConfig, LanguageRegister, Pacing, Tone

__all__ = ["EmpathyConfig", "EmpathyPlanner", "LanguageRegister", "Pacing", "Tone"]
