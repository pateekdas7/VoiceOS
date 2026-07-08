"""CampaignRepository — dialler campaign records (V5 Ch6).

Sprint-023 fills in the ``ab_test_variants`` CRUD this repository's
docstring previously deferred: ``create_variant``/``find_variants`` below.
``Campaign.ab_variants`` still round-trips as empty from ``get()``/
``find_active_for_tenant()`` (unchanged from Sprint-014) — callers needing
variants call ``find_variants()`` explicitly, same as every other
one-to-many relationship in this repository layer (e.g. loans are not
inlined onto ``Customer``).

Architecture: V5 Ch6 (Campaign Engine).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..contracts.models.campaign import ABTestVariant, AudienceCriteria, Campaign, CampaignStatus, RetryPolicy
from ..contracts.primitives import CampaignId, TenantId
from .base import BaseRepository

_TABLE = "campaigns"
_VARIANT_TABLE = "ab_test_variants"
_VARIANT_COLUMNS = ("variant_id", "campaign_id", "name", "strategy_override", "traffic_weight")

_CAMPAIGN_COLUMNS = (
    "campaign_id",
    "tenant_id",
    "name",
    "description",
    "status",
    "audience_criteria",
    "max_attempts",
    "retry_interval_hours",
    "retry_on_outcomes",
    "do_not_retry_on_outcomes",
    "scheduled_start",
    "scheduled_end",
    "daily_start_hour",
    "daily_end_hour",
    "timezone",
    "default_strategy",
    "target_call_count",
    "completed_call_count",
    "created_at",
    "updated_at",
    "created_by",
)


class CampaignRepository(BaseRepository):
    """Tenant-scoped CRUD + lookup queries for the ``campaigns`` domain."""

    def create(self, campaign: Campaign) -> Campaign:
        """Insert a new campaign record."""
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                campaign_id, tenant_id, name, description, status, audience_criteria,
                max_attempts, retry_interval_hours, retry_on_outcomes, do_not_retry_on_outcomes,
                scheduled_start, scheduled_end, daily_start_hour, daily_end_hour, timezone,
                default_strategy, target_call_count, completed_call_count,
                created_at, updated_at, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                campaign.campaign_id,
                campaign.tenant_id,
                campaign.name,
                campaign.description,
                campaign.status.value,
                json.dumps(campaign.audience_criteria.model_dump(mode="json")),
                campaign.retry_policy.max_attempts,
                campaign.retry_policy.retry_interval_hours,
                list(campaign.retry_policy.retry_on_outcomes),
                list(campaign.retry_policy.do_not_retry_on_outcomes),
                campaign.scheduled_start,
                campaign.scheduled_end,
                campaign.daily_start_hour,
                campaign.daily_end_hour,
                campaign.timezone,
                campaign.default_strategy,
                campaign.target_call_count,
                campaign.completed_call_count,
                campaign.created_at,
                campaign.updated_at,
                campaign.created_by,
            ),
        )
        self._commit()
        return campaign

    def get(self, tenant_id: TenantId, campaign_id: str) -> Campaign | None:
        """Fetch a campaign by ID, scoped to ``tenant_id``."""
        row = self._tenant_select_one(
            _TABLE,
            _CAMPAIGN_COLUMNS,
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
        )
        return self._hydrate(row) if row is not None else None

    def find_active_for_tenant(self, tenant_id: TenantId) -> tuple[Campaign, ...]:
        """Find all ACTIVE campaigns for a tenant."""
        rows = self._tenant_select(
            _TABLE,
            _CAMPAIGN_COLUMNS,
            tenant_id,
            extra_where="status = %s",
            extra_params=(CampaignStatus.ACTIVE.value,),
            order_by="created_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def update_status(self, tenant_id: TenantId, campaign_id: str, status: CampaignStatus) -> None:
        """Transition a campaign's lifecycle status, scoped to ``tenant_id``."""
        self._tenant_update(
            _TABLE,
            ("status",),
            (status.value,),
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
        )

    def update_counts(
        self, tenant_id: TenantId, campaign_id: str, target_call_count: int, completed_call_count: int
    ) -> None:
        """Update the audience-size / completed-attempt counters, scoped to ``tenant_id``."""
        self._tenant_update(
            _TABLE,
            ("target_call_count", "completed_call_count"),
            (target_call_count, completed_call_count),
            tenant_id,
            extra_where="campaign_id = %s",
            extra_params=(campaign_id,),
        )

    def create_variant(self, campaign_id: CampaignId, variant: ABTestVariant) -> ABTestVariant:
        """Insert one A/B test variant row for ``campaign_id``."""
        variant_id = variant.variant_id or str(uuid.uuid4())
        self._execute(
            f"""
            INSERT INTO {_VARIANT_TABLE} (variant_id, campaign_id, name, strategy_override, traffic_weight)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (variant_id, campaign_id, variant.name, variant.strategy_override, variant.traffic_weight),
        )
        self._commit()
        return ABTestVariant(
            variant_id=variant_id,
            name=variant.name,
            strategy_override=variant.strategy_override,
            traffic_weight=variant.traffic_weight,
        )

    def find_variants(self, campaign_id: CampaignId) -> tuple[ABTestVariant, ...]:
        """All A/B test variants configured for ``campaign_id``, in creation order."""
        cur = self._execute(
            f"SELECT {', '.join(_VARIANT_COLUMNS)} FROM {_VARIANT_TABLE} "
            f"WHERE campaign_id = %s ORDER BY created_at ASC",
            (campaign_id,),
        )
        rows: list[tuple[Any, ...]] = cur.fetchall()
        return tuple(
            ABTestVariant(
                variant_id=str(variant_id),
                name=name,
                strategy_override=strategy_override or "",
                traffic_weight=traffic_weight,
            )
            for variant_id, _campaign_id, name, strategy_override, traffic_weight in rows
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...]) -> Campaign:
        (
            campaign_id,
            tenant_id,
            name,
            description,
            status,
            audience_criteria_json,
            max_attempts,
            retry_interval_hours,
            retry_on_outcomes,
            do_not_retry_on_outcomes,
            scheduled_start,
            scheduled_end,
            daily_start_hour,
            daily_end_hour,
            tz_name,
            default_strategy,
            target_call_count,
            completed_call_count,
            created_at,
            updated_at,
            created_by,
        ) = row

        criteria_dict = (
            json.loads(audience_criteria_json) if isinstance(audience_criteria_json, str) else audience_criteria_json
        )

        return Campaign(
            campaign_id=campaign_id,
            tenant_id=TenantId(tenant_id),
            name=name,
            description=description or "",
            status=CampaignStatus(status),
            audience_criteria=AudienceCriteria.model_validate(criteria_dict or {}),
            retry_policy=RetryPolicy(
                max_attempts=max_attempts,
                retry_interval_hours=retry_interval_hours,
                retry_on_outcomes=tuple(retry_on_outcomes or ()),
                do_not_retry_on_outcomes=tuple(do_not_retry_on_outcomes or ()),
            ),
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            daily_start_hour=daily_start_hour,
            daily_end_hour=daily_end_hour,
            timezone=tz_name,
            default_strategy=default_strategy or "",
            ab_variants=(),
            target_call_count=target_call_count,
            completed_call_count=completed_call_count,
            created_at=created_at,
            updated_at=updated_at,
            created_by=created_by,
        )
