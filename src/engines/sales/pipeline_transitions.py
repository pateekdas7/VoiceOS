"""Pipeline transition records — LeadStage FSM validation.

Enforces valid forward transitions in the sales pipeline. Terminal states
(CONVERTED, DISQUALIFIED) cannot transition to any other stage. All backward
transitions (except intentional regression paths like NURTURING) are rejected.

Architecture: VoiceOS Phase 3 Production Action Layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import LeadStage


class InvalidTransitionError(Exception):
    """Raised when a LeadStage transition is not allowed by the pipeline FSM."""

    def __init__(
        self,
        from_stage: LeadStage,
        to_stage: LeadStage,
        reason: str = "",
    ) -> None:
        self.from_stage = from_stage
        self.to_stage = to_stage
        msg = f"Invalid pipeline transition: {from_stage.value} → {to_stage.value}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


@dataclass(frozen=True)
class PipelineTransition:
    """Record of a validated LeadStage transition."""

    from_stage: LeadStage
    to_stage: LeadStage
    reason: str
    trigger: str  # "SalesStateUpdater" or "SalesActionPlanner"


class PipelineTransitionEngine:
    """Detects and validates LeadStage transitions.

    All transitions must be explicitly registered in _ALLOWED. Terminal states
    (CONVERTED, DISQUALIFIED) raise InvalidTransitionError on any transition
    attempt. Same-stage 'transitions' return None (no event).

    Architecture: VoiceOS Phase 3 Production Action Layer.
    """

    # Allowed forward (and intentional backward) transitions.
    # Terminal states map to empty frozensets.
    _ALLOWED: dict[LeadStage, frozenset[LeadStage]] = {
        LeadStage.NEW: frozenset({LeadStage.ENGAGED, LeadStage.DISQUALIFIED}),
        LeadStage.ENGAGED: frozenset({
            LeadStage.QUALIFYING,
            LeadStage.NURTURING,
            LeadStage.DISQUALIFIED,
        }),
        LeadStage.QUALIFYING: frozenset({
            LeadStage.QUALIFIED,
            LeadStage.NURTURING,
            LeadStage.DISQUALIFIED,
        }),
        LeadStage.QUALIFIED: frozenset({
            LeadStage.SITE_VISIT_SCHEDULED,
            LeadStage.NEGOTIATING,
            LeadStage.NURTURING,
            LeadStage.DISQUALIFIED,
        }),
        LeadStage.SITE_VISIT_SCHEDULED: frozenset({
            LeadStage.NEGOTIATING,
            LeadStage.NURTURING,
            LeadStage.CONVERTED,
            LeadStage.DISQUALIFIED,
        }),
        LeadStage.NEGOTIATING: frozenset({
            LeadStage.CONVERTED,
            LeadStage.NURTURING,
            LeadStage.DISQUALIFIED,
        }),
        LeadStage.NURTURING: frozenset({
            LeadStage.QUALIFYING,
            LeadStage.ENGAGED,
            LeadStage.DISQUALIFIED,
        }),
        LeadStage.CONVERTED: frozenset(),   # terminal
        LeadStage.DISQUALIFIED: frozenset(),  # terminal
    }

    @classmethod
    def evaluate(
        cls,
        previous: LeadStage,
        current: LeadStage,
        reason: str,
        trigger: str = "SalesStateUpdater",
    ) -> PipelineTransition | None:
        """Return a PipelineTransition if stage changed and transition is valid.

        Returns:
            PipelineTransition if ``previous != current`` and the transition is
            in the allowed set.
            None if ``previous == current`` (no transition occurred).

        Raises:
            InvalidTransitionError: If the transition is not allowed.
        """
        if previous == current:
            return None  # no transition

        allowed = cls._ALLOWED.get(previous, frozenset())
        if current not in allowed:
            raise InvalidTransitionError(previous, current, reason)

        return PipelineTransition(
            from_stage=previous,
            to_stage=current,
            reason=reason,
            trigger=trigger,
        )
