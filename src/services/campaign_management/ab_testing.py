"""ABTestingFramework — deterministic variant assignment + result tracking (V5 Ch6.6).

Architecture: V5 Ch6 (Campaign Management — A/B Testing).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.libs.contracts.models.campaign import ABTestVariant, CampaignResult
from src.libs.contracts.primitives import CampaignId, TenantId

if TYPE_CHECKING:
    from src.libs.repositories.campaign_result import CampaignResultRepository


class ABTestingFramework:
    """Deterministic A/B variant assignment and per-variant outcome tracking."""

    def __init__(self, result_repository: CampaignResultRepository | None = None) -> None:
        self._result_repo = result_repository

    @staticmethod
    def assign_variant(customer_id: str, campaign_id: str, variants: tuple[ABTestVariant, ...]) -> ABTestVariant | None:
        """Deterministically assign one of ``variants`` to ``customer_id`` for ``campaign_id``.

        Same ``(customer_id, campaign_id)`` always yields the same variant
        (AC: "assigns variants deterministically") — a stable SHA-256 hash of
        the pair is mapped onto the variants' cumulative ``traffic_weight``
        distribution, so weighting is respected across the whole population
        even though any single customer's assignment is fixed.
        """
        if not variants:
            return None
        digest = hashlib.sha256(f"{customer_id}:{campaign_id}".encode()).hexdigest()
        bucket = int(digest, 16) % 100
        cumulative = 0
        for variant in variants:
            cumulative += variant.traffic_weight
            if bucket < cumulative:
                return variant
        return variants[-1]

    def record_result(
        self,
        tenant_id: TenantId,
        campaign_id: CampaignId,
        customer_id: str,
        outcome_code: str,
        *,
        variant_id: str | None = None,
        call_id: str | None = None,
        ptp_created: bool = False,
    ) -> CampaignResult:
        """Persist one contact-attempt outcome for variant metrics."""
        now = datetime.now(UTC)
        result = CampaignResult(
            campaign_result_id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            variant_id=variant_id,
            customer_id=customer_id,
            call_id=call_id,
            outcome_code=outcome_code,
            ptp_created=ptp_created,
            completed_at=now,
            created_at=now,
        )
        if self._result_repo is not None:
            self._result_repo.create(result)
        return result

    def variant_metrics(self, tenant_id: TenantId, campaign_id: CampaignId, variant_id: str) -> dict[str, float | int]:
        """PTP rate and completion count for one variant (V5 Ch6 "per-variant PTP rate, completion rate")."""
        if self._result_repo is None:
            return {"completion_count": 0, "ptp_count": 0, "ptp_rate": 0.0}
        results = self._result_repo.find_by_variant(tenant_id, campaign_id, variant_id)
        completion_count = len(results)
        ptp_count = sum(1 for r in results if r.ptp_created)
        ptp_rate = (ptp_count / completion_count) if completion_count else 0.0
        return {"completion_count": completion_count, "ptp_count": ptp_count, "ptp_rate": ptp_rate}
