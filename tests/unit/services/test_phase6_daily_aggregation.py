"""Unit tests for Phase 6b: DailyAggregationJob with real PTP + DPD aggregation.

Verifies:
- amount_collected_minor uses PTPAggregationPort.sum_kept_amount_between()
- avg_dpd uses LoanDPDAggregationPort.avg_dpd_for_tenant()
- Both ports are optional — existing callers without them still get zeros
- Idempotency: second run for same day produces updated rollup
- Tenant isolation: each tenant's data is fetched and stored independently
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, time
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.primitives import CampaignId, TenantId
from src.services.analytics.aggregation import DailyAggregationJob


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeDispositionRepo:
    def find_between(self, tenant_id: TenantId, start: datetime, end: datetime):
        return ()


class _FakeCampaignResultRepo:
    def find_between(self, tenant_id: TenantId, start: datetime, end: datetime):
        return ()

    def find_by_campaign(self, tenant_id: TenantId, campaign_id: CampaignId):
        return ()


class _FakeDailyRepo:
    def __init__(self) -> None:
        self.stored: list[AnalyticsDailyRollup] = []

    def upsert(self, rollup: AnalyticsDailyRollup) -> AnalyticsDailyRollup:
        self.stored.append(rollup)
        return rollup


class _FakePTPRepo:
    def __init__(self, amount: int = 0) -> None:
        self._amount = amount
        self.calls: list[tuple[Any, Any, Any]] = []

    def sum_kept_amount_between(self, tenant_id: TenantId, start: datetime, end: datetime) -> int:
        self.calls.append((tenant_id, start, end))
        return self._amount


class _FakeLoanRepo:
    def __init__(self, avg_dpd: float = 0.0) -> None:
        self._avg_dpd = avg_dpd
        self.calls: list[TenantId] = []

    def avg_dpd_for_tenant(self, tenant_id: TenantId) -> float:
        self.calls.append(tenant_id)
        return self._avg_dpd


# ---------------------------------------------------------------------------
# Tests — without optional ports (backward compat)
# ---------------------------------------------------------------------------

class TestDailyAggregationJobWithoutOptionalPorts:
    def test_amount_collected_minor_is_zero_without_ptp_port(self) -> None:
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(), _FakeCampaignResultRepo(), daily_repo
        )
        rollup = job.run_for_day(TenantId("t-1"), date(2026, 9, 16))
        assert rollup.amount_collected_minor == 0

    def test_avg_dpd_is_zero_without_loan_port(self) -> None:
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(), _FakeCampaignResultRepo(), daily_repo
        )
        rollup = job.run_for_day(TenantId("t-1"), date(2026, 9, 16))
        assert rollup.avg_dpd == 0.0


# ---------------------------------------------------------------------------
# Tests — with optional ports (Phase 6b)
# ---------------------------------------------------------------------------

class TestDailyAggregationJobWithPTPPort:
    def test_amount_collected_minor_uses_ptp_port(self) -> None:
        ptp_repo = _FakePTPRepo(amount=125_000)
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(),
            _FakeCampaignResultRepo(),
            daily_repo,
            ptp_repository=ptp_repo,
        )
        rollup = job.run_for_day(TenantId("t-1"), date(2026, 9, 16))
        assert rollup.amount_collected_minor == 125_000

    def test_ptp_port_called_with_correct_day_bounds(self) -> None:
        ptp_repo = _FakePTPRepo(amount=0)
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(),
            _FakeCampaignResultRepo(),
            daily_repo,
            ptp_repository=ptp_repo,
        )
        day = date(2026, 9, 16)
        job.run_for_day(TenantId("t-1"), day)

        assert len(ptp_repo.calls) == 1
        _, start, end = ptp_repo.calls[0]
        expected_start = datetime.combine(day, time.min, tzinfo=UTC)
        expected_end = expected_start + timedelta(days=1)
        assert start == expected_start
        assert end == expected_end

    def test_ptp_port_called_with_correct_tenant(self) -> None:
        ptp_repo = _FakePTPRepo(amount=0)
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(),
            _FakeCampaignResultRepo(),
            daily_repo,
            ptp_repository=ptp_repo,
        )
        tenant = TenantId("t-abc")
        job.run_for_day(tenant, date(2026, 9, 16))
        assert ptp_repo.calls[0][0] == tenant


class TestDailyAggregationJobWithLoanPort:
    def test_avg_dpd_uses_loan_port(self) -> None:
        loan_repo = _FakeLoanRepo(avg_dpd=42.5)
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(),
            _FakeCampaignResultRepo(),
            daily_repo,
            loan_repository=loan_repo,
        )
        rollup = job.run_for_day(TenantId("t-1"), date(2026, 9, 16))
        assert rollup.avg_dpd == 42.5

    def test_loan_port_called_with_correct_tenant(self) -> None:
        loan_repo = _FakeLoanRepo(avg_dpd=10.0)
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(),
            _FakeCampaignResultRepo(),
            daily_repo,
            loan_repository=loan_repo,
        )
        tenant = TenantId("t-xyz")
        job.run_for_day(tenant, date(2026, 9, 16))
        assert loan_repo.calls[0] == tenant


class TestDailyAggregationJobIdempotency:
    def test_second_run_same_day_produces_second_upsert(self) -> None:
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(), _FakeCampaignResultRepo(), daily_repo
        )
        day = date(2026, 9, 16)
        job.run_for_day(TenantId("t-1"), day)
        job.run_for_day(TenantId("t-1"), day)
        # Two upsert calls — the DB layer handles ON CONFLICT idempotency
        assert len(daily_repo.stored) == 2

    def test_rollup_upserted_to_daily_repo(self) -> None:
        daily_repo = _FakeDailyRepo()
        ptp_repo = _FakePTPRepo(amount=5000)
        loan_repo = _FakeLoanRepo(avg_dpd=15.0)
        job = DailyAggregationJob(
            _FakeDispositionRepo(),
            _FakeCampaignResultRepo(),
            daily_repo,
            ptp_repository=ptp_repo,
            loan_repository=loan_repo,
        )
        rollup = job.run_for_day(TenantId("t-1"), date(2026, 9, 16))

        assert len(daily_repo.stored) == 1
        assert daily_repo.stored[0].amount_collected_minor == 5000
        assert daily_repo.stored[0].avg_dpd == 15.0


class TestDailyAggregationJobTenantIsolation:
    def test_each_tenant_gets_own_ptp_query(self) -> None:
        ptp_repo = _FakePTPRepo(amount=0)
        daily_repo = _FakeDailyRepo()
        job = DailyAggregationJob(
            _FakeDispositionRepo(),
            _FakeCampaignResultRepo(),
            daily_repo,
            ptp_repository=ptp_repo,
        )
        day = date(2026, 9, 16)
        job.run_for_day(TenantId("t-1"), day)
        job.run_for_day(TenantId("t-2"), day)

        assert len(ptp_repo.calls) == 2
        tenant_ids = {str(call[0]) for call in ptp_repo.calls}
        assert tenant_ids == {"t-1", "t-2"}
