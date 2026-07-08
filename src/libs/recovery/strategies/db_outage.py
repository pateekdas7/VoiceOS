"""DBOutageStrategy — halt new calls, hold existing calls in-memory.

Postgres is the authoritative store (Law of Authority). When it is
unreachable, the system must refuse new calls (no authoritative writes can
be guaranteed) while letting already-in-progress calls continue on their
in-memory state until either Postgres recovers or the call ends naturally.

Architecture: V3 Ch7 (Crash Recovery — DB outage class).
"""

from __future__ import annotations

from ..outcome import RecoveryOutcome


class DBOutageStrategy:
    """Stops new call admission during a Postgres outage; holds existing calls."""

    strategy_name = "db_outage"

    def __init__(self) -> None:
        self._accepting_new_calls = True

    def recover(self) -> RecoveryOutcome:
        """Halt new-call admission; existing calls are held in memory, not dropped."""
        self._accepting_new_calls = False
        return RecoveryOutcome(success=True, detail={"accepting_new_calls": False, "mode": "hold_existing_in_memory"})

    def resolve(self) -> None:
        """Called once Postgres is confirmed healthy again — resumes admission."""
        self._accepting_new_calls = True

    @property
    def accepting_new_calls(self) -> bool:
        return self._accepting_new_calls
