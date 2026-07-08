"""Unit tests for the Analytics Platform (Sprint-024, V5 Ch11).

All tests run fully in-process — no live Postgres required (Phase 1).
Repository interactions use small in-memory fake doubles.

Required named test (Sprint-024.md):
    test_campaign_ptp_rate_calculation — 10 calls, 3 PTPs -> ptp_rate = 0.30
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.libs.contracts.models.analytics import AnalyticsDailyRollup, CallDisposition
from src.libs.contracts.models.campaign import CampaignResult
from src.libs.contracts.primitives import CallId, CampaignId, CustomerId, TenantId
from src.services.analytics.aggregation import DailyAggregationJob
from src.services.analytics.call_analytics import CallAnalytics
from src.services.analytics.campaign_analytics import CampaignAnalytics
from src.services.analytics.realtime import RealtimeAnalytics
from src.services.analytics.service import AnalyticsService

_TENANT = TenantId("tenant-a")
_CAMPAIGN = CampaignId("campaign-1")
_DAY = datetime(2026, 7, 1, tzinfo=UTC)


class _FakeCallDispositionRepository:
    def __init__(self) -> None:
        self.dispositions: list[CallDisposition] = []

    def find_between(self, tenant_id: TenantId, start: datetime, end: datetime) -> tuple[CallDisposition, ...]:
        return tuple(d for d in self.dispositions if start <= d.dispositioned_at < end)


class _FakeCampaignResultRepository:
    def __init__(self) -> None:
        self.results: list[CampaignResult] = []

    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId) -> tuple[CampaignResult, ...]:
        return tuple(r for r in self.results if r.campaign_id == campaign_id)

    def find_between(self, tenant_id: TenantId, start: datetime, end: datetime) -> tuple[CampaignResult, ...]:
        return tuple(r for r in self.results if start <= r.completed_at < end)


class _FakeAnalyticsDailyRepository:
    def __init__(self) -> None:
        self.rows: list[AnalyticsDailyRollup] = []

    def upsert(self, rollup: AnalyticsDailyRollup) -> AnalyticsDailyRollup:
        self.rows.append(rollup)
        return rollup


def _disposition(outcome_code: str, duration_ms: int, at: datetime) -> CallDisposition:
    return CallDisposition(
        disposition_id=f"d-{outcome_code}-{at.isoformat()}",
        tenant_id=_TENANT,
        call_id=CallId("call-1"),
        customer_id=CustomerId("cust-1"),
        loan_account_id="loan-1",
        outcome_code=outcome_code,
        duration_ms=duration_ms,
        dispositioned_at=at,
    )


def _campaign_result(ptp_created: bool, outcome_code: str, at: datetime) -> CampaignResult:
    return CampaignResult(
        campaign_result_id=f"r-{outcome_code}-{at.isoformat()}-{ptp_created}",
        campaign_id=_CAMPAIGN,
        tenant_id=_TENANT,
        customer_id="cust-1",
        outcome_code=outcome_code,
        ptp_created=ptp_created,
        completed_at=at,
        created_at=at,
    )


class TestCallAnalytics:
    def test_outcome_distribution_and_average_duration(self) -> None:
        repo = _FakeCallDispositionRepository()
        repo.dispositions = [
            _disposition("PTP_MADE", 60_000, _DAY),
            _disposition("NOT_REACHABLE", 10_000, _DAY + timedelta(hours=1)),
        ]
        analytics = CallAnalytics(repo)

        distribution = analytics.outcome_distribution(_TENANT, _DAY, _DAY + timedelta(days=1))

        assert distribution == {"PTP_MADE": 1, "NOT_REACHABLE": 1}
        assert analytics.average_duration_ms(_TENANT, _DAY, _DAY + timedelta(days=1)) == 35_000

    def test_contactability_and_recovery_rate(self) -> None:
        repo = _FakeCallDispositionRepository()
        repo.dispositions = [
            _disposition("PTP_MADE", 1000, _DAY),
            _disposition("NOT_REACHABLE", 1000, _DAY),
            _disposition("PROMISE_BROKEN", 1000, _DAY),
            _disposition("NOT_REACHABLE", 1000, _DAY),
        ]
        analytics = CallAnalytics(repo)

        assert analytics.contactability_rate(_TENANT, _DAY, _DAY + timedelta(days=1)) == 0.5
        assert analytics.recovery_rate(_TENANT, _DAY, _DAY + timedelta(days=1)) == 0.25

    def test_empty_window_returns_zero(self) -> None:
        analytics = CallAnalytics(_FakeCallDispositionRepository())
        assert analytics.average_duration_ms(_TENANT, _DAY, _DAY + timedelta(days=1)) == 0.0
        assert analytics.contactability_rate(_TENANT, _DAY, _DAY + timedelta(days=1)) == 0.0

    def test_intent_sequence_negotiation_and_sentiment_arc(self) -> None:
        turns = [{"intent": "GREETING"}, {"intent": ""}, {"intent": "NEGOTIATE"}]
        assert CallAnalytics.intent_sequence(turns) == ("GREETING", "NEGOTIATE")
        assert CallAnalytics.negotiation_result(["OPEN", "COUNTER", "ACCEPTED"]) == "ACCEPTED"
        assert CallAnalytics.negotiation_result([]) == "UNKNOWN"
        assert CallAnalytics.sentiment_arc([0.1, 0.4, 0.8]) == (0.1, 0.4, 0.8)


class TestCampaignAnalytics:
    def test_campaign_ptp_rate_calculation(self) -> None:
        """10 calls, 3 PTPs -> ptp_rate = 0.30."""
        repo = _FakeCampaignResultRepository()
        for i in range(10):
            repo.results.append(_campaign_result(ptp_created=i < 3, outcome_code="PTP_MADE", at=_DAY))
        analytics = CampaignAnalytics(repo)

        assert analytics.ptp_rate(_TENANT, _CAMPAIGN) == 0.30
        assert analytics.conversion_rate(_TENANT, _CAMPAIGN) == 0.30

    def test_ptp_rate_zero_when_no_results(self) -> None:
        analytics = CampaignAnalytics(_FakeCampaignResultRepository())
        assert analytics.ptp_rate(_TENANT, _CAMPAIGN) == 0.0

    def test_contactability_rate(self) -> None:
        repo = _FakeCampaignResultRepository()
        repo.results = [
            _campaign_result(False, "PTP_MADE", _DAY),
            _campaign_result(False, "NOT_REACHABLE", _DAY),
        ]
        analytics = CampaignAnalytics(repo)
        assert analytics.contactability_rate(_TENANT, _CAMPAIGN) == 0.5


class TestDailyAggregationJob:
    def test_run_for_day_tenant_wide(self) -> None:
        disposition_repo = _FakeCallDispositionRepository()
        disposition_repo.dispositions = [
            _disposition("PTP_MADE", 60_000, _DAY + timedelta(hours=2)),
            _disposition("NOT_REACHABLE", 20_000, _DAY + timedelta(hours=3)),
        ]
        result_repo = _FakeCampaignResultRepository()
        result_repo.results = [_campaign_result(True, "PTP_MADE", _DAY + timedelta(hours=2))]
        analytics_daily_repo = _FakeAnalyticsDailyRepository()
        job = DailyAggregationJob(disposition_repo, result_repo, analytics_daily_repo)

        rollup = job.run_for_day(_TENANT, _DAY.date())

        assert rollup.calls_completed == 2
        assert rollup.ptp_count == 1
        assert rollup.ptp_rate == 1.0
        assert rollup.avg_duration_ms == 40_000
        assert analytics_daily_repo.rows == [rollup]

    def test_run_for_day_empty_day_is_zeroed(self) -> None:
        job = DailyAggregationJob(
            _FakeCallDispositionRepository(), _FakeCampaignResultRepository(), _FakeAnalyticsDailyRepository()
        )
        rollup = job.run_for_day(_TENANT, _DAY.date())
        assert rollup.calls_completed == 0
        assert rollup.ptp_rate == 0.0


class TestRealtimeAnalytics:
    def test_snapshot_includes_core_fields(self) -> None:
        disposition_repo = _FakeCallDispositionRepository()
        disposition_repo.dispositions = [_disposition("PTP_MADE", 1000, _DAY)]
        realtime = RealtimeAnalytics(
            CallAnalytics(disposition_repo), CampaignAnalytics(_FakeCampaignResultRepository())
        )

        snapshot = realtime.snapshot(_TENANT, _DAY, _DAY + timedelta(days=1))

        assert snapshot["tenant_id"] == str(_TENANT)
        assert snapshot["calls_completed"] == 1

    def test_stream_yields_bounded_snapshots(self) -> None:
        realtime = RealtimeAnalytics(
            CallAnalytics(_FakeCallDispositionRepository()), CampaignAnalytics(_FakeCampaignResultRepository())
        )
        snapshots = list(realtime.stream(_TENANT, _DAY, _DAY + timedelta(days=1), max_iterations=3))
        assert len(snapshots) == 3


class TestAnalyticsService:
    def test_campaign_summary_and_daily_aggregation(self) -> None:
        disposition_repo = _FakeCallDispositionRepository()
        result_repo = _FakeCampaignResultRepository()
        result_repo.results = [_campaign_result(True, "PTP_MADE", _DAY)]
        analytics_daily_repo = _FakeAnalyticsDailyRepository()

        service = AnalyticsService(
            CallAnalytics(disposition_repo),
            CampaignAnalytics(result_repo),
            RealtimeAnalytics(CallAnalytics(disposition_repo), CampaignAnalytics(result_repo)),
            DailyAggregationJob(disposition_repo, result_repo, analytics_daily_repo),
        )

        summary = service.campaign_summary(_TENANT, _CAMPAIGN)
        assert summary["ptp_rate"] == 1.0

        rollup = service.run_daily_aggregation(_TENANT, _DAY.date())
        assert rollup.ptp_count == 1
