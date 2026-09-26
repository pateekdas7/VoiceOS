"""Entry point for the DailyAggregationJob CronJob (V5 Ch11).

Called by the K8s CronJob at 00:05 UTC daily (infra/helm/voiceos-platform/
charts/analytics/templates/daily-aggregation-cronjob.yaml). Aggregates the
previous day's call dispositions and campaign results into analytics_daily
for every active tenant.

Usage:
    python -m scripts.run_daily_aggregation               # aggregate yesterday
    python -m scripts.run_daily_aggregation 2026-07-28    # aggregate specific date
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta

import psycopg2

from src.libs.repositories.analytics import AnalyticsDailyRepository
from src.libs.repositories.call_disposition import CallDispositionRepository
from src.libs.repositories.campaign_result import CampaignResultRepository
from src.libs.repositories.tenant import TenantRepository
from src.services.analytics.aggregation import DailyAggregationJob

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
_log = logging.getLogger("voiceos.daily_aggregation")


def main() -> None:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        _log.error("POSTGRES_DSN environment variable is required")
        sys.exit(1)

    if len(sys.argv) > 1:
        target_day = date.fromisoformat(sys.argv[1])
    elif os.environ.get("AGGREGATION_DATE"):
        target_day = date.fromisoformat(os.environ["AGGREGATION_DATE"])
    else:
        target_day = date.today() - timedelta(days=1)

    _log.info("Connecting to Postgres")
    conn = psycopg2.connect(dsn)

    job = DailyAggregationJob(
        CallDispositionRepository(conn),
        CampaignResultRepository(conn),
        AnalyticsDailyRepository(conn),
    )

    tenant_repo = TenantRepository(conn)
    tenants = tenant_repo.list_all()

    _log.info("Running DailyAggregationJob for %s across %d tenants", target_day, len(tenants))
    errors = 0
    for tenant in tenants:
        try:
            rollup = job.run_for_day(tenant.tenant_id, target_day)
            _log.info(
                "tenant=%s calls=%d ptp_count=%d contactability=%.2f",
                tenant.tenant_id,
                rollup.calls_completed,
                rollup.ptp_count,
                rollup.contactability_rate,
            )
        except Exception as exc:
            _log.error("Failed for tenant=%s: %s", tenant.tenant_id, exc)
            errors += 1

    conn.close()
    if errors:
        _log.error("%d tenant(s) failed aggregation", errors)
        sys.exit(1)
    _log.info("DailyAggregationJob complete")


if __name__ == "__main__":
    main()
