"""CampaignResultRepository — per-contact-attempt campaign outcomes (Sprint-023).

Backs ``ABTestingFramework``'s per-variant PTP-rate/completion-rate metrics.

Architecture: V5 Ch6 (Campaign Engine — A/B testing, outcome analytics).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.campaign import CampaignResult
from ..contracts.primitives import CampaignId, TenantId
from .base import BaseRepository

_TABLE = "campaign_results"

_COLUMNS = (
    "campaign_result_id",
    "campaign_id",
    "tenant_id",
    "variant_id",
    "customer_id",
    "call_id",
    "outcome_code",
    "ptp_created",
    "completed_at",
    "created_at",
)


class CampaignResultRepository(BaseRepository):
    """Tenant-scoped CRUD + aggregation queries for campaign contact outcomes."""

    def create(self, result: CampaignResult) -> CampaignResult:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                campaign_result_id, campaign_id, tenant_id, variant_id,
                customer_id, call_id, outcome_code, ptp_created, completed_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                result.campaign_result_id,
                result.campaign_id,
                result.tenant_id,
                result.variant_id,
                result.customer_id,
                result.call_id,
                result.outcome_code,
                result.ptp_created,
                result.completed_at,
                result.created_at,
            ),
        )
        self._commit()
        return result

    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[CampaignResult, ...]:
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
            order_by="completed_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def find_between(self, tenant_id: TenantId, start: Any, end: Any) -> tuple[CampaignResult, ...]:
        """Find all campaign results in ``[start, end)`` for a tenant, across every campaign

        (Sprint-024: the tenant-wide ``DailyAggregationJob`` rollup source — as distinct from
        ``find_by_campaign``, which is scoped to one campaign regardless of date).
        """
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="completed_at >= %s AND completed_at < %s",
            extra_params=(start, end),
            order_by="completed_at",
        )
        return tuple(self._hydrate(row) for row in rows)

    def find_by_variant(
        self, tenant_id: TenantId, campaign_id: CampaignId, variant_id: str
    ) -> tuple[CampaignResult, ...]:
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s AND variant_id = %s",
            extra_params=(campaign_id, variant_id),
            order_by="completed_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> CampaignResult:
        (
            campaign_result_id,
            campaign_id,
            tenant_id,
            variant_id,
            customer_id,
            call_id,
            outcome_code,
            ptp_created,
            completed_at,
            created_at,
        ) = row
        return CampaignResult(
            campaign_result_id=str(campaign_result_id),
            campaign_id=CampaignId(str(campaign_id)),
            tenant_id=TenantId(str(tenant_id)),
            variant_id=str(variant_id) if variant_id is not None else None,
            customer_id=str(customer_id),
            call_id=str(call_id) if call_id is not None else None,
            outcome_code=outcome_code,
            ptp_created=ptp_created,
            completed_at=completed_at,
            created_at=created_at,
        )
