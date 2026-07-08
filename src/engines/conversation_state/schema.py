"""ConversationState — the call state machine states.

Defines the 9 states of the conversation lifecycle for collections
and customer engagement calls. The state machine is deterministic:
transitions are governed by allowed_transitions (transitions.py) and
are never decided by the LLM.

Architecture: V2 Ch13 (Conversation State Intelligence).
"""

from __future__ import annotations

from enum import StrEnum


class ConversationState(StrEnum):
    """The 9 lifecycle states of a collections/engagement call.

    Architecture: V2 Ch13.
    """

    GREETING = "GREETING"
    """Initial state: agent identifies and greets the customer."""

    IDENTITY_VERIFICATION = "IDENTITY_VERIFICATION"
    """Customer identity is being verified before any account discussion."""

    DEBT_DISCUSSION = "DEBT_DISCUSSION"
    """Agent presents the outstanding amount; customer asks questions."""

    NEGOTIATION = "NEGOTIATION"
    """Active negotiation of payment amount and timeline."""

    COMMITMENT_CAPTURE = "COMMITMENT_CAPTURE"
    """Customer has agreed to pay; agent confirms the commitment details."""

    OBJECTION_HANDLING = "OBJECTION_HANDLING"
    """Customer raised an objection or dispute; agent is addressing it."""

    ESCALATION = "ESCALATION"
    """Call has been escalated to a supervisor or specialized team."""

    CLOSING = "CLOSING"
    """Call is wrapping up with summary and next-steps."""

    POST_CALL = "POST_CALL"
    """Terminal state: call has ended; post-call processing underway."""
