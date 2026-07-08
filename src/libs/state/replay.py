"""EventTailReplay — replay events from a snapshot forward (V3 Ch6/Ch7).

Loads (or accepts) a snapshot, restores it into the target component, then
replays every subsequent event for that call from the EventBus (Sprint-013,
Redis Streams) in offset order, so the component's final state is
deterministic and identical to what it would have been had it never crashed.

Architecture: V3 Ch6 (event tail replay), V3 Ch7 (Crash Recovery).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import CallId

from .recoverable import Recoverable, StateSnapshot


@runtime_checkable
class EventReplaySource(Protocol):
    """Protocol for anything that can replay a stream's history (EventBus)."""

    def replay_from(self, offset: str = "-", count: int | None = None) -> list[tuple[str, EventEnvelope]]: ...


class EventTailReplay:
    """Replays a call's event tail onto a Recoverable component."""

    def __init__(self, event_bus: EventReplaySource) -> None:
        self._bus = event_bus

    def replay_from_snapshot(
        self,
        call_id: CallId,
        component: Recoverable,
        snapshot: StateSnapshot | None,
    ) -> int:
        """Restore ``snapshot`` (if any) into ``component``, then replay events.

        Args:
            call_id: The call whose event tail is replayed. Events belonging
                to other calls in the same stream are skipped.
            component: The Recoverable to restore/advance.
            snapshot: The most recent snapshot, or ``None`` if the component
                has never been snapshotted (replay starts from the beginning
                of the stream).

        Returns:
            The number of events actually applied to ``component`` (i.e. the
            events for this call_id found strictly after the snapshot's
            offset — 0 if there is nothing new to replay).
        """
        if snapshot is not None:
            component.restore(snapshot)
            offset = snapshot.last_event_offset
        else:
            offset = "-"

        entries = self._bus.replay_from(offset=offset)

        replayed = 0
        for entry_id, envelope in entries:
            if snapshot is not None and entry_id == snapshot.last_event_offset:
                # XRANGE's min bound is inclusive on real Redis — skip the
                # entry the snapshot was already taken at (resume is strictly
                # after the snapshot's offset).
                continue
            if envelope.payload.get("call_id") != call_id:
                continue
            component.apply_event(envelope)
            replayed += 1
        return replayed
