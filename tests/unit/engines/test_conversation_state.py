"""Unit tests for ConversationStateIntelligence.

Required named tests (per Sprint-010 spec):
  - test_conversation_state_valid_transition
  - test_conversation_state_invalid_transition

Architecture: V2 Ch13; DocSuite-08.
"""

from __future__ import annotations

import pytest

from src.engines.conversation_state.engine import (
    ConversationStateIntelligence,
    InvalidTransitionError,
)
from src.engines.conversation_state.schema import ConversationState
from src.engines.conversation_state.transitions import ALLOWED_TRANSITIONS

# ---------------------------------------------------------------------------
# Required named tests
# ---------------------------------------------------------------------------


def test_conversation_state_valid_transition() -> None:
    """AC-7: GREETING → IDENTITY_VERIFICATION is an allowed transition."""
    csi = ConversationStateIntelligence()
    assert csi.current_state == ConversationState.GREETING

    new_state = csi.transition(ConversationState.IDENTITY_VERIFICATION)
    assert new_state == ConversationState.IDENTITY_VERIFICATION
    current_after_transition: ConversationState = csi.current_state
    assert current_after_transition == ConversationState.IDENTITY_VERIFICATION


def test_conversation_state_invalid_transition() -> None:
    """AC-7: GREETING → NEGOTIATION is not allowed; must raise InvalidTransitionError."""
    csi = ConversationStateIntelligence()
    assert csi.current_state == ConversationState.GREETING

    with pytest.raises(InvalidTransitionError) as exc_info:
        csi.transition(ConversationState.NEGOTIATION)

    err = exc_info.value
    assert err.from_state == ConversationState.GREETING
    assert err.to_state == ConversationState.NEGOTIATION
    # State must remain unchanged after failed transition
    assert csi.current_state == ConversationState.GREETING


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


def test_initial_state_is_greeting() -> None:
    """Default initial state is GREETING."""
    csi = ConversationStateIntelligence()
    assert csi.current_state == ConversationState.GREETING


def test_custom_initial_state() -> None:
    """ConversationStateIntelligence accepts a custom initial state."""
    csi = ConversationStateIntelligence(initial_state=ConversationState.DEBT_DISCUSSION)
    assert csi.current_state == ConversationState.DEBT_DISCUSSION


def test_full_happy_path() -> None:
    """Happy-path call: GREETING → IDENTITY → DEBT → NEGOTIATION → COMMITMENT → CLOSING → POST_CALL."""
    csi = ConversationStateIntelligence()
    path = [
        ConversationState.IDENTITY_VERIFICATION,
        ConversationState.DEBT_DISCUSSION,
        ConversationState.NEGOTIATION,
        ConversationState.COMMITMENT_CAPTURE,
        ConversationState.CLOSING,
        ConversationState.POST_CALL,
    ]
    for state in path:
        csi.transition(state)
    assert csi.current_state == ConversationState.POST_CALL


def test_post_call_is_terminal() -> None:
    """POST_CALL is terminal — all transitions raise InvalidTransitionError."""
    csi = ConversationStateIntelligence(initial_state=ConversationState.POST_CALL)
    for state in ConversationState:
        with pytest.raises(InvalidTransitionError):
            csi.transition(state)


def test_escalation_from_debt_discussion() -> None:
    """DEBT_DISCUSSION → ESCALATION is allowed."""
    csi = ConversationStateIntelligence(initial_state=ConversationState.DEBT_DISCUSSION)
    csi.transition(ConversationState.ESCALATION)
    assert csi.current_state == ConversationState.ESCALATION


def test_escalation_to_closing() -> None:
    """ESCALATION → CLOSING is allowed."""
    csi = ConversationStateIntelligence(initial_state=ConversationState.ESCALATION)
    csi.transition(ConversationState.CLOSING)
    assert csi.current_state == ConversationState.CLOSING


def test_objection_handling_lateral_moves() -> None:
    """OBJECTION_HANDLING can go to DEBT_DISCUSSION, NEGOTIATION, CLOSING, ESCALATION."""
    for to_state in [
        ConversationState.DEBT_DISCUSSION,
        ConversationState.NEGOTIATION,
        ConversationState.COMMITMENT_CAPTURE,
        ConversationState.ESCALATION,
        ConversationState.CLOSING,
    ]:
        csi = ConversationStateIntelligence(initial_state=ConversationState.OBJECTION_HANDLING)
        csi.transition(to_state)
        assert csi.current_state == to_state


def test_can_transition_true_for_allowed() -> None:
    """can_transition returns True for allowed transitions."""
    csi = ConversationStateIntelligence()
    assert csi.can_transition(ConversationState.IDENTITY_VERIFICATION) is True


def test_can_transition_false_for_disallowed() -> None:
    """can_transition returns False for invalid transitions."""
    csi = ConversationStateIntelligence()
    assert csi.can_transition(ConversationState.NEGOTIATION) is False


def test_allowed_next_states_from_greeting() -> None:
    """allowed_next_states returns correct frozenset from GREETING."""
    csi = ConversationStateIntelligence()
    allowed = csi.allowed_next_states()
    assert ConversationState.IDENTITY_VERIFICATION in allowed
    assert ConversationState.NEGOTIATION not in allowed


def test_invalid_transition_error_message() -> None:
    """InvalidTransitionError message identifies from/to states."""
    csi = ConversationStateIntelligence()
    try:
        csi.transition(ConversationState.COMMITMENT_CAPTURE)
    except InvalidTransitionError as e:
        assert "GREETING" in str(e)
        assert "COMMITMENT_CAPTURE" in str(e)


def test_invalid_transition_does_not_change_state() -> None:
    """State remains unchanged after an invalid transition attempt."""
    csi = ConversationStateIntelligence()
    try:
        csi.transition(ConversationState.POST_CALL)
    except InvalidTransitionError:
        pass
    assert csi.current_state == ConversationState.GREETING


def test_allowed_transitions_completeness() -> None:
    """Every ConversationState has an entry in ALLOWED_TRANSITIONS."""
    for state in ConversationState:
        assert state in ALLOWED_TRANSITIONS, f"{state} missing from ALLOWED_TRANSITIONS"


def test_post_call_has_empty_transitions() -> None:
    """POST_CALL must have an empty allowed transition set."""
    assert ALLOWED_TRANSITIONS[ConversationState.POST_CALL] == frozenset()


def test_conversation_state_enum_has_9_members() -> None:
    """ConversationState must define exactly 9 states (V2 Ch13)."""
    assert len(list(ConversationState)) == 9


def test_greeting_to_closing_direct() -> None:
    """GREETING → CLOSING is allowed (immediate call end)."""
    csi = ConversationStateIntelligence()
    csi.transition(ConversationState.CLOSING)
    assert csi.current_state == ConversationState.CLOSING


def test_commitment_capture_to_negotiation_retraction() -> None:
    """COMMITMENT_CAPTURE → NEGOTIATION is allowed (customer retracts)."""
    csi = ConversationStateIntelligence(initial_state=ConversationState.COMMITMENT_CAPTURE)
    csi.transition(ConversationState.NEGOTIATION)
    assert csi.current_state == ConversationState.NEGOTIATION


def test_negotiation_back_to_debt_discussion() -> None:
    """NEGOTIATION → DEBT_DISCUSSION is allowed (customer wants to revisit)."""
    csi = ConversationStateIntelligence(initial_state=ConversationState.NEGOTIATION)
    csi.transition(ConversationState.DEBT_DISCUSSION)
    assert csi.current_state == ConversationState.DEBT_DISCUSSION
