"""FakeRecoverable — controllable Recoverable test double (Sprint-015).

Matches the Sprint-015.md Phase 1 Mock Backends table: "ConversationEngine
(crash simulation) | FakeRecoverable test double | Implements Recoverable
protocol with controllable state".
"""

from __future__ import annotations

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import CallId
from src.libs.state.recoverable import StateSnapshot


class FakeRecoverable:
    """A minimal Recoverable with one controllable integer counter."""

    def __init__(self, call_id: CallId, counter: int = 0) -> None:
        self.call_id = call_id
        self.counter = counter
        self.applied_event_ids: list[str] = []
        self._version = 0

    def snapshot(self) -> StateSnapshot:
        self._version += 1
        return StateSnapshot(
            call_id=self.call_id,
            version=self._version,
            state={"counter": self.counter},
            last_event_offset=str(len(self.applied_event_ids)),
        )

    def restore(self, snapshot: StateSnapshot) -> None:
        self.counter = int(snapshot.state["counter"])
        self._version = snapshot.version

    def apply_event(self, event: EventEnvelope) -> None:
        self.counter += 1
        self.applied_event_ids.append(event.event_id)


class FakeEventBus:
    """Duck-typed EventBus double exposing only ``replay_from()``."""

    def __init__(self, entries: list[tuple[str, EventEnvelope]]) -> None:
        self._entries = entries

    def replay_from(self, offset: str = "-", count: int | None = None) -> list[tuple[str, EventEnvelope]]:
        return self._entries
