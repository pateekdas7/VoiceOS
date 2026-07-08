"""State persistence — snapshot + event-tail replay (V3 Ch6).

Provides the ``Recoverable`` protocol any stateful component implements to
become snapshot-able and replay-able, ``Snapshot`` (periodic serialization to
Postgres), ``EventTailReplay`` (replay from a snapshot forward using the
Sprint-013 EventBus), and ``RecoveryLog`` (auditable recovery history).

Architecture: V3 Ch6 (State Persistence).
"""

from __future__ import annotations

from .recoverable import Recoverable, StateSnapshot
from .recovery_log import RecoveryAttempt, RecoveryLog
from .replay import EventTailReplay
from .snapshot import Snapshot

__all__ = [
    "EventTailReplay",
    "Recoverable",
    "RecoveryAttempt",
    "RecoveryLog",
    "Snapshot",
    "StateSnapshot",
]
