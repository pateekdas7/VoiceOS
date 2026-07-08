"""Unit tests for the Business Intelligence Platform (Sprint-024, V5 Ch21).

All tests run fully in-process — no live Postgres required (Phase 1).
Repository interactions use small in-memory fake doubles.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.models.bi import BIFactDaily
from src.libs.contracts.models.billing import UsageEvent, UsageType
from src.libs.contracts.primitives import TenantId
from src.services.bi_platform.benchmarking import CrossTenantBenchmarking, UnknownBenchmarkMetricError
from src.services.bi_platform.executive_dashboard import ExecutiveDashboard
from src.services.bi_platform.forecasting import ARIMA_MIN_HISTORY_DAYS, ForecastingEngine
from src.services.bi_platform.service import BIPlatformService
from src.services.bi_platform.warehouse import BIWarehouse
from src.services.metering.aggregator import hour_bucket

_TENANT_A = TenantId("tenant-a")
_TENANT_B = TenantId("tenant-b")
_DAY = date(2026, 7, 1)


class _FakeBIRepository:
    def __init__(self) -> None:
        self._surrogate_keys: dict[str, str] = {}
        self.facts: dict[tuple[str, date], BIFactDaily] = {}

    def get_or_create_surrogate_key(self, tenant_id: TenantId) -> str:
        if tenant_id not in self._surrogate_keys:
            self._surrogate_keys[tenant_id] = f"surrogate-{uuid.uuid4()}"
        return self._surrogate_keys[tenant_id]

    def upsert_fact_daily(self, fact: BIFactDaily) -> BIFactDaily:
        self.facts[(fact.tenant_surrogate_key, fact.day)] = fact
        return fact

    def find_fact_for_tenant(self, tenant_id: TenantId, day: date) -> BIFactDaily | None:
        key = self._surrogate_keys.get(tenant_id)
        if key is None:
            return None
        return self.facts.get((key, day))

    def find_all_facts_for_day(self, day: date) -> tuple[BIFactDaily, ...]:
        return tuple(fact for (_, fact_day), fact in self.facts.items() if fact_day == day)

    def find_facts_for_surrogate(self, surrogate_key: str, start: date, end: date) -> tuple[BIFactDaily, ...]:
        return tuple(
            fact for (key, fact_day), fact in self.facts.items() if key == surrogate_key and start <= fact_day <= end
        )


class _FakeAnalyticsDailyRepository:
    def __init__(self) -> None:
        self.rollups: dict[tuple[str, date], AnalyticsDailyRollup] = {}

    def find_for_day(
        self, tenant_id: TenantId, day: date, campaign_id: object | None = None
    ) -> AnalyticsDailyRollup | None:
        return self.rollups.get((tenant_id, day))


class _FakeUsageRepository:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    def find_all_between(self, tenant_id: TenantId, since_bucket: str, until_bucket: str) -> tuple[UsageEvent, ...]:
        return tuple(
            e for e in self.events if e.tenant_id == tenant_id and since_bucket <= e.occurred_at_bucket <= until_bucket
        )


def _rollup(tenant_id: TenantId, day: date, ptp_rate: float, recovery_rate: float) -> AnalyticsDailyRollup:
    return AnalyticsDailyRollup(
        analytics_daily_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        day=day,
        calls_completed=10,
        ptp_count=3,
        ptp_rate=ptp_rate,
        avg_duration_ms=30_000,
        recovery_rate=recovery_rate,
        computed_at=datetime.now(UTC),
    )


def _usage_event(tenant_id: TenantId, usage_type: UsageType, quantity: int, when: datetime) -> UsageEvent:
    return UsageEvent(
        usage_event_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        usage_type=usage_type,
        quantity=quantity,
        unit_cost_minor=1,
        total_cost_minor=quantity,
        currency="INR",
        occurred_at_bucket=hour_bucket(when),
    )


class TestBIWarehouse:
    def test_refresh_aggregates_analytics_billing_usage(self) -> None:
        bi_repo = _FakeBIRepository()
        analytics_repo = _FakeAnalyticsDailyRepository()
        usage_repo = _FakeUsageRepository()
        analytics_repo.rollups[(_TENANT_A, _DAY)] = _rollup(_TENANT_A, _DAY, ptp_rate=0.3, recovery_rate=0.4)
        usage_repo.events = [
            _usage_event(_TENANT_A, UsageType.CALL_MINUTE, 100, datetime(2026, 7, 1, 10, tzinfo=UTC)),
            _usage_event(_TENANT_A, UsageType.STT_TOKEN, 5000, datetime(2026, 7, 1, 11, tzinfo=UTC)),
        ]
        warehouse = BIWarehouse(bi_repo, analytics_repo, usage_repo)

        fact = warehouse.refresh(_TENANT_A, _DAY)

        assert fact.usage_call_minutes == 100
        assert fact.usage_stt_tokens == 5000
        assert fact.revenue_minor == 100 + 5000
        assert fact.ptp_rate == 0.3
        assert fact.recovery_rate == 0.4
        assert fact.compliance_score == 1.0  # default compliance port
        assert bi_repo.facts[(bi_repo.get_or_create_surrogate_key(_TENANT_A), _DAY)] == fact

    def test_refresh_is_idempotent_upsert(self) -> None:
        bi_repo = _FakeBIRepository()
        warehouse = BIWarehouse(bi_repo, _FakeAnalyticsDailyRepository(), _FakeUsageRepository())

        warehouse.refresh(_TENANT_A, _DAY)
        warehouse.refresh(_TENANT_A, _DAY)

        assert len(bi_repo.facts) == 1


class TestForecastingEngine:
    def test_forecast_returns_non_zero_result_for_test_data(self) -> None:
        bi_repo = _FakeBIRepository()
        surrogate = bi_repo.get_or_create_surrogate_key(_TENANT_A)
        for i in range(10):
            bi_repo.facts[(surrogate, _DAY - timedelta(days=10 - i))] = BIFactDaily(
                fact_daily_id=str(uuid.uuid4()),
                tenant_surrogate_key=surrogate,
                day=_DAY - timedelta(days=10 - i),
                recovery_rate=0.3 + i * 0.01,
                refreshed_at=datetime.now(UTC),
            )
        engine = ForecastingEngine(bi_repo)

        result = engine.forecast_collections_recovery(_TENANT_A, horizon_days=7, as_of=_DAY)

        assert result.model == "exponential_smoothing"
        assert len(result.points) == 7
        assert all(p.predicted_recovery_rate > 0 for p in result.points)

    def test_uses_linear_trend_proxy_with_long_history(self) -> None:
        bi_repo = _FakeBIRepository()
        surrogate = bi_repo.get_or_create_surrogate_key(_TENANT_A)
        for i in range(ARIMA_MIN_HISTORY_DAYS):
            bi_repo.facts[(surrogate, _DAY - timedelta(days=ARIMA_MIN_HISTORY_DAYS - i))] = BIFactDaily(
                fact_daily_id=str(uuid.uuid4()),
                tenant_surrogate_key=surrogate,
                day=_DAY - timedelta(days=ARIMA_MIN_HISTORY_DAYS - i),
                recovery_rate=0.2 + i * 0.001,
                refreshed_at=datetime.now(UTC),
            )
        engine = ForecastingEngine(bi_repo)

        result = engine.forecast_collections_recovery(_TENANT_A, horizon_days=5, as_of=_DAY)

        assert result.model == "arima_proxy_linear_trend"

    def test_no_history_returns_zeroed_forecast(self) -> None:
        engine = ForecastingEngine(_FakeBIRepository())
        result = engine.forecast_collections_recovery(_TENANT_A, horizon_days=3, as_of=_DAY)
        assert all(p.predicted_recovery_rate == 0.0 for p in result.points)


class TestCrossTenantBenchmarking:
    def test_get_benchmark_returns_percentile_without_other_tenant_ids(self) -> None:
        bi_repo = _FakeBIRepository()
        for tenant, revenue in ((_TENANT_A, 1000), (_TENANT_B, 5000)):
            surrogate = bi_repo.get_or_create_surrogate_key(tenant)
            bi_repo.facts[(surrogate, _DAY)] = BIFactDaily(
                fact_daily_id=str(uuid.uuid4()),
                tenant_surrogate_key=surrogate,
                day=_DAY,
                revenue_minor=revenue,
                refreshed_at=datetime.now(UTC),
            )
        benchmarking = CrossTenantBenchmarking(bi_repo)

        result = benchmarking.get_benchmark("revenue_minor", _TENANT_A, _DAY)

        assert result.tenant_value == 1000
        assert result.percentile == 50.0  # tenant_a is the lower of 2 values
        assert result.sample_size == 2
        # No tenant identifier (raw tenant_id or the other tenant's surrogate key) is exposed.
        assert "tenant_id" not in result.__dict__
        assert str(_TENANT_B) not in repr(result)

    def test_rejects_unknown_metric(self) -> None:
        benchmarking = CrossTenantBenchmarking(_FakeBIRepository())
        try:
            benchmarking.get_benchmark("not_a_real_metric", _TENANT_A, _DAY)
            raise AssertionError("expected UnknownBenchmarkMetricError")
        except UnknownBenchmarkMetricError:
            pass

    def test_empty_sample_returns_zero(self) -> None:
        benchmarking = CrossTenantBenchmarking(_FakeBIRepository())
        result = benchmarking.get_benchmark("revenue_minor", _TENANT_A, _DAY)
        assert result.sample_size == 0
        assert result.percentile == 0.0


class TestExecutiveDashboard:
    def test_get_executive_summary_returns_all_required_fields(self) -> None:
        bi_repo = _FakeBIRepository()
        surrogate = bi_repo.get_or_create_surrogate_key(_TENANT_A)
        bi_repo.facts[(surrogate, _DAY)] = BIFactDaily(
            fact_daily_id=str(uuid.uuid4()),
            tenant_surrogate_key=surrogate,
            day=_DAY,
            revenue_minor=100_000,
            usage_call_minutes=1000,
            recovery_rate=0.4,
            compliance_score=0.95,
            refreshed_at=datetime.now(UTC),
        )
        dashboard = ExecutiveDashboard(bi_repo)

        summary = dashboard.get_executive_summary(_TENANT_A, _DAY)

        assert summary.gross_recovery_rate == 0.4
        assert summary.cost_per_conversation_minor == 100
        assert summary.compliance_score == 0.95
        assert summary.slo_attainment == 1.0
        assert summary.mom_improvement == 0.4  # no prior-month fact -> improvement = full current rate

    def test_missing_fact_returns_zeroed_summary(self) -> None:
        dashboard = ExecutiveDashboard(_FakeBIRepository())
        summary = dashboard.get_executive_summary(_TENANT_A, _DAY)
        assert summary.gross_recovery_rate == 0.0
        assert summary.cost_per_conversation_minor == 0


class TestBIPlatformService:
    def test_facade_delegates_to_all_four_components(self) -> None:
        bi_repo = _FakeBIRepository()
        analytics_repo = _FakeAnalyticsDailyRepository()
        usage_repo = _FakeUsageRepository()
        service = BIPlatformService(
            BIWarehouse(bi_repo, analytics_repo, usage_repo),
            ForecastingEngine(bi_repo),
            CrossTenantBenchmarking(bi_repo),
            ExecutiveDashboard(bi_repo),
        )

        fact = service.refresh(_TENANT_A, _DAY)
        assert fact is not None

        forecast = service.forecast_collections_recovery(_TENANT_A, horizon_days=3)
        assert forecast.horizon_days == 3

        benchmark = service.get_benchmark("revenue_minor", _TENANT_A, _DAY)
        assert benchmark.sample_size == 1

        summary = service.get_executive_summary(_TENANT_A, _DAY)
        assert summary.tenant_id == str(_TENANT_A)
