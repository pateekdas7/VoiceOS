"""CallDispatcher — emits call initiation requests for active campaigns (V5 Ch6.9).

Architecture: V5 Ch6 (Campaign Management — dispatch sequence).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.libs.contracts.models.campaign import ABTestVariant, Campaign, CampaignAudienceMember
from src.libs.contracts.primitives import TenantId

from .ab_testing import ABTestingFramework
from .scheduler import ScheduleEngine


@dataclass(frozen=True)
class CallRequest:
    """One call the dialler should place, parametrized by campaign/variant (V5 Ch6.12)."""

    tenant_id: TenantId
    campaign_id: str
    customer_id: str
    loan_account_id: str | None
    variant: ABTestVariant | None
    scheduled_at: object


class CallDispatcher:
    """Selects the next eligible calls for an ACTIVE campaign's audience."""

    def __init__(self, schedule_engine: ScheduleEngine, ab_testing: ABTestingFramework) -> None:
        self._schedule_engine = schedule_engine
        self._ab_testing = ab_testing

    def dispatch(
        self,
        tenant_id: TenantId,
        campaign: Campaign,
        audience: tuple[CampaignAudienceMember, ...],
        *,
        hour: int,
        calls_today_count_by_customer: dict[str, int] | None = None,
    ) -> tuple[CallRequest, ...]:
        """Return the subset of ``audience`` eligible to be dialled right now.

        Members excluded from the cohort (``excluded_reason``) or flagged
        ``dnd`` are skipped before ``ScheduleEngine`` is even consulted.
        """
        calls_today = calls_today_count_by_customer or {}
        dispatched: list[CallRequest] = []
        for member in audience:
            if member.excluded_reason or member.dnd:
                continue
            next_call_at = self._schedule_engine.schedule_next_call(
                tenant_id,
                member.customer_id,
                campaign.campaign_id,
                hour=hour,
                calls_today_count=calls_today.get(member.customer_id, 0),
                retry_policy=campaign.retry_policy,
            )
            if next_call_at is None:
                continue
            variant = (
                self._ab_testing.assign_variant(member.customer_id, campaign.campaign_id, campaign.ab_variants)
                if campaign.ab_variants
                else None
            )
            dispatched.append(
                CallRequest(
                    tenant_id=tenant_id,
                    campaign_id=campaign.campaign_id,
                    customer_id=member.customer_id,
                    loan_account_id=member.loan_account_id,
                    variant=variant,
                    scheduled_at=next_call_at,
                )
            )
        return tuple(dispatched)
