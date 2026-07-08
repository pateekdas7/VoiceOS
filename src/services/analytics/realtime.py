"""RealtimeAnalytics — live dashboard data feed (V5 Ch11, Sprint-024.md).

Produces JSON-serializable snapshots suitable for an SSE ``data:`` frame or
WebSocket message. The actual SSE/WebSocket transport (a bound HTTP
listener) is Sprint-026 scope (K8s/Helm — see CPU_NODE_STATE.md §8.1, same
"library class now, HTTP wiring later" precedent every service in this repo
follows); this class is the data-producing half a future transport layer
calls into unchanged.

Architecture: V5 Ch11 (Analytics Platform — RealtimeAnalytics).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from src.libs.contracts.primitives import CampaignId, TenantId

from .call_analytics import CallAnalytics
from .campaign_analytics import CampaignAnalytics


class RealtimeAnalytics:
    """Produces live dashboard snapshots from :class:`CallAnalytics`/:class:`CampaignAnalytics`."""

    def __init__(self, call_analytics: CallAnalytics, campaign_analytics: CampaignAnalytics) -> None:
        self._call_analytics = call_analytics
        self._campaign_analytics = campaign_analytics

    def snapshot(
        self,
        tenant_id: TenantId,
        window_start: datetime,
        window_end: datetime,
        campaign_id: CampaignId | None = None,
    ) -> dict[str, Any]:
        """One JSON-serializable dashboard snapshot."""
        data: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "as_of": datetime.now(UTC).isoformat(),
            "calls_completed": len(self._call_analytics.dispositions_between(tenant_id, window_start, window_end)),
            "outcome_distribution": self._call_analytics.outcome_distribution(tenant_id, window_start, window_end),
            "average_duration_ms": self._call_analytics.average_duration_ms(tenant_id, window_start, window_end),
            "contactability_rate": self._call_analytics.contactability_rate(tenant_id, window_start, window_end),
            "recovery_rate": self._call_analytics.recovery_rate(tenant_id, window_start, window_end),
        }
        if campaign_id is not None:
            data["campaign_id"] = str(campaign_id)
            data["ptp_rate"] = self._campaign_analytics.ptp_rate(tenant_id, campaign_id)
        return data

    def stream(
        self,
        tenant_id: TenantId,
        window_start: datetime,
        window_end: datetime,
        campaign_id: CampaignId | None = None,
        max_iterations: int = 1,
        sleep_fn: Callable[[float], None] | None = None,
        interval_seconds: float = 5.0,
    ) -> Iterator[dict[str, Any]]:
        """Yield ``max_iterations`` snapshots, sleeping ``interval_seconds`` between
        (an SSE/WebSocket transport wraps this in its own send loop — see module
        docstring). ``max_iterations`` bounds the generator for tests/step-driven use."""
        for i in range(max_iterations):
            yield self.snapshot(tenant_id, window_start, window_end, campaign_id)
            if sleep_fn is not None and i < max_iterations - 1:
                sleep_fn(interval_seconds)


__all__ = ["RealtimeAnalytics"]
