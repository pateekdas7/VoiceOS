"""Result types for the Operational Analytics service (V7 Ch21, Sprint-027).

Architecture: V7 Ch21 (Operational Analytics -- combined technical +
business KPI synthesis, unit economics, MTTR/MTBF).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("DateRange.end must not precede DateRange.start")


@dataclass(frozen=True)
class TechnicalKPIs:
    """Technical operations KPIs synthesized from observability data (V7 Ch21)."""

    availability: float
    """Fraction of calls completed without a system_error outcome (0.0-1.0)."""
    mttr_seconds: float
    """Mean Time To Resolve, across incidents in the scoring window."""
    mtbf_seconds: float
    """Mean Time Between Failures, across incidents in the scoring window."""
    slo_attainment: float
    """Fraction of the scoring window during which the first-audio SLO was met (0.0-1.0)."""


@dataclass(frozen=True)
class BusinessKPIs:
    """Business KPIs for the same scoring window (V7 Ch21)."""

    revenue_minor: int
    collections_recovered_minor: int
    cost_per_call_minor: int


@dataclass(frozen=True)
class OperatorScorecard:
    """Output of ``OpsAnalytics.get_operator_scorecard()`` -- combines both KPI sets."""

    date_range: DateRange
    technical_kpis: TechnicalKPIs
    business_kpis: BusinessKPIs

    def as_dict(self) -> dict[str, object]:
        """Dict view with the literal ``technical_kpis``/``business_kpis`` keys (Sprint-027.md AC)."""
        return {"technical_kpis": self.technical_kpis, "business_kpis": self.business_kpis}


@dataclass(frozen=True)
class UnitEconomicsReport:
    """Output of ``OpsAnalytics.unit_economics()`` -- V7 Ch21 unit economics."""

    tenant_id: str
    cost_per_conversation_minor: int
    revenue_per_conversation_minor: int
    margin_per_conversation_minor: int
    break_even_calls: int
    """Calls needed this period for cumulative revenue to cover cumulative cost; 0 if already covered."""


__all__ = ["BusinessKPIs", "DateRange", "OperatorScorecard", "TechnicalKPIs", "UnitEconomicsReport"]
