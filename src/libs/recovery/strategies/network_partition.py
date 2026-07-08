"""NetworkPartitionStrategy — island mode: drain calls, await reconnect.

A network partition isolates this node from its peers/shared infrastructure.
Rather than risk split-brain writes, the node enters "island mode" (refuses
new authoritative work), gracefully drains in-flight calls, and waits for
connectivity to be restored before resuming normal operation.

Architecture: V3 Ch7 (Crash Recovery — network partition class).
"""

from __future__ import annotations

from src.libs.contracts.primitives import CallId

from ..outcome import RecoveryOutcome


class NetworkPartitionStrategy:
    """Drains active calls and enters island mode during a network partition."""

    strategy_name = "network_partition"

    def __init__(self) -> None:
        self._island_mode = False

    def recover(self, active_call_ids: list[CallId]) -> RecoveryOutcome:
        """Enter island mode and record the calls being drained."""
        self._island_mode = True
        return RecoveryOutcome(success=True, detail={"mode": "island", "drained_calls": list(active_call_ids)})

    def resolve(self) -> None:
        """Called once connectivity is restored — exits island mode."""
        self._island_mode = False

    @property
    def is_island_mode(self) -> bool:
        return self._island_mode
