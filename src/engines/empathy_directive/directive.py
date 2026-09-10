"""EmpathyState taxonomy and EmpathyDirective — Path-A Phase 6d types.

Architecture: V2 Ch14 (EmpathyPlanner); V1 Ch20 (AdaptiveProsodyEngine).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EmpathyState(str, Enum):
    """Fine-grained emotional states EmpathyDirectiveComposer distinguishes.

    Deliberately finer-grained than the coarse StressLevel/Sentiment pair
    src/engines/empathy/EmpathyPlanner consumes (V2 Ch14's EmpathyPlanner +
    AdaptiveProsodyEngine pair) — this is a lexical hardship taxonomy, not a
    stress-level bucket.
    """

    NEUTRAL = "NEUTRAL"
    HARDSHIP_FINANCIAL = "HARDSHIP_FINANCIAL"
    HARDSHIP_ILLNESS = "HARDSHIP_ILLNESS"
    HARDSHIP_JOB_LOSS = "HARDSHIP_JOB_LOSS"
    HARDSHIP_SALARY_DLY = "HARDSHIP_SALARY_DLY"
    HARDSHIP_FAMILY = "HARDSHIP_FAMILY"
    FRUSTRATION = "FRUSTRATION"
    ANXIETY = "ANXIETY"
    RESIGNATION = "RESIGNATION"
    ANGER = "ANGER"
    GRATITUDE = "GRATITUDE"
    RELIEF = "RELIEF"


@dataclass(frozen=True)
class EmpathyDirective:
    """Merge instructions produced for a single turn.

    Fields
    ------
    state : EmpathyState
        The classifier's verdict. Kept on the directive for logging/QA.
    acknowledgment : str
        Bilingual short phrase spoken before the scripted ask. Empty string
        when the state is NEUTRAL (no prefix injected).
    listening_break_ms : int
        Silence padded before the reply so it sounds like a breath was taken.
        ~0 for NEUTRAL, 250-500 on hardship/emotional states.
    rate_scale_delta : float
        Additive delta applied to the outgoing TTS rate scale (softer/slower
        on hardship). Clamped by the caller.
    energy_scale_delta : float
        Additive delta applied to the outgoing TTS energy scale (quieter on
        hardship).
    allow_close_on_ack : bool
        Routing hint: when the customer sends a bare ack after a
        confirm_date/plan ask, the response engine should treat it as a
        closing signal rather than re-anchoring the same ask.
    """

    state: EmpathyState = EmpathyState.NEUTRAL
    acknowledgment: str = ""
    listening_break_ms: int = 0
    rate_scale_delta: float = 0.0
    energy_scale_delta: float = 0.0
    allow_close_on_ack: bool = True


__all__ = ["EmpathyDirective", "EmpathyState"]
