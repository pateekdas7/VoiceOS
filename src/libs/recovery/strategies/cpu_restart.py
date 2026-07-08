"""CPURestartStrategy — replay from the last snapshot after a process restart.

The most common recovery path: ConversationEngine (or any Recoverable
component) restarts on the CPU node, loads its most recent Postgres
snapshot, and replays the event tail since that snapshot so its state is
identical to what it was immediately before the crash.

Architecture: V3 Ch7 (Crash Recovery — CPU restart class).
"""

from __future__ import annotations

from src.libs.contracts.primitives import CallId, TenantId
from src.libs.state.recoverable import Recoverable
from src.libs.state.replay import EventTailReplay
from src.libs.state.snapshot import Snapshot

from ..outcome import RecoveryOutcome


class CPURestartStrategy:
    """Recovers a Recoverable component's state after a CPU/process restart."""

    strategy_name = "cpu_restart"

    def __init__(self, snapshot_store: Snapshot, replay: EventTailReplay) -> None:
        self._snapshot_store = snapshot_store
        self._replay = replay

    def recover(self, component: Recoverable, tenant_id: TenantId, call_id: CallId) -> RecoveryOutcome:
        """Restore ``component`` from its latest snapshot and replay events since.

        Returns:
            A successful RecoveryOutcome carrying the snapshot version used
            (0 if none existed) and the number of events replayed.
        """
        snapshot = self._snapshot_store.load_latest_snapshot(tenant_id, call_id)
        events_replayed = self._replay.replay_from_snapshot(call_id, component, snapshot)
        return RecoveryOutcome(
            success=True,
            detail={
                "snapshot_version": snapshot.version if snapshot is not None else 0,
                "events_replayed": events_replayed,
            },
        )
