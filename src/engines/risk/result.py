"""RiskAssessment — output type of the RiskEngine.

Architecture: V2 Ch6 (Risk Engine).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .flags import RiskFlag


class RiskAssessment(BaseModel):
    """Result of a RiskEngine evaluation for one turn.

    Aggregates active risk flags and derives actionable escalation decisions.
    A RiskAssessment with ``human_handoff_required=True`` must be routed to
    a human agent before any further automated dialogue proceeds.

    Architecture: V2 Ch6.
    """

    model_config = ConfigDict(frozen=True)

    flags: list[RiskFlag]
    """All risk flags active in this turn. May be empty (no risk detected)."""

    escalation_required: bool
    """True when at least one flag warrants escalation to a supervisor."""

    human_handoff_required: bool
    """True when ABUSE_DETECTED is raised — agent must immediately hand off to a human."""
