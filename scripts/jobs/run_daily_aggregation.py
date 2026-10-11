#!/usr/bin/env python3
"""K8s CronJob runner — daily analytics aggregation (Phase 6b).

Runs daily at 00:30 UTC (CronJob schedule: 30 0 * * *).
Aggregates the previous day's call dispositions, PTP outcomes (amount_collected_minor),
and average DPD for every tenant and upserts into analytics_daily.

Each tenant is processed independently; failures for one tenant are logged and
counted but do not abort the other tenants. The job exits 1 if any tenant failed.

Required environment variables:
    POSTGRES_DSN  -- e.g. postgresql://user:pass@host:5432/voiceos

Architecture: V5 Ch11 (Analytics Platform — DailyAggregationJob), Phase 6b.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
_log = logging.getLogger("voiceos.jobs.daily_aggregation")


def main() -> int:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        _log.error("POSTGRES_DSN is required")
        return 1

    try:
        import psycopg2

        from src.libs.repositories.analytics import AnalyticsDailyRepository
        from src.libs.repositories.call_disposition import CallDispositionRepository
        from src.libs.repositories.campaign_result import CampaignResultRepository
        from src.libs.repositories.loan_account import LoanAccountRepository
        from src.libs.repositories.promise_to_pay import PromiseToPayRepository
        from src.libs.repositories.tenant import TenantRepository
        from src.services.analytics.aggregation import DailyAggregationJob

        conn = psycopg2.connect(dsn)
        try:
            yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
            job = DailyAggregationJob(
                CallDispositionRepository(conn),
                CampaignResultRepository(conn),
                AnalyticsDailyRepository(conn),
                ptp_repository=PromiseToPayRepository(conn),
                loan_repository=LoanAccountRepository(conn),
            )
            tenants = TenantRepository(conn).list_all()
            _log.info("Aggregating %d tenants for %s", len(tenants), yesterday)
            errors = 0
            for tenant in tenants:
                try:
                    rollup = job.run_for_day(tenant.tenant_id, yesterday)
                    _log.info(
                        "Aggregated tenant=%s calls=%d ptps=%d amount=%d avg_dpd=%.2f",
                        tenant.tenant_id,
                        rollup.calls_completed,
                        rollup.ptp_count,
                        rollup.amount_collected_minor,
                        rollup.avg_dpd,
                    )
                except Exception:
                    _log.exception("Aggregation failed for tenant=%s", tenant.tenant_id)
                    errors += 1
            _log.info(
                "Daily aggregation complete: %d tenants, %d errors",
                len(tenants),
                errors,
            )
        finally:
            conn.close()
        return 0 if errors == 0 else 1
    except Exception:
        _log.exception("Daily aggregation job setup failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
