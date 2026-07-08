"""UsageAggregator — sums usage_events for a billing period (V5 Ch10, Sprint-024.md).

Shared by ``InvoiceGenerator`` (period-end aggregation) and any dashboard
that needs "how much has this tenant used so far this period" without going
through the Redis fast path (``UsageLimitEnforcer``).

Architecture: V5 Ch10 (Usage Metering).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Protocol

from src.libs.contracts.models.billing import UsageEvent, UsageType
from src.libs.contracts.primitives import TenantId


class UsageRepositoryPort(Protocol):
    def find_uninvoiced(
        self, tenant_id: TenantId, *, since_bucket: str = "", until_bucket: str = ""
    ) -> tuple[UsageEvent, ...]: ...


def hour_bucket(moment: datetime) -> str:
    """Floor ``moment`` to its UTC hour, formatted as the ``occurred_at_bucket`` convention
    (e.g. ``'2026-07-01T14:00:00Z'``)."""
    return moment.strftime("%Y-%m-%dT%H:00:00Z")


class UsageAggregator:
    """Aggregates recorded (not-yet-invoiced) usage events for a tenant's billing period."""

    def __init__(self, usage_repository: UsageRepositoryPort) -> None:
        self._usage_repository = usage_repository

    def aggregate_period(
        self, tenant_id: TenantId, period_start: datetime, period_end: datetime
    ) -> dict[UsageType, int]:
        """Total quantity consumed per ``UsageType`` in ``[period_start, period_end)``."""
        events = self._usage_repository.find_uninvoiced(
            tenant_id,
            since_bucket=hour_bucket(period_start),
            until_bucket=hour_bucket(period_end),
        )
        totals: dict[UsageType, int] = defaultdict(int)
        for event in events:
            totals[event.usage_type] += event.quantity
        return dict(totals)


__all__ = ["UsageAggregator", "UsageRepositoryPort", "hour_bucket"]
