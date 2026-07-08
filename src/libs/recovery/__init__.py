"""Crash recovery — per-failure-class deterministic recovery (V3 Ch7).

``RecoveryManager`` routes a detected failure class to its registered
strategy (``strategies/``) and audits the outcome via ``RecoveryLog``
(``src.libs.state``).

Architecture: V3 Ch7 (Crash Recovery).
"""

from __future__ import annotations

from .outcome import RecoveryOutcome
from .recovery_manager import RecoveryManager
from .strategies import (
    CPURestartStrategy,
    DBOutageStrategy,
    GPUFailoverPort,
    GPUFailureStrategy,
    NetworkPartitionStrategy,
    RedisOutageStrategy,
    TTSHaltPort,
    TwilioDisconnectStrategy,
)

__all__ = [
    "CPURestartStrategy",
    "DBOutageStrategy",
    "GPUFailoverPort",
    "GPUFailureStrategy",
    "NetworkPartitionStrategy",
    "RecoveryManager",
    "RecoveryOutcome",
    "RedisOutageStrategy",
    "TTSHaltPort",
    "TwilioDisconnectStrategy",
]
