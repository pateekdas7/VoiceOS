"""TechnicalKPISynthesizer -- MTTR, MTBF, SLO attainment from observability data (V7 Ch21).

Takes an injected ``ObservabilityPort`` (a real deployment backs this with
Prometheus range queries over the recording rules in
``monitoring/prometheus/recording_rules.yml`` and the incident timeline
tracked by ``IncidentResponse``, Sprint-020) rather than importing
Prometheus/monitoring config directly -- keeps this service's boundary the
same "communicate through a narrow port" discipline as every other
service in this codebase.

Architecture: V7 Ch21 (Operational Analytics).
"""

from __future__ import annotations

from typing import Protocol

from .models import DateRange, TechnicalKPIs


class ObservabilityPort(Protocol):
    """Structural port over whatever supplies raw technical-KPI inputs for a window."""

    def availability_ratio(self, date_range: DateRange) -> float: ...
    def incident_resolution_seconds(self, date_range: DateRange) -> tuple[float, ...]: ...

    """Duration of each incident's detection-to-resolution in the window, in seconds."""

    def time_between_failures_seconds(self, date_range: DateRange) -> tuple[float, ...]: ...

    """Gap between consecutive incident starts in the window, in seconds."""

    def slo_attainment_ratio(self, date_range: DateRange) -> float: ...


class TechnicalKPISynthesizer:
    """Synthesizes availability/MTTR/MTBF/SLO-attainment for a scoring window."""

    def __init__(self, observability: ObservabilityPort) -> None:
        self._observability = observability

    def synthesize(self, date_range: DateRange) -> TechnicalKPIs:
        resolution_times = self._observability.incident_resolution_seconds(date_range)
        failure_gaps = self._observability.time_between_failures_seconds(date_range)

        mttr = sum(resolution_times) / len(resolution_times) if resolution_times else 0.0
        mtbf = sum(failure_gaps) / len(failure_gaps) if failure_gaps else 0.0

        return TechnicalKPIs(
            availability=self._observability.availability_ratio(date_range),
            mttr_seconds=mttr,
            mtbf_seconds=mtbf,
            slo_attainment=self._observability.slo_attainment_ratio(date_range),
        )


__all__ = ["ObservabilityPort", "TechnicalKPISynthesizer"]
