"""Allowed conversation state transitions.

The transition table is the single authoritative source of truth for
which state transitions are permitted. The ConversationStateIntelligence
engine enforces these transitions strictly — no transition outside this
table is ever allowed, regardless of LLM output (Law of Authority, RI-5;
deterministic control, V2 Ch13).

Transition design principles:
  - Forward progression is preferred (Greeting → Identity → Debt → Negotiation)
  - Lateral moves are allowed (e.g., Debt ↔ Objection, Negotiation ↔ Objection)
  - Escalation is reachable from most states
  - POST_CALL is a terminal state (no outgoing transitions)
  - CLOSING must precede POST_CALL

Architecture: V2 Ch13 (Conversation State Intelligence — deterministic transitions).
"""

from __future__ import annotations

from .schema import ConversationState

# ---------------------------------------------------------------------------
# Allowed transitions: {from_state: frozenset of allowed to_states}
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    ConversationState.GREETING: frozenset(
        {
            ConversationState.IDENTITY_VERIFICATION,
            ConversationState.CLOSING,  # customer immediately disconnects
            ConversationState.ESCALATION,  # immediate escalation request
        }
    ),
    ConversationState.IDENTITY_VERIFICATION: frozenset(
        {
            ConversationState.DEBT_DISCUSSION,
            ConversationState.CLOSING,  # customer refuses to verify
            ConversationState.ESCALATION,
        }
    ),
    ConversationState.DEBT_DISCUSSION: frozenset(
        {
            ConversationState.NEGOTIATION,
            ConversationState.OBJECTION_HANDLING,
            ConversationState.COMMITMENT_CAPTURE,
            ConversationState.ESCALATION,
            ConversationState.CLOSING,
        }
    ),
    ConversationState.NEGOTIATION: frozenset(
        {
            ConversationState.COMMITMENT_CAPTURE,
            ConversationState.OBJECTION_HANDLING,
            ConversationState.DEBT_DISCUSSION,  # customer wants to revisit
            ConversationState.ESCALATION,
            ConversationState.CLOSING,
        }
    ),
    ConversationState.COMMITMENT_CAPTURE: frozenset(
        {
            ConversationState.CLOSING,
            ConversationState.NEGOTIATION,  # customer retracts commitment
            ConversationState.ESCALATION,
        }
    ),
    ConversationState.OBJECTION_HANDLING: frozenset(
        {
            ConversationState.DEBT_DISCUSSION,
            ConversationState.NEGOTIATION,
            ConversationState.COMMITMENT_CAPTURE,
            ConversationState.ESCALATION,
            ConversationState.CLOSING,
        }
    ),
    ConversationState.ESCALATION: frozenset(
        {
            ConversationState.CLOSING,
            ConversationState.DEBT_DISCUSSION,  # de-escalated
            ConversationState.NEGOTIATION,  # de-escalated
        }
    ),
    ConversationState.CLOSING: frozenset(
        {
            ConversationState.POST_CALL,
        }
    ),
    ConversationState.POST_CALL: frozenset(),  # terminal — no outgoing transitions
}
