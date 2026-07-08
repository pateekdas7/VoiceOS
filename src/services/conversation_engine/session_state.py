"""ConversationSessionState — the per-call Recoverable state ConversationEngine tracks.

Sprint-012's ConversationEngine takes ``intent_history`` as a caller-supplied
argument on every ``handle_turn()`` call rather than owning any internal
per-call state — there was nothing to snapshot. This is the minimal
Sprint-015 baseline that makes it a real ``Recoverable``: turn count and
recent intent history, enough for ``CPURestartStrategy`` to demonstrably
restore a call to its pre-crash state via snapshot + event-tail replay.

Architecture: V3 Ch6 (Recoverable protocol).
"""

from __future__ import annotations

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import CallId
from src.libs.state.recoverable import StateSnapshot

_MAX_INTENT_HISTORY = 10
"""Matches AdaptiveConversationEngine's loop-detection window (Sprint-012)."""


class ConversationSessionState:
    """Recoverable per-call conversation state."""

    def __init__(self, call_id: CallId) -> None:
        self._call_id = call_id
        self._turn_count = 0
        self._intent_history: list[str] = []
        self._version = 0
        self._last_event_offset = "-"

    @property
    def call_id(self) -> CallId:
        return self._call_id

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def intent_history(self) -> list[str]:
        return list(self._intent_history)

    def record_turn(self, intent_label: str | None, event_offset: str) -> None:
        """Advance state after one processed turn.

        Args:
            intent_label: The turn's classified intent label, if any.
            event_offset: The EventBus entry ID this turn's DecisionEnvelope
                was published under — becomes the snapshot's resume point.
        """
        self._turn_count += 1
        if intent_label is not None:
            self._intent_history = [*self._intent_history, intent_label][-_MAX_INTENT_HISTORY:]
        self._last_event_offset = event_offset

    # ------------------------------------------------------------------
    # Recoverable protocol
    # ------------------------------------------------------------------

    def snapshot(self) -> StateSnapshot:
        self._version += 1
        return StateSnapshot(
            call_id=self._call_id,
            version=self._version,
            state={"turn_count": self._turn_count, "intent_history": self._intent_history},
            last_event_offset=self._last_event_offset,
        )

    def restore(self, snapshot: StateSnapshot) -> None:
        self._turn_count = int(snapshot.state.get("turn_count", 0))
        self._intent_history = list(snapshot.state.get("intent_history", []))
        self._version = snapshot.version
        self._last_event_offset = snapshot.last_event_offset

    def apply_event(self, event: EventEnvelope) -> None:
        """Advance state by one replayed ``decision.made`` event."""
        if event.event_type == "decision.made":
            self._turn_count += 1
