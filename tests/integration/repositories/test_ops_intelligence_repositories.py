"""Integration tests: ops_intelligence Postgres repositories against real Postgres (ADR-006).

Skipped when POSTGRES_DSN is not set. Each test creates its own real
``tenants`` row (required by the FK on ``tenant_id``) and cleans it up,
mirroring ``tests/integration/repositories/test_tenant_isolation.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from src.services.ops_intelligence.models import (
    AlertRecord,
    AlertSource,
    AlertStatus,
    ConfidenceLevel,
    Hypothesis,
    Insight,
    InsightCategory,
    PatternSignature,
    Report,
    ReportType,
    ScopeLevel,
    Severity,
    SourceServiceCall,
    VerifiedFact,
)
from src.services.ops_intelligence.reasoning.capacity_planner import CapacityForecast
from src.services.ops_intelligence.repositories import (
    PostgresAlertRepository,
    PostgresCapacityForecastRepository,
    PostgresInsightRepository,
    PostgresPatternSignatureRepository,
    PostgresReportRepository,
)
from tests.integration.conftest import requires_postgres

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


def _create_tenant(pg_conn: Any) -> str:
    tenant_id = str(uuid.uuid4())
    cur = pg_conn.cursor()
    cur.execute(
        "INSERT INTO tenants (tenant_id, slug, display_name, subscription_tier, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (tenant_id, f"test-{tenant_id[:8]}", "Test Tenant", "GROWTH", NOW, NOW),
    )
    pg_conn.commit()
    return tenant_id


def _delete_tenant(pg_conn: Any, tenant_id: str) -> None:
    cur = pg_conn.cursor()
    cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
    pg_conn.commit()


@requires_postgres
class TestPostgresInsightRepository:
    def test_create_and_get_round_trips_platform_wide_insight(self, pg_conn: Any) -> None:
        repo = PostgresInsightRepository(pg_conn)
        insight = Insight(
            insight_id=str(uuid.uuid4()),
            tenant_id=None,
            category=InsightCategory.REGRESSION,
            severity=Severity.WARNING,
            verified_facts=(VerifiedFact(claim="latency up", source="prometheus", query="q", value="900", observed_at=NOW),),
            hypotheses=(Hypothesis(claim="thermal throttle", reasoning="pattern match", confidence=ConfidenceLevel.MEDIUM),),
            affected_components=("tts",),
            confidence_level=ConfidenceLevel.MEDIUM,
            model="claude-sonnet-5",
            prompt_version="v1",
            generated_at=NOW,
            recommendation="check temps",
        )
        try:
            repo.create(insight)
            fetched = repo.get(insight.insight_id)
            assert fetched is not None
            assert fetched.tenant_id is None
            assert fetched.verified_facts[0].claim == "latency up"
            assert fetched.hypotheses[0].confidence == ConfidenceLevel.MEDIUM
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM ops_insights WHERE insight_id = %s", (insight.insight_id,))
            pg_conn.commit()

    def test_tenant_scoped_list_excludes_other_tenants_and_platform_wide(self, pg_conn: Any) -> None:
        repo = PostgresInsightRepository(pg_conn)
        tenant_a = _create_tenant(pg_conn)
        tenant_b = _create_tenant(pg_conn)
        insight_ids = []
        try:
            for tenant_id in (tenant_a, tenant_b, None):
                insight = Insight(
                    insight_id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    category=InsightCategory.ANOMALY,
                    severity=Severity.INFO,
                    verified_facts=(VerifiedFact(claim="x", source="prometheus", query="q", value="1", observed_at=NOW),),
                    hypotheses=(),
                    affected_components=(),
                    confidence_level=ConfidenceLevel.LOW,
                    model="m",
                    prompt_version="v1",
                    generated_at=NOW,
                )
                repo.create(insight)
                insight_ids.append(insight.insight_id)

            tenant_a_insights = repo.list(tenant_id=tenant_a)
            assert {i.insight_id for i in tenant_a_insights} == {insight_ids[0]}
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM ops_insights WHERE insight_id = ANY(%s::uuid[])", (insight_ids,))
            pg_conn.commit()
            _delete_tenant(pg_conn, tenant_a)
            _delete_tenant(pg_conn, tenant_b)

    def test_insert_with_zero_verified_facts_violates_db_constraint(self, pg_conn: Any) -> None:
        """Defense-in-depth for ADR-006 Sec 3.2.2 -- the DB itself refuses an unevidenced insight."""
        cur = pg_conn.cursor()
        with pytest.raises(Exception, match="ck_ops_insights_has_verified_facts"):
            cur.execute(
                "INSERT INTO ops_insights (insight_id, category, severity, verified_facts, confidence_level, model, prompt_version) "
                "VALUES (%s, 'anomaly', 'info', '[]'::jsonb, 'low', 'm', 'v1')",
                (str(uuid.uuid4()),),
            )
        pg_conn.rollback()


@requires_postgres
class TestPostgresAlertRepository:
    def test_create_get_and_lifecycle_transitions(self, pg_conn: Any) -> None:
        repo = PostgresAlertRepository(pg_conn)
        alert = AlertRecord(
            alert_id=str(uuid.uuid4()),
            tenant_id=None,
            source=AlertSource.ALERTMANAGER,
            fingerprint="fp-int-1",
            severity=Severity.CRITICAL,
            status=AlertStatus.FIRING,
            fired_at=NOW,
            labels={"alertname": "GPUUnavailable"},
            annotations={},
        )
        try:
            repo.create(alert)
            assert repo.find_by_fingerprint_open("fp-int-1") is not None

            from dataclasses import replace

            acked = replace(alert, status=AlertStatus.ACKNOWLEDGED, acknowledged_by="alice")
            repo.update_status(alert.alert_id, acked)
            fetched = repo.get(alert.alert_id)
            assert fetched is not None
            assert fetched.status == AlertStatus.ACKNOWLEDGED
            assert fetched.acknowledged_by == "alice"
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM alert_history WHERE alert_id = %s", (alert.alert_id,))
            pg_conn.commit()

    def test_list_open_excludes_resolved(self, pg_conn: Any) -> None:
        repo = PostgresAlertRepository(pg_conn)
        open_alert = AlertRecord(
            alert_id=str(uuid.uuid4()), tenant_id=None, source=AlertSource.ALERTMANAGER, fingerprint="fp-open",
            severity=Severity.WARNING, status=AlertStatus.FIRING, fired_at=NOW, labels={}, annotations={},
        )
        resolved_alert = AlertRecord(
            alert_id=str(uuid.uuid4()), tenant_id=None, source=AlertSource.ALERTMANAGER, fingerprint="fp-resolved",
            severity=Severity.WARNING, status=AlertStatus.RESOLVED, fired_at=NOW, labels={}, annotations={}, resolved_at=NOW,
        )
        try:
            repo.create(open_alert)
            repo.create(resolved_alert)
            open_ids = {a.alert_id for a in repo.list_open()}
            assert open_alert.alert_id in open_ids
            assert resolved_alert.alert_id not in open_ids
        finally:
            cur = pg_conn.cursor()
            cur.execute(
                "DELETE FROM alert_history WHERE alert_id = ANY(%s::uuid[])",
                ([open_alert.alert_id, resolved_alert.alert_id],),
            )
            pg_conn.commit()


@requires_postgres
class TestPostgresReportRepository:
    def test_create_and_get_round_trips_full_evidence_chain(self, pg_conn: Any) -> None:
        repo = PostgresReportRepository(pg_conn)
        insight = Insight(
            insight_id=str(uuid.uuid4()), tenant_id=None, category=InsightCategory.RCA, severity=Severity.CRITICAL,
            verified_facts=(VerifiedFact(claim="x", source="prometheus", query="q", value="1", observed_at=NOW),),
            hypotheses=(), affected_components=("gpu",), confidence_level=ConfidenceLevel.HIGH, model="m",
            prompt_version="v1", generated_at=NOW,
        )
        report = Report(
            report_id=str(uuid.uuid4()), report_type=ReportType.RCA, period_start=NOW, period_end=NOW,
            scope_level=ScopeLevel.PLATFORM, tenant_id=None, severity=Severity.CRITICAL,
            affected_components=("gpu",), business_impact="impact", recommended_actions=("fix it",),
            confidence_level=ConfidenceLevel.HIGH, evidence=(insight,),
            source_service_calls=(SourceServiceCall(service="s", method="m", params={}, result_snapshot={"x": 1}, called_at=NOW),),
            narrative="narrative text", generated_at=NOW,
        )
        try:
            repo.create(report)
            fetched = repo.get(report.report_id)
            assert fetched is not None
            assert fetched.narrative == "narrative text"
            assert fetched.evidence[0].verified_facts[0].claim == "x"
            assert fetched.source_service_calls[0].result_snapshot == {"x": 1}
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM ai_reports WHERE report_id = %s", (report.report_id,))
            pg_conn.commit()


@requires_postgres
class TestPostgresCapacityForecastRepository:
    def test_create_and_latest(self, pg_conn: Any) -> None:
        repo = PostgresCapacityForecastRepository(pg_conn)
        forecast = CapacityForecast(
            forecast_id=str(uuid.uuid4()), tenant_id=None, resource="gpu", horizon_days=30,
            forecast_data={"method": "arima_proxy_linear_trend"}, headroom_pct=42.5,
            confidence=ConfidenceLevel.MEDIUM, generated_at=NOW,
        )
        try:
            repo.create(forecast)
            latest = repo.latest("gpu")
            assert latest is not None
            assert latest.headroom_pct == pytest.approx(42.5)
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM capacity_forecasts WHERE forecast_id = %s", (forecast.forecast_id,))
            pg_conn.commit()


@requires_postgres
class TestPostgresPatternSignatureRepository:
    def test_upsert_creates_then_increments(self, pg_conn: Any) -> None:
        repo = PostgresPatternSignatureRepository(pg_conn)
        signature = PatternSignature(
            signature_id=str(uuid.uuid4()), tenant_id=None, fingerprint_hash="fp-hash-int-1",
            category="regression", affected_components=("tts",), root_cause_summary="thermal throttle",
            first_seen=NOW, last_seen=NOW, occurrence_count=1,
        )
        try:
            repo.upsert_occurrence(signature)
            found = repo.find("fp-hash-int-1", tenant_id=None)
            assert found is not None
            assert found.occurrence_count == 1

            from dataclasses import replace

            incremented = replace(found, occurrence_count=found.occurrence_count + 1)
            repo.upsert_occurrence(incremented)
            found_again = repo.find("fp-hash-int-1", tenant_id=None)
            assert found_again is not None
            assert found_again.occurrence_count == 2
        finally:
            cur = pg_conn.cursor()
            cur.execute("DELETE FROM ops_pattern_signatures WHERE fingerprint_hash = %s", ("fp-hash-int-1",))
            pg_conn.commit()
