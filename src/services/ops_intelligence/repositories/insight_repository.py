"""PostgresInsightRepository -- backs InsightRepositoryPort against ``ops_insights`` (migration 0028).

Follows the ``BaseRepository`` primitives (``_execute``/``_commit``,
circuit-breaker-guarded) but does NOT use ``_tenant_select`` -- that helper
assumes ``tenant_id`` is always a mandatory, non-NULL scope, whereas
``ops_insights.tenant_id`` is deliberately nullable (NULL = platform-wide,
ADR-006 Sec 7/10). Query methods here implement the correct nullable-tenant
semantics explicitly instead.
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
    Severity,
    VerifiedFact,
)

_TABLE = "ops_insights"
_COLUMNS = (
    "insight_id",
    "tenant_id",
    "category",
    "severity",
    "verified_facts",
    "hypotheses",
    "recommendation",
    "affected_components",
    "confidence_level",
    "model",
    "prompt_version",
    "generated_at",
    "reviewed_by",
    "reviewed_at",
)


class PostgresInsightRepository(BaseRepository):
    """Real Postgres-backed ``InsightRepositoryPort`` implementation."""

    def create(self, insight: Insight) -> Insight:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                insight_id, tenant_id, category, severity, verified_facts, hypotheses,
                recommendation, affected_components, confidence_level, model, prompt_version,
                generated_at, reviewed_by, reviewed_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            """,
            (
                insight.insight_id,
                insight.tenant_id,
                insight.category.value,
                insight.severity.value,
                json.dumps([_fact_to_dict(f) for f in insight.verified_facts]),
                json.dumps([_hypothesis_to_dict(h) for h in insight.hypotheses]),
                insight.recommendation,
                json.dumps(list(insight.affected_components)),
                insight.confidence_level.value,
                insight.model,
                insight.prompt_version,
                insight.generated_at,
                insight.reviewed_by,
                insight.reviewed_at,
            ),
        )
        self._commit()
        return insight

    def get(self, insight_id: str) -> Insight | None:
        cur = self._execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} WHERE insight_id = %s",
            (insight_id,),
        )
        row = cur.fetchone()
        return _row_to_insight(row) if row is not None else None

    def list(
        self, *, tenant_id: str | None = None, category: InsightCategory | None = None, limit: int = 50
    ) -> tuple[Insight, ...]:
        """``tenant_id=None`` returns every insight (platform-wide admin view, ADR-006 Sec 10);
        a concrete ``tenant_id`` returns ONLY that tenant's own rows -- never NULL/other-tenant rows,
        satisfying the tenant-scoped evidence-bundle isolation rule (Sec 10.2)."""
        where = []
        params: list[Any] = []
        if tenant_id is not None:
            where.append("tenant_id = %s")
            params.append(tenant_id)
        if category is not None:
            where.append("category = %s")
            params.append(category.value)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cur = self._execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} {where_sql} ORDER BY generated_at DESC LIMIT %s",
            (*params, limit),
        )
        return tuple(_row_to_insight(row) for row in cur.fetchall())


def _fact_to_dict(fact: VerifiedFact) -> dict[str, Any]:
    return {
        "claim": fact.claim,
        "source": fact.source,
        "query": fact.query,
        "value": fact.value,
        "observed_at": fact.observed_at.isoformat(),
    }


def _hypothesis_to_dict(hypothesis: Hypothesis) -> dict[str, Any]:
    return {"claim": hypothesis.claim, "reasoning": hypothesis.reasoning, "confidence": hypothesis.confidence.value}


def _as_list(value: Any) -> list[Any]:
    result: list[Any] = json.loads(value) if isinstance(value, str) else value
    return result


def _row_to_insight(row: tuple[Any, ...]) -> Insight:
    (
        insight_id,
        tenant_id,
        category,
        severity,
        verified_facts,
        hypotheses,
        recommendation,
        affected_components,
        confidence_level,
        model,
        prompt_version,
        generated_at,
        reviewed_by,
        reviewed_at,
    ) = row
    return Insight(
        insight_id=str(insight_id),
        tenant_id=str(tenant_id) if tenant_id is not None else None,
        category=InsightCategory(category),
        severity=Severity(severity),
        verified_facts=tuple(
            VerifiedFact(
                claim=f["claim"],
                source=f["source"],
                query=f["query"],
                value=f["value"],
                observed_at=_parse_dt(f["observed_at"]),
            )
            for f in _as_list(verified_facts)
        ),
        hypotheses=tuple(
            Hypothesis(claim=h["claim"], reasoning=h["reasoning"], confidence=ConfidenceLevel(h["confidence"]))
            for h in _as_list(hypotheses)
        ),
        affected_components=tuple(_as_list(affected_components)),
        confidence_level=ConfidenceLevel(confidence_level),
        model=model,
        prompt_version=prompt_version,
        generated_at=generated_at,
        recommendation=recommendation,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


__all__ = ["PostgresInsightRepository"]
