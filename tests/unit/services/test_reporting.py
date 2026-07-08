"""Unit tests for the Reporting Platform (Sprint-024, V5 Ch12).

All tests run fully in-process — no live Postgres required (Phase 1).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime

import pytest
from openpyxl import load_workbook

from src.libs.audit.event import AuditEvent
from src.libs.contracts.models.analytics import AnalyticsDailyRollup
from src.libs.contracts.primitives import TenantId
from src.services.reporting.exporter import ExportService, UnsupportedExportFormatError
from src.services.reporting.scheduler import ReportScheduler
from src.services.reporting.service import ReportingService
from src.services.reporting.templates import ReportData
from src.services.reporting.templates.campaign_summary import build as build_campaign_summary
from src.services.reporting.templates.collections_performance import build as build_collections_performance
from src.services.reporting.templates.compliance_audit import build as build_compliance_audit

_TENANT = TenantId("tenant-a")
_DAY = date(2026, 7, 1)


class _FakeAggregationJob:
    def __init__(self) -> None:
        self.calls: list[tuple[TenantId, date]] = []

    def run_for_day(self, tenant_id: TenantId, day: date, campaign_id: object | None = None) -> AnalyticsDailyRollup:
        self.calls.append((tenant_id, day))
        return AnalyticsDailyRollup(
            analytics_daily_id="rollup-1",
            tenant_id=tenant_id,
            day=day,
            calls_completed=5,
            ptp_rate=0.4,
            computed_at=datetime.now(UTC),
        )


class TestReportScheduler:
    def test_run_daily_records_history(self) -> None:
        job = _FakeAggregationJob()
        scheduler = ReportScheduler(job)

        rollup = scheduler.run_daily(_TENANT, _DAY)

        assert rollup.calls_completed == 5
        assert len(scheduler.history()) == 1
        assert scheduler.history()[0].tenant_id == str(_TENANT)
        assert job.calls == [(_TENANT, _DAY)]

    def test_multiple_runs_accumulate_history(self) -> None:
        scheduler = ReportScheduler(_FakeAggregationJob())
        scheduler.run_daily(_TENANT, _DAY)
        scheduler.run_daily(_TENANT, _DAY)
        assert len(scheduler.history()) == 2


class TestExportService:
    def _sample_report(self) -> ReportData:
        return ReportData(
            title="Test Report",
            columns=("Metric", "Value"),
            rows=(("Calls", 10), ("PTP Rate", "30.00%")),
            generated_at=datetime.now(UTC),
        )

    def test_to_csv(self) -> None:
        exporter = ExportService()
        raw = exporter.to_csv(self._sample_report())
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
        assert rows[0] == ["Metric", "Value"]
        assert rows[1] == ["Calls", "10"]

    def test_to_xlsx(self) -> None:
        exporter = ExportService()
        raw = exporter.to_xlsx(self._sample_report())
        workbook = load_workbook(io.BytesIO(raw))
        sheet = workbook.active
        assert sheet is not None
        assert [cell.value for cell in sheet[1]] == ["Metric", "Value"]

    def test_to_pdf_produces_valid_pdf_bytes(self) -> None:
        exporter = ExportService()
        raw = exporter.to_pdf(self._sample_report())
        assert raw.startswith(b"%PDF")

    def test_export_dispatches_by_format(self) -> None:
        exporter = ExportService()
        report = self._sample_report()
        assert exporter.export(report, "csv") == exporter.to_csv(report)

    def test_unsupported_format_raises(self) -> None:
        exporter = ExportService()
        with pytest.raises(UnsupportedExportFormatError):
            exporter.export(self._sample_report(), "docx")


class TestReportTemplates:
    def test_campaign_summary_template(self) -> None:
        report = build_campaign_summary(
            campaign_name="Diwali Collections",
            calls_completed=100,
            ptp_rate=0.3,
            contactability_rate=0.6,
            conversion_rate=0.3,
            generated_at=datetime.now(UTC),
        )
        assert "Diwali Collections" in report.title
        assert ("Calls Completed", 100) in report.rows

    def test_collections_performance_template(self) -> None:
        report = build_collections_performance(
            tenant_name="Acme Lending",
            day=_DAY,
            calls_completed=50,
            ptp_rate=0.2,
            recovery_rate=0.15,
            contactability_rate=0.5,
            amount_collected_minor=100_000,
            currency="INR",
            generated_at=datetime.now(UTC),
        )
        assert "Acme Lending" in report.title
        assert any(row[0] == "Amount Collected" for row in report.rows)

    def test_compliance_audit_template(self) -> None:
        events = [
            AuditEvent(
                audit_id="audit-1",
                tenant_id=str(_TENANT),
                actor_id="agent-1",
                action="policy.evaluate.start_call",
                resource_type="rbi",
                resource_id="call-1",
                outcome="DENY",
                recorded_at=datetime.now(UTC),
            )
        ]
        report = build_compliance_audit(tenant_name="Acme Lending", events=events, generated_at=datetime.now(UTC))
        assert "Acme Lending" in report.title
        assert report.rows[0][1] == "agent-1"
        assert report.rows[0][4] == "DENY"


class TestReportingService:
    def test_run_scheduled_aggregation_and_export(self) -> None:
        service = ReportingService(ReportScheduler(_FakeAggregationJob()), ExportService())

        rollup = service.run_scheduled_aggregation(_TENANT, _DAY)
        assert rollup.calls_completed == 5
        assert len(service.run_history()) == 1

        report = ReportData(title="T", columns=("A",), rows=(("x",),), generated_at=datetime.now(UTC))
        csv_bytes = service.export(report, "csv")
        assert b"A" in csv_bytes
