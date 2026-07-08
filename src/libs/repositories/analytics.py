"""AnalyticsDailyRepository — pre-computed daily rollups (Sprint-024, V5 Ch11).

Backs ``DailyAggregationJob``'s upsert-per-day writes and the AnalyticsService/
ReportingService/BIWarehouse read paths.

Architecture: V5 Ch11 (Analytics Platform); V5 Ch12 (Reporting Platform).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..contracts.models.analytics import AnalyticsDailyRollup
from ..contracts.primitives import CampaignId, TenantId
from .base import BaseRepository

_TABLE = "analytics_daily"

_COLUMNS = (
    "analytics_daily_id",
    "tenant_id",
    "day",
    "campaign_id",
    "calls_completed",
    "ptp_count",
    "ptp_rate",
    "avg_duration_ms",
    "amount_collected_minor",
    "contactability_rate",
    "recovery_rate",
    "avg_dpd",
    "computed_at",
)


class AnalyticsDailyRepository(BaseRepository):
    """Tenant-scoped upsert + read queries for the ``analytics_daily`` rollup table."""

    def upsert(self, rollup: AnalyticsDailyRollup) -> AnalyticsDailyRollup:
        """Insert a day's rollup, or replace it if the day (± campaign) was already computed.

        ``ON CONFLICT`` targets the partial unique indexes from migration
        0022 — the tenant-wide index when ``campaign_id IS NULL``, the
        per-campaign index otherwise — so re-running ``DailyAggregationJob``
        for the same day is idempotent.
        """
        conflict_target = "(tenant_id, day) WHERE campaign_id IS NULL"
        if rollup.campaign_id is not None:
            conflict_target = "(tenant_id, day, campaign_id) WHERE campaign_id IS NOT NULL"
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                analytics_daily_id, tenant_id, day, campaign_id, calls_completed, ptp_count,
                ptp_rate, avg_duration_ms, amount_collected_minor, contactability_rate,
                recovery_rate, avg_dpd, computed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT {conflict_target}
            DO UPDATE SET
                calls_completed = EXCLUDED.calls_completed,
                ptp_count = EXCLUDED.ptp_count,
                ptp_rate = EXCLUDED.ptp_rate,
                avg_duration_ms = EXCLUDED.avg_duration_ms,
                amount_collected_minor = EXCLUDED.amount_collected_minor,
                contactability_rate = EXCLUDED.contactability_rate,
                recovery_rate = EXCLUDED.recovery_rate,
                avg_dpd = EXCLUDED.avg_dpd,
                computed_at = EXCLUDED.computed_at
            """,
            (
                rollup.analytics_daily_id,
                rollup.tenant_id,
                rollup.day,
                rollup.campaign_id,
                rollup.calls_completed,
                rollup.ptp_count,
                rollup.ptp_rate,
                rollup.avg_duration_ms,
                rollup.amount_collected_minor,
                rollup.contactability_rate,
                rollup.recovery_rate,
                rollup.avg_dpd,
                rollup.computed_at,
            ),
        )
        self._commit()
        return rollup

    def find_for_day(
        self, tenant_id: TenantId, day: date, campaign_id: CampaignId | None = None
    ) -> AnalyticsDailyRollup | None:
        extra_where = "day = %s AND campaign_id IS NULL" if campaign_id is None else "day = %s AND campaign_id = %s"
        extra_params: tuple[Any, ...] = (day,) if campaign_id is None else (day, campaign_id)
        row = self._tenant_select_one(_TABLE, _COLUMNS, tenant_id, extra_where=extra_where, extra_params=extra_params)
        return self._hydrate(row) if row is not None else None

    def find_range(
        self, tenant_id: TenantId, start: date, end: date, campaign_id: CampaignId | None = None
    ) -> tuple[AnalyticsDailyRollup, ...]:
        if campaign_id is None:
            extra_where = "day >= %s AND day <= %s AND campaign_id IS NULL"
            extra_params: tuple[Any, ...] = (start, end)
        else:
            extra_where = "day >= %s AND day <= %s AND campaign_id = %s"
            extra_params = (start, end, campaign_id)
        rows = self._tenant_select(
            _TABLE, _COLUMNS, tenant_id, extra_where=extra_where, extra_params=extra_params, order_by="day"
        )
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> AnalyticsDailyRollup:
        (
            analytics_daily_id,
            tenant_id,
            day,
            campaign_id,
            calls_completed,
            ptp_count,
            ptp_rate,
            avg_duration_ms,
            amount_collected_minor,
            contactability_rate,
            recovery_rate,
            avg_dpd,
            computed_at,
        ) = row
        return AnalyticsDailyRollup(
            analytics_daily_id=str(analytics_daily_id),
            tenant_id=TenantId(str(tenant_id)),
            day=day,
            campaign_id=CampaignId(str(campaign_id)) if campaign_id is not None else None,
            calls_completed=calls_completed,
            ptp_count=ptp_count,
            ptp_rate=ptp_rate,
            avg_duration_ms=avg_duration_ms,
            amount_collected_minor=amount_collected_minor,
            contactability_rate=contactability_rate,
            recovery_rate=recovery_rate,
            avg_dpd=avg_dpd,
            computed_at=computed_at,
        )
