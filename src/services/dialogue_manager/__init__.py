"""Dialogue Manager — turn state machine and TurnInput assembler.

Architecture: V1 Ch9 (Dialogue Manager).
"""

from __future__ import annotations

from .service import DialogueManager, DialogueState

__all__ = ["DialogueManager", "DialogueState"]
