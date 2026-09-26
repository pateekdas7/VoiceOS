"""CapacityPlanner -- implements Volume 7 Ch.12's previously-unimplemented forecast()/headroom().

Uses an honestly-labeled linear-trend projection over historical Prometheus/
Thanos series -- the same "arima_proxy_linear_trend" honesty precedent
``bi_platform.forecasting.ForecastingEngine`` already established for
business forecasting (never silently claims to be real ARIMA). Confidence
is derived from how much history actually backs the projection, not from
the reasoning model's own opinion -- forecasting math stays plain code
(ADR-006 Sec 3.2.4), the reasoning model only narrates the result
(``report_generator.generate_capacity_report``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from src.services.ops_intelligence.models import ConfidenceLevel

VALID_HORIZONS_DAYS = (30, 90, 365)
_MIN_POINTS_FOR_MEDIUM_CONFIDENCE = 5
_MIN_POINTS_FOR_HIGH_CONFIDENCE = 14


class MetricsHistoryPort(Protocol):
    """Prometheus/Thanos read port for capacity forecasting."""

    def historical_series(self, resource: str, *, days: int) -> tuple[tuple[datetime, float], ...]:
        """Daily (timestamp, usage) samples over the trailing ``days``, oldest first."""
        ...

    def current_capacity(self, resource: str) -> float:
        """Total provisioned capacity for ``resource`` (e.g. total fleet VRAM MB)."""
        ...


@dataclass(frozen=True)
class CapacityForecast:
    forecast_id: str
    tenant_id: str | None
    resource: str
    horizon_days: int
    forecast_data: dict[str, object]
    headroom_pct: float
    confidence: ConfidenceLevel
    generated_at: datetime


class CapacityForecastSink(Protocol):
    def create(self, forecast: CapacityForecast) -> CapacityForecast: ...


class CapacityPlanner:
    """Implements Vol.7 Ch.12's ``CapacityPlanning`` interface: ``forecast()``/``headroom()``."""

    def __init__(self, metrics_history: MetricsHistoryPort, repository: CapacityForecastSink | None = None) -> None:
        self._metrics_history = metrics_history
        self._repository = repository

    def forecast(self, resource: str, horizon_days: int, *, tenant_id: str | None = None) -> CapacityForecast:
        if horizon_days not in VALID_HORIZONS_DAYS:
            raise ValueError(f"horizon_days must be one of {VALID_HORIZONS_DAYS}, got {horizon_days}")

        series = self._metrics_history.historical_series(resource, days=min(horizon_days, 365))
        capacity = self._metrics_history.current_capacity(resource)

        if len(series) < 2 or capacity <= 0:
            # Not enough history (or no known capacity ceiling) to project --
            # be honest about that rather than fabricating a trend line.
            forecast = CapacityForecast(
                forecast_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                resource=resource,
                horizon_days=horizon_days,
                forecast_data={"method": "insufficient_history", "sample_count": len(series)},
                headroom_pct=100.0 if capacity > 0 else 0.0,
                confidence=ConfidenceLevel.LOW,
                generated_at=datetime.now(UTC),
            )
        else:
            slope_per_day, intercept = _linear_regression(series)
            projected_usage = intercept + slope_per_day * (len(series) + horizon_days)
            headroom_pct = max(-100.0, min(100.0, ((capacity - projected_usage) / capacity) * 100.0))
            confidence = _confidence_for_sample_count(len(series))
            forecast = CapacityForecast(
                forecast_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                resource=resource,
                horizon_days=horizon_days,
                forecast_data={
                    "method": "arima_proxy_linear_trend",
                    "sample_count": len(series),
                    "slope_per_day": slope_per_day,
                    "projected_usage": projected_usage,
                    "capacity": capacity,
                },
                headroom_pct=headroom_pct,
                confidence=confidence,
                generated_at=datetime.now(UTC),
            )

        if self._repository is not None:
            self._repository.create(forecast)
        return forecast

    def headroom(self, resource: str) -> float:
        """Current (non-forecast) headroom percentage for ``resource`` right now."""
        capacity = self._metrics_history.current_capacity(resource)
        if capacity <= 0:
            return 0.0
        series = self._metrics_history.historical_series(resource, days=1)
        if not series:
            return 100.0
        current_usage = series[-1][1]
        return max(-100.0, min(100.0, ((capacity - current_usage) / capacity) * 100.0))


def _linear_regression(series: tuple[tuple[datetime, float], ...]) -> tuple[float, float]:
    """Ordinary least squares over index-as-x (daily samples) -- returns (slope_per_day, intercept)."""
    n = len(series)
    xs = list(range(n))
    ys = [value for _, value in series]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = numerator / denominator if denominator != 0 else 0.0
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _confidence_for_sample_count(count: int) -> ConfidenceLevel:
    if count >= _MIN_POINTS_FOR_HIGH_CONFIDENCE:
        return ConfidenceLevel.HIGH
    if count >= _MIN_POINTS_FOR_MEDIUM_CONFIDENCE:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


__all__ = ["VALID_HORIZONS_DAYS", "CapacityForecast", "CapacityForecastSink", "CapacityPlanner", "MetricsHistoryPort"]
