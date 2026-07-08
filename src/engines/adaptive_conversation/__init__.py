"""Adaptive Conversation Engine — loop detection and silence recovery.

Architecture: V2 Ch1.
"""

from __future__ import annotations

from .engine import AdaptiveConversationEngine, AdaptiveSignal
from .loop_detector import ConversationLoopDetector
from .silence_handler import SilenceRecoveryPolicy

__all__ = [
    "AdaptiveConversationEngine",
    "AdaptiveSignal",
    "ConversationLoopDetector",
    "SilenceRecoveryPolicy",
]
