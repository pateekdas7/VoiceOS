"""UnitEconomics -- cost/margin per conversation, revenue attribution (V7 Ch21).

Joins the Cost Optimization service's per-conversation cost breakdown
(Sprint-027's own ``CostOptimizer.cost_per_conversation()``) with a
revenue-attribution port (a real deployment backs this with the Billing
Platform's invoiced-usage figures, Sprint-024) to produce break-even/
margin figures -- V7 Ch21's own definition of "unit economics."

Architecture: V7 Ch21 (Operational Analytics -- Unit Economics).
"""

from __future__ import annotations

from typing import Protocol

from src.libs.contracts.primitives import TenantId
from src.services.cost_optimizer.models import DateRange as CostDateRange

from .models import DateRange, UnitEconomicsReport


class RevenuePort(Protocol):
    """Structural port over revenue attribution for a tenant/window."""

    def revenue_minor(self, tenant_id: TenantId, date_range: DateRange) -> int: ...
    def call_count(self, tenant_id: TenantId, date_range: DateRange) -> int: ...


class CostPort(Protocol):
    """Structural port over ``CostOptimizer.cost_per_conversation()``."""

    def cost_per_conversation(self, tenant_id: TenantId, date_range: CostDateRange) -> object: ...

    """Returns a ``CostReport``-shaped object exposing ``.total_cost_minor``."""


class UnitEconomics:
    """Computes cost/margin per conversation and break-even call count for a tenant/window."""

    def __init__(self, cost_source: CostPort, revenue_source: RevenuePort) -> None:
        self._cost_source = cost_source
        self._revenue_source = revenue_source

    def unit_economics(self, tenant_id: TenantId, date_range: DateRange) -> UnitEconomicsReport:
        cost_range = CostDateRange(start=date_range.start, end=date_range.end)
        cost_report = self._cost_source.cost_per_conversation(tenant_id, cost_range)
        total_cost_minor = int(cost_report.total_cost_minor)  # type: ignore[attr-defined]

        call_count = self._revenue_source.call_count(tenant_id, date_range)
        revenue_minor = self._revenue_source.revenue_minor(tenant_id, date_range)

        cost_per_conversation = total_cost_minor // call_count if call_count else 0
        revenue_per_conversation = revenue_minor // call_count if call_count else 0
        margin_per_conversation = revenue_per_conversation - cost_per_conversation

        if margin_per_conversation >= 0 or cost_per_conversation == 0:
            break_even_calls = 0
        else:
            # Fixed-cost-recovery style estimate: how many additional calls at the
            # current per-call revenue rate would it take to cover the current
            # per-call cost shortfall. Never negative, never a fractional call.
            shortfall = cost_per_conversation - revenue_per_conversation
            break_even_calls = (
                -(-shortfall // max(1, revenue_per_conversation)) if revenue_per_conversation > 0 else call_count
            )

        return UnitEconomicsReport(
            tenant_id=str(tenant_id),
            cost_per_conversation_minor=cost_per_conversation,
            revenue_per_conversation_minor=revenue_per_conversation,
            margin_per_conversation_minor=margin_per_conversation,
            break_even_calls=int(break_even_calls),
        )


__all__ = ["CostPort", "RevenuePort", "UnitEconomics"]
