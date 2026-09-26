"""Unit tests for CapacityPlanner (ADR-006 Sec 1.5 item 6, Vol.7 Ch.12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.services.ops_intelligence.models import ConfidenceLevel
from src.services.ops_intelligence.reasoning.capacity_planner import CapacityForecast, CapacityPlanner

BASE = datetime(2026, 7, 1, tzinfo=UTC)


class _FakeMetricsHistory:
    def __init__(self, series: tuple[tuple[datetime, float], ...], capacity: float) -> None:
        self._series = series
        self._capacity = capacity

    def historical_series(self, resource: str, *, days: int) -> tuple[tuple[datetime, float], ...]:
        return self._series[-days:] if days < len(self._series) else self._series

    def current_capacity(self, resource: str) -> float:
        return self._capacity


class _FakeRepository:
    def __init__(self) -> None:
        self.rows: list[CapacityForecast] = []

    def create(self, forecast: CapacityForecast) -> CapacityForecast:
        self.rows.append(forecast)
        return forecast


def _rising_series(days: int, start: float, per_day: float) -> tuple[tuple[datetime, float], ...]:
    return tuple((BASE + timedelta(days=i), start + per_day * i) for i in range(days))


class TestForecast:
    def test_invalid_horizon_raises(self) -> None:
        planner = CapacityPlanner(_FakeMetricsHistory((), 100.0))
        with pytest.raises(ValueError, match="horizon_days must be one of"):
            planner.forecast("gpu", 45)

    def test_insufficient_history_is_low_confidence(self) -> None:
        planner = CapacityPlanner(_FakeMetricsHistory(_rising_series(1, 50.0, 1.0), 100.0))
        forecast = planner.forecast("gpu", 30)
        assert forecast.confidence == ConfidenceLevel.LOW
        assert forecast.forecast_data["method"] == "insufficient_history"

    def test_zero_capacity_is_low_confidence_not_division_error(self) -> None:
        planner = CapacityPlanner(_FakeMetricsHistory(_rising_series(20, 50.0, 1.0), 0.0))
        forecast = planner.forecast("gpu", 30)
        assert forecast.confidence == ConfidenceLevel.LOW
        assert forecast.headroom_pct == 0.0

    def test_rising_usage_reduces_headroom(self) -> None:
        planner = CapacityPlanner(_FakeMetricsHistory(_rising_series(20, 50.0, 2.0), 100.0))
        forecast = planner.forecast("gpu", 30)
        assert forecast.forecast_data["method"] == "arima_proxy_linear_trend"
        assert forecast.headroom_pct < 100.0

    def test_flat_usage_keeps_high_headroom(self) -> None:
        flat_series = tuple((BASE + timedelta(days=i), 10.0) for i in range(20))
        planner = CapacityPlanner(_FakeMetricsHistory(flat_series, 100.0))
        forecast = planner.forecast("gpu", 30)
        assert forecast.headroom_pct == pytest.approx(90.0, abs=1.0)

    def test_confidence_scales_with_sample_count(self) -> None:
        planner_few = CapacityPlanner(_FakeMetricsHistory(_rising_series(6, 10.0, 1.0), 100.0))
        planner_many = CapacityPlanner(_FakeMetricsHistory(_rising_series(20, 10.0, 1.0), 100.0))
        assert planner_few.forecast("gpu", 30).confidence == ConfidenceLevel.MEDIUM
        assert planner_many.forecast("gpu", 30).confidence == ConfidenceLevel.HIGH

    def test_headroom_never_exceeds_100_or_drops_below_negative_100(self) -> None:
        extreme_series = _rising_series(20, 10.0, 1000.0)
        planner = CapacityPlanner(_FakeMetricsHistory(extreme_series, 100.0))
        forecast = planner.forecast("gpu", 365)
        assert -100.0 <= forecast.headroom_pct <= 100.0

    def test_forecast_persists_to_repository_when_wired(self) -> None:
        repo = _FakeRepository()
        planner = CapacityPlanner(_FakeMetricsHistory(_rising_series(20, 10.0, 1.0), 100.0), repository=repo)
        planner.forecast("gpu", 30)
        assert len(repo.rows) == 1

    def test_forecast_works_without_repository(self) -> None:
        planner = CapacityPlanner(_FakeMetricsHistory(_rising_series(20, 10.0, 1.0), 100.0))
        forecast = planner.forecast("gpu", 30)  # must not raise
        assert forecast.resource == "gpu"


class TestHeadroom:
    def test_current_headroom_reflects_latest_sample(self) -> None:
        series = _rising_series(5, 10.0, 5.0)  # last value = 30.0
        planner = CapacityPlanner(_FakeMetricsHistory(series, 100.0))
        assert planner.headroom("gpu") == pytest.approx(70.0)

    def test_zero_capacity_headroom_is_zero(self) -> None:
        planner = CapacityPlanner(_FakeMetricsHistory((), 0.0))
        assert planner.headroom("gpu") == 0.0

    def test_no_history_defaults_to_full_headroom(self) -> None:
        planner = CapacityPlanner(_FakeMetricsHistory((), 100.0))
        assert planner.headroom("gpu") == 100.0
