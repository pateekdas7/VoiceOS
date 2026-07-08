"""ForecastingEngine — collections recovery time-series prediction (V5 Ch21, Sprint-024.md).

Two models, selected by available history length (V5 Ch9.md: "simple
exponential smoothing as baseline; ARIMA for tenants with >=90 days of
history"):

- **Simple exponential smoothing** (< 90 days of ``bi_facts.fact_daily``
  history): the standard SES recurrence, projected flat over the horizon —
  the textbook baseline for short/no-trend series.
- **Linear-trend proxy** (>= 90 days): Volume 5 Ch21 names "ARIMA" only in
  the abstract (no order/parameters specified anywhere in the architecture
  docs or Sprint-024.md) — implementing real ARIMA would require a new
  heavy statistical dependency (statsmodels) for a model the sprint
  doesn't test the internals of. A least-squares linear trend extrapolation
  is used as a documented, dependency-free stand-in; ``ForecastResult.model``
  is labeled ``"arima_proxy_linear_trend"`` (not ``"arima"``) so this
  substitution is never silently reported as the real thing — see
  CHANGELOG.md Sprint-024 deviations.

Architecture: V5 Ch21 (Business Intelligence Platform — ForecastingEngine).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

from src.libs.contracts.models.bi import BIFactDaily
from src.libs.contracts.primitives import TenantId

from .models import ForecastPoint, ForecastResult

ARIMA_MIN_HISTORY_DAYS = 90
DEFAULT_SES_ALPHA = 0.3


class BIRepositoryPort(Protocol):
    def get_or_create_surrogate_key(self, tenant_id: TenantId) -> str: ...

    def find_facts_for_surrogate(self, surrogate_key: str, start: date, end: date) -> tuple[BIFactDaily, ...]: ...


class ForecastingEngine:
    """Predicts a tenant's collections-recovery-rate trajectory over a horizon."""

    def __init__(self, bi_repository: BIRepositoryPort) -> None:
        self._bi_repository = bi_repository

    def forecast_collections_recovery(
        self, tenant_id: TenantId, horizon_days: int, as_of: date | None = None
    ) -> ForecastResult:
        end = as_of or date.today()
        start = end - timedelta(days=365)
        surrogate_key = self._bi_repository.get_or_create_surrogate_key(tenant_id)
        history = self._bi_repository.find_facts_for_surrogate(surrogate_key, start, end)
        recovery_rates = [fact.recovery_rate for fact in history]

        if len(recovery_rates) >= ARIMA_MIN_HISTORY_DAYS:
            model = "arima_proxy_linear_trend"
            predicted = self._linear_trend_forecast(recovery_rates, horizon_days)
        else:
            model = "exponential_smoothing"
            predicted = self._exponential_smoothing_forecast(recovery_rates, horizon_days)

        points = tuple(
            ForecastPoint(day=end + timedelta(days=i + 1), predicted_recovery_rate=value)
            for i, value in enumerate(predicted)
        )
        return ForecastResult(tenant_id=str(tenant_id), horizon_days=horizon_days, model=model, points=points)

    @staticmethod
    def _exponential_smoothing_forecast(
        history: list[float], horizon_days: int, alpha: float = DEFAULT_SES_ALPHA
    ) -> list[float]:
        if not history:
            return [0.0] * horizon_days
        level = history[0]
        for value in history[1:]:
            level = alpha * value + (1 - alpha) * level
        return [level] * horizon_days

    @staticmethod
    def _linear_trend_forecast(history: list[float], horizon_days: int) -> list[float]:
        n = len(history)
        xs = list(range(n))
        mean_x = sum(xs) / n
        mean_y = sum(history) / n
        denominator = sum((x - mean_x) ** 2 for x in xs) or 1.0
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, history, strict=True)) / denominator
        intercept = mean_y - slope * mean_x
        return [max(0.0, min(1.0, intercept + slope * (n + i))) for i in range(horizon_days)]


__all__ = ["ARIMA_MIN_HISTORY_DAYS", "ForecastingEngine"]
