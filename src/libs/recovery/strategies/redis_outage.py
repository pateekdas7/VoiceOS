"""RedisOutageStrategy — degrade to Postgres, write no new Redis state.

Redis is explicitly non-authoritative (V3 Ch4 §4.4): when it is unavailable,
the system must not block or crash, but it must also stop depending on Redis
for anything (WorkingMemoryStore, EventBus, locks) until it recovers. This
strategy flips a degraded-mode flag callers check before writing to Redis.

Architecture: V3 Ch7 (Crash Recovery — Redis outage class); V3 Ch4 §4.4.
"""

from __future__ import annotations

from ..outcome import RecoveryOutcome


class RedisOutageStrategy:
    """Puts the system into degraded (Postgres-only) mode during a Redis outage."""

    strategy_name = "redis_outage"

    def __init__(self) -> None:
        self._degraded = False

    def recover(self) -> RecoveryOutcome:
        """Enter degraded mode: no new Redis state is written until recovery."""
        self._degraded = True
        return RecoveryOutcome(success=True, detail={"mode": "degraded_to_postgres"})

    def resolve(self) -> None:
        """Called once Redis is confirmed healthy again — exits degraded mode."""
        self._degraded = False

    @property
    def is_degraded(self) -> bool:
        return self._degraded
