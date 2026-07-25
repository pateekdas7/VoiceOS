"""DialogueResponseEngine — deterministic scripted-response FSM for the
golden path, consuming real IntentEngine/EntityExtractor/NegotiationEngine
output instead of a second parallel parser.

Architecture: V2 Ch13 (dialogue state).
"""

from __future__ import annotations

from src.engines.dialogue_response.buckets import Bucket
from src.engines.dialogue_response.engine import DialogueResponseEngine, DialogueTurnOutput
from src.engines.dialogue_response.installment import InstallmentPlan, InstallmentPlanKind, compute_installment_plan
from src.engines.dialogue_response.session_protocol import DialogueSessionState

__all__ = [
    "Bucket",
    "DialogueResponseEngine",
    "DialogueSessionState",
    "DialogueTurnOutput",
    "InstallmentPlan",
    "InstallmentPlanKind",
    "compute_installment_plan",
]
