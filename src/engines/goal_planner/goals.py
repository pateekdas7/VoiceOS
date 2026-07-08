"""Goal types for the GoalPlanner.

Each Goal represents the agent's primary objective for the current turn.
The GoalPlanner selects exactly one goal per turn, constrained by the
CustomerContext (DPD, outstanding) and campaign settings.

Architecture: V2 Ch7 (Goal Planner).
"""

from __future__ import annotations

from enum import StrEnum


class Goal(StrEnum):
    """Agent goals available to the GoalPlanner.

    Goals are selected deterministically from CustomerContext data.
    The GoalPlanner always returns exactly one primary Goal per turn.

    Architecture: V2 Ch7; DocSuite-03 (Data Dictionary).
    """

    COLLECT_FULL_PAYMENT = "COLLECT_FULL_PAYMENT"
    """Objective: collect the entire outstanding balance this call."""

    COLLECT_PARTIAL_PAYMENT = "COLLECT_PARTIAL_PAYMENT"
    """Objective: collect a meaningful partial payment toward the outstanding."""

    SECURE_PTP = "SECURE_PTP"
    """Objective: obtain a credible, time-bound promise-to-pay from the customer."""

    VERIFY_IDENTITY = "VERIFY_IDENTITY"
    """Objective: confirm the customer's identity before proceeding."""

    HANDLE_DISPUTE = "HANDLE_DISPUTE"
    """Objective: acknowledge and triage the customer's account dispute."""

    DE_ESCALATE = "DE_ESCALATE"
    """Objective: reduce customer distress and restore a productive dialogue."""

    END_CALL = "END_CALL"
    """Objective: close the call safely and compliantly."""

    TRANSFER_AGENT = "TRANSFER_AGENT"
    """Objective: transfer the call to a human agent."""
