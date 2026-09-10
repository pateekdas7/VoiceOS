"""PostgresReportRepository -- backs ReportRepositoryPort against ``ai_reports`` (migration 0031).

``evidence`` stores each contributing ``Insight`` fully inlined (not just an
``insight_id`` reference) -- this is deliberate: ADR-006 Sec 13.8 requires a
report be reproducible from its OWN stored evidence, independent of whether
the referenced ``ops_insights`` row is later pruned/archived.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.libs.repositories.base import BaseRepository
from src.services.ops_intelligence.models import (
    ConfidenceLevel,
    Hypothesis,
    Insight,
    InsightCategory,
    Report,
    ReportType,
    ScopeLevel,
    Severity,
    SourceServiceCall,
    VerifiedFact,
)

_TABLE = "ai_reports"
_COLUMNS = (
    "report_id",
    "report_type",
    "period_start",
    "period_end",
    "scope_level",
    "tenant_id",
    "severity",
    "affected_components",
    "business_impact",
    "recommended_actions",
    "confidence_level",
    "evidence",
    "source_service_calls",
    "narrative",
    "generated_at",
    "delivered_to",
)


class PostgresReportRepository(BaseRepository):
    """Real Postgres-backed ``ReportRepositoryPort`` implementation."""

    def create(self, report: Report) -> Report:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                report_id, report_type, period_start, period_end, scope_level, tenant_id,
                severity, affected_components, business_impact, recommended_actions,
                confidence_level, evidence, source_service_calls, narrative, generated_at, delivered_to
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb)
            """,
            (
                report.report_id,
                report.report_type.value,
                report.period_start,
                report.period_end,
                report.scope_level.value,
                report.tenant_id,
                report.severity.value,
                json.dumps(list(report.affected_components)),
                report.business_impact,
                json.dumps(list(report.recommended_actions)),
                report.confidence_level.value,
                json.dumps([_insight_to_dict(i) for i in report.evidence]),
                json.dumps([_call_to_dict(c) for c in report.source_service_calls]),
                report.narrative,
                report.generated_at,
                json.dumps(list(report.delivered_to)),
            ),
        )
        self._commit()
        return report

    def get(self, report_id: str) -> Report | None:
        cur = self._execute(f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE report_id = %s", (report_id,))
        row = cur.fetchone()
        return _row_to_report(row) if row is not None else None

    def list(
        self, *, tenant_id: str | None = None, report_type: ReportType | None = None, limit: int = 50
    ) -> tuple[Report, ...]:
        where = []
        params: list[Any] = []
        if tenant_id is not None:
            where.append("tenant_id = %s")
            params.append(tenant_id)
        if report_type is not None:
            where.append("report_type = %s")
            params.append(report_type.value)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cur = self._execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} {where_sql} ORDER BY period_start DESC LIMIT %s",
            (*params, limit),
        )
        return tuple(_row_to_report(row) for row in cur.fetchall())


def _as_list(value: Any) -> list[Any]:
    result: list[Any] = json.loads(value) if isinstance(value, str) else value
    return result


def _insight_to_dict(insight: Insight) -> dict[str, Any]:
    return {
        "insight_id": insight.insight_id,
        "tenant_id": insight.tenant_id,
        "category": insight.category.value,
        "severity": insight.severity.value,
        "verified_facts": [
            {"claim": f.claim, "source": f.source, "query": f.query, "value": f.value, "observed_at": f.observed_at.isoformat()}
            for f in insight.verified_facts
        ],
        "hypotheses": [{"claim": h.claim, "reasoning": h.reasoning, "confidence": h.confidence.value} for h in insight.hypotheses],
        "affected_components": list(insight.affected_components),
        "confidence_level": insight.confidence_level.value,
        "model": insight.model,
        "prompt_version": insight.prompt_version,
        "generated_at": insight.generated_at.isoformat(),
        "recommendation": insight.recommendation,
    }


def _dict_to_insight(d: dict[str, Any]) -> Insight:
    return Insight(
        insight_id=d["insight_id"],
        tenant_id=d["tenant_id"],
        category=InsightCategory(d["category"]),
        severity=Severity(d["severity"]),
        verified_facts=tuple(
            VerifiedFact(claim=f["claim"], source=f["source"], query=f["query"], value=f["value"], observed_at=datetime.fromisoformat(f["observed_at"]))
            for f in d["verified_facts"]
        ),
        hypotheses=tuple(Hypothesis(claim=h["claim"], reasoning=h["reasoning"], confidence=ConfidenceLevel(h["confidence"])) for h in d["hypotheses"]),
        affected_components=tuple(d["affected_components"]),
        confidence_level=ConfidenceLevel(d["confidence_level"]),
        model=d["model"],
        prompt_version=d["prompt_version"],
        generated_at=datetime.fromisoformat(d["generated_at"]),
        recommendation=d.get("recommendation"),
    )


def _call_to_dict(call: SourceServiceCall) -> dict[str, Any]:
    return {
        "service": call.service,
        "method": call.method,
        "params": call.params,
        "result_snapshot": call.result_snapshot,
        "called_at": call.called_at.isoformat(),
    }


def _dict_to_call(d: dict[str, Any]) -> SourceServiceCall:
    return SourceServiceCall(
        service=d["service"], method=d["method"], params=d["params"], result_snapshot=d["result_snapshot"],
        called_at=datetime.fromisoformat(d["called_at"]),
    )


def _row_to_report(row: tuple[Any, ...]) -> Report:
    (
        report_id, report_type, period_start, period_end, scope_level, tenant_id, severity,
        affected_components, business_impact, recommended_actions, confidence_level, evidence,
        source_service_calls, narrative, generated_at, delivered_to,
    ) = row
    return Report(
        report_id=str(report_id),
        report_type=ReportType(report_type),
        period_start=period_start,
        period_end=period_end,
        scope_level=ScopeLevel(scope_level),
        tenant_id=str(tenant_id) if tenant_id is not None else None,
        severity=Severity(severity),
        affected_components=tuple(_as_list(affected_components)),
        business_impact=business_impact,
        recommended_actions=tuple(_as_list(recommended_actions)),
        confidence_level=ConfidenceLevel(confidence_level),
        evidence=tuple(_dict_to_insight(i) for i in _as_list(evidence)),
        source_service_calls=tuple(_dict_to_call(c) for c in _as_list(source_service_calls)),
        narrative=narrative,
        generated_at=generated_at,
        delivered_to=tuple(_as_list(delivered_to)),
    )


__all__ = ["PostgresReportRepository"]
