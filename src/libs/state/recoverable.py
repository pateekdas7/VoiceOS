"""Recoverable protocol + StateSnapshot value type (V3 Ch6).

Any stateful component that must survive a crash implements ``Recoverable``:
serialize its state to a ``StateSnapshot``, restore from one, and apply a
single replayed event to advance state deterministically. ``Snapshot``
(snapshot.py) and ``EventTailReplay`` (replay.py) operate purely in terms of
this protocol, so recovery logic is never coupled to a specific component.

Architecture: V3 Ch6 (State Persistence — Snapshot + event tail replay,
Recoverable protocol).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import CallId


class StateSnapshot(BaseModel):
    """A single point-in-time serialization of a Recoverable component's state.

    ``version`` is a monotonically increasing counter owned by the component
    itself (incremented once per ``snapshot()`` call) — the persistence layer
    (``Snapshot``) never invents versions, it only stores/retrieves them, so
    the highest stored version for a ``call_id`` is always the most recent.
    """

    model_config = ConfigDict(frozen=True)

    call_id: CallId
    version: int = Field(ge=1)
    state: dict[str, Any]
    """Serialized component state — JSON-compatible values only."""

    last_event_offset: str = "-"
    """EventBus stream entry ID of the last event applied before this
    snapshot was taken. Replay resumes strictly after this offset (exclusive)
    — ``"-"`` (Redis Streams' "beginning" sentinel) means no events have been
    applied yet, so replay starts from the very first entry."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@runtime_checkable
class Recoverable(Protocol):
    """Mixin protocol for any stateful component that supports crash recovery."""

    def snapshot(self) -> StateSnapshot:
        """Serialize current state into a StateSnapshot (fresh version)."""
        ...

    def restore(self, snapshot: StateSnapshot) -> None:
        """Replace current state with the state captured in ``snapshot``."""
        ...

    def apply_event(self, event: EventEnvelope) -> None:
        """Advance state by one replayed event, in event order."""
        ...
