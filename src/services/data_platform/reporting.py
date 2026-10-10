"""Reporting aggregation worker — refreshes rpt_daily_call_summary from raw_events.

Runs after each ETL batch (or on a schedule). Computes per-tenant/campaign
call summary counts from raw_events payloads and upserts into the reporting
table for fast dashboard reads.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import asyncpg

_log = logging.getLogger("voiceos.data_platform.reporting")


class ReportingAggregator:
    def __init__(self, pg_pool: asyncpg.Pool) -> None:
        self._pool = pg_pool

    async def refresh_daily_summary(self, target_date: date | None = None) -> int:
        """Refresh rpt_daily_call_summary for target_date (defaults to yesterday + today)."""
        if target_date is None:
            today = datetime.now(tz=UTC).date()
            dates = [today - timedelta(days=1), today]
        else:
            dates = [target_date]

        total_rows = 0
        for d in dates:
            rows = await self._pool.fetch(
                """
                SELECT
                    tenant_id::uuid,
                    (payload->>'campaign_id')::uuid                      AS campaign_id,
                    COUNT(*)                                             AS calls_attempted,
                    COUNT(*) FILTER (WHERE payload->>'connected' = 'true')  AS calls_connected,
                    COUNT(*) FILTER (WHERE payload->>'answered'  = 'true')  AS calls_answered,
                    COUNT(*) FILTER (WHERE event_type = 'ptp.captured')     AS ptps_captured,
                    COALESCE(SUM((payload->>'duration_seconds')::int), 0)   AS total_duration_s
                FROM raw_events
                WHERE occurred_at >= $1::date
                  AND occurred_at <  $1::date + INTERVAL '1 day'
                  AND event_type IN ('call.started','call.ended','ptp.captured')
                GROUP BY tenant_id, payload->>'campaign_id'
                """,
                d,
            )

            for row in rows:
                await self._pool.execute(
                    """INSERT INTO rpt_daily_call_summary
                         (summary_date, tenant_id, campaign_id,
                          calls_attempted, calls_connected, calls_answered,
                          ptps_captured, total_duration_s, refreshed_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                       ON CONFLICT (summary_date, tenant_id,
                         COALESCE(campaign_id, '00000000-0000-0000-0000-000000000000'::uuid))
                       DO UPDATE SET
                         calls_attempted  = EXCLUDED.calls_attempted,
                         calls_connected  = EXCLUDED.calls_connected,
                         calls_answered   = EXCLUDED.calls_answered,
                         ptps_captured    = EXCLUDED.ptps_captured,
                         total_duration_s = EXCLUDED.total_duration_s,
                         refreshed_at     = NOW()""",
                    d,
                    row["tenant_id"],
                    row["campaign_id"],
                    row["calls_attempted"],
                    row["calls_connected"],
                    row["calls_answered"],
                    row["ptps_captured"],
                    row["total_duration_s"],
                )
            total_rows += len(rows)
            _log.info("Reporting: refreshed date=%s rows=%d", d, len(rows))

        return total_rows
