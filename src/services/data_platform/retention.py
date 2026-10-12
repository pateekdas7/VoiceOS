"""Event retention enforcer — deletes raw_events rows past their policy TTL.

Runs as a periodic job (daily). Matches event_type against glob patterns in
event_retention_policies (most-specific wins) and hard-deletes expired rows
from raw_events partitions.
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import UTC, datetime, timedelta

import asyncpg

_log = logging.getLogger("voiceos.data_platform.retention")


class RetentionEnforcer:
    def __init__(self, pg_pool: asyncpg.Pool) -> None:
        self._pool = pg_pool

    async def _load_policies(self) -> list[tuple[str, int]]:
        rows = await self._pool.fetch(
            "SELECT event_type_glob, retain_days FROM event_retention_policies "
            "WHERE is_active ORDER BY LENGTH(event_type_glob) DESC"
        )
        return [(r["event_type_glob"], r["retain_days"]) for r in rows]

    def _retention_days(self, event_type: str, policies: list[tuple[str, int]]) -> int:
        for glob, days in policies:
            if fnmatch.fnmatch(event_type, glob):
                return days
        return 90  # safe default

    async def run(self) -> dict[str, int]:
        policies = await self._load_policies()
        event_types: list[str] = [
            r["event_type"] for r in await self._pool.fetch("SELECT DISTINCT event_type FROM raw_events")
        ]

        totals: dict[str, int] = {}
        now = datetime.now(tz=UTC)

        for et in event_types:
            days = self._retention_days(et, policies)
            cutoff = now - timedelta(days=days)
            result = await self._pool.execute(
                "DELETE FROM raw_events WHERE event_type=$1 AND occurred_at < $2",
                et,
                cutoff,
            )
            deleted = int(result.split()[-1]) if result else 0
            if deleted:
                totals[et] = deleted
                _log.info("Retention: deleted=%d event_type=%s cutoff=%s", deleted, et, cutoff.date())

        _log.info("Retention run complete. types_pruned=%d total_deleted=%d", len(totals), sum(totals.values()))
        return totals
