"""ConversationStateIntelligence — deterministic call state machine.

Tracks the current ConversationState across turns and validates all
requested transitions against the allowed_transitions table. Invalid
transitions raise InvalidTransitionError immediately — the LLM never
decides state transitions (Law of Authority, RI-5; V2 Ch13).

Architecture: V2 Ch13 (Conversation State Intelligence).
"""

from __future__ import annotations

import logging

from prometheus_client import Counter

from .schema import ConversationState
from .transitions import ALLOWED_TRANSITIONS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_STATE_TRANSITIONS = Counter(
    "conversation_state_transitions_total",
    "Total conversation state transitions",
    ["from_state", "to_state"],
)

_INVALID_TRANSITIONS = Counter(
    "conversation_state_invalid_transitions_total",
    "Total invalid conversation state transition attempts",
    ["from_state", "to_state"],
)


class InvalidTransitionError(Exception):
    """Raised when a requested state transition is not in allowed_transitions.

    This exception is intentional and expected in production: it signals
    that the upstream decision engine proposed an illegal move. The
    Conversation Engine must catch this and fallback to a safe state.

    Architecture: V2 Ch13.
    """

    def __init__(
        self,
        from_state: ConversationState,
        to_state: ConversationState,
    ) -> None:
        allowed = sorted(s.value for s in ALLOWED_TRANSITIONS.get(from_state, frozenset()))
        super().__init__(
            f"Invalid transition: {from_state.value} → {to_state.value}. Allowed from {from_state.value}: {allowed}"
        )
        self.from_state = from_state
        self.to_state = to_state


class ConversationStateIntelligence:
    """Tracks and validates conversation state over the course of a call.

    Architecture: V2 Ch13.

    Usage:
        csi = ConversationStateIntelligence()
        # Initial state is always GREETING
        csi.transition(ConversationState.IDENTITY_VERIFICATION)  # OK
        csi.transition(ConversationState.NEGOTIATION)            # raises

    The state machine is per-call; create a new instance per call.
    """

    def __init__(
        self,
        initial_state: ConversationState = ConversationState.GREETING,
    ) -> None:
        self._state: ConversationState = initial_state

    @property
    def current_state(self) -> ConversationState:
        """The current conversation state."""
        return self._state

    def transition(self, to_state: ConversationState) -> ConversationState:
        """Attempt a state transition.

        Args:
            to_state: The requested next state.

        Returns:
            The new current state if the transition is allowed.

        Raises:
            InvalidTransitionError: If the transition is not in
                                    ALLOWED_TRANSITIONS for the current state.
        """
        allowed = ALLOWED_TRANSITIONS.get(self._state, frozenset())

        if to_state not in allowed:
            _INVALID_TRANSITIONS.labels(
                from_state=self._state.value,
                to_state=to_state.value,
            ).inc()
            logger.warning(
                "Invalid state transition rejected",
                extra={
                    "from_state": self._state.value,
                    "to_state": to_state.value,
                },
            )
            raise InvalidTransitionError(self._state, to_state)

        from_state = self._state
        self._state = to_state

        _STATE_TRANSITIONS.labels(
            from_state=from_state.value,
            to_state=to_state.value,
        ).inc()

        logger.debug(
            "State transition",
            extra={
                "from_state": from_state.value,
                "to_state": to_state.value,
            },
        )

        return self._state

    def can_transition(self, to_state: ConversationState) -> bool:
        """Return True if the requested transition is allowed from current state."""
        return to_state in ALLOWED_TRANSITIONS.get(self._state, frozenset())

    def allowed_next_states(self) -> frozenset[ConversationState]:
        """Return the set of states reachable from the current state."""
        return ALLOWED_TRANSITIONS.get(self._state, frozenset())
