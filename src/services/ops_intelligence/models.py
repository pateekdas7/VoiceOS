"""Shared result types for the Intelligent Analysis Layer (ADR-006).

Split to mirror the ``plumbing/`` vs ``reasoning/`` boundary (ADR-006 Sec 3.0):
alert/report *lifecycle* types used by ``plumbing/`` carry no AI-generated
content; ``Insight``/``Report`` (the ``reasoning/``-produced types) always
carry the evidence/hypothesis split mandated by ADR-006 Sec 3.2.2.

Architecture: ADR-006 (Monitoring & Intelligent Operations Architecture).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InsightCategory(StrEnum):
    ANOMALY = "anomaly"
    REGRESSION = "regression"
    RCA = "rca"
    PREDICTION = "prediction"
    SUMMARY = "summary"
    DOMAIN_QUALITY = "domain_quality"


class ReportType(StrEnum):
    EXECUTIVE_SUMMARY = "executive_summary"
    ENGINEERING_SUMMARY = "engineering_summary"
    INCIDENT_REPORT = "incident_report"
    RCA = "rca"
    PERFORMANCE_REPORT = "performance_report"
    CAPACITY_REPORT = "capacity_report"
    DAILY_HEALTH = "daily_health"
    WEEKLY_HEALTH = "weekly_health"
    MONTHLY_HEALTH = "monthly_health"


class ScopeLevel(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


class AlertSource(StrEnum):
    ALERTMANAGER = "alertmanager"
    COMPLIANCE_MONITORING = "compliance_monitoring"


class AlertStatus(StrEnum):
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


# ---------------------------------------------------------------------------
# reasoning/ types (ADR-006 Sec 3.2.2 -- explainable AI: evidence + hypothesis)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedFact:
    """One evidence citation: a real, re-runnable query against a named telemetry source.

    An ``Insight`` with zero ``VerifiedFact`` entries must never be stored
    (ADR-006 Sec 3.2.2) -- enforced in :mod:`insight_service`, backstopped
    by a DB CHECK constraint (migration 0032).
    """

    claim: str
    source: str
    """One of: prometheus | loki | jaeger | alertmanager | event_bus."""
    query: str
    """A real, copy-pasteable PromQL/LogQL/trace-query string."""
    value: str
    observed_at: datetime


@dataclass(frozen=True)
class Hypothesis:
    """The model's inference beyond what the verified facts directly show.

    Always rendered as a visually distinct section from ``verified_facts``
    (ADR-006 Sec 3.2.2) -- never merged into one paragraph.
    """

    claim: str
    reasoning: str
    confidence: ConfidenceLevel


@dataclass(frozen=True)
class Insight:
    """One AI-generated conclusion, always evidence-backed (ADR-006 Sec 3.2.2)."""

    insight_id: str
    tenant_id: str | None
    """None = platform-wide insight."""
    category: InsightCategory
    severity: Severity
    verified_facts: tuple[VerifiedFact, ...]
    hypotheses: tuple[Hypothesis, ...]
    affected_components: tuple[str, ...]
    confidence_level: ConfidenceLevel
    model: str
    prompt_version: str
    generated_at: datetime
    recommendation: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.verified_facts:
            raise ValueError(
                "Insight must carry at least one VerifiedFact (ADR-006 Sec 3.2.2) -- "
                "an unevidenced AI claim may never be constructed, let alone stored."
            )


@dataclass(frozen=True)
class SourceServiceCall:
    """A frozen snapshot of a pre-existing service call a report's KPI numbers came from.

    Added in ADR-006 Rev 3 (Sec 6.2/13.8) so a report's numeric content --
    not just its AI narrative -- is reproducible: every number traces either
    to an Insight's verified_facts, or to one of these frozen calls.
    """

    service: str
    method: str
    params: dict[str, object]
    result_snapshot: dict[str, object]
    called_at: datetime


@dataclass(frozen=True)
class Report:
    """One generated report from the 9-type catalog (ADR-006 Sec 6.1)."""

    report_id: str
    report_type: ReportType
    period_start: datetime
    period_end: datetime
    scope_level: ScopeLevel
    tenant_id: str | None
    severity: Severity
    affected_components: tuple[str, ...]
    business_impact: str
    recommended_actions: tuple[str, ...]
    confidence_level: ConfidenceLevel
    evidence: tuple[Insight, ...]
    source_service_calls: tuple[SourceServiceCall, ...]
    narrative: str
    generated_at: datetime
    delivered_to: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.scope_level == ScopeLevel.PLATFORM and self.tenant_id is not None:
            raise ValueError("A platform-scoped Report must not carry a tenant_id.")
        if self.scope_level == ScopeLevel.TENANT and self.tenant_id is None:
            raise ValueError("A tenant-scoped Report must carry a tenant_id.")


# ---------------------------------------------------------------------------
# plumbing/ types (ADR-006 Sec 3.0/4 -- deterministic alert lifecycle, no AI)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertRecord:
    """One row of the always-on alert lifecycle (``plumbing/alert_lifecycle.py``).

    Carries zero AI-generated content and has zero dependency on the
    reasoning model -- this type (and everything that produces/consumes it)
    must keep working exactly as-is when ``reasoning/`` is fully disabled
    (ADR-006 Sec 3.0/13.12).
    """

    alert_id: str
    tenant_id: str | None
    source: AlertSource
    fingerprint: str
    severity: Severity
    status: AlertStatus
    fired_at: datetime
    labels: dict[str, str]
    annotations: dict[str, str]
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    escalated_at: datetime | None = None
    escalated_to: str | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class PatternSignature:
    """A recurring-issue fingerprint (ADR-006 Sec 3.5) -- plain hash/counter, no ML."""

    signature_id: str
    tenant_id: str | None
    fingerprint_hash: str
    category: str
    affected_components: tuple[str, ...]
    root_cause_summary: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int


__all__ = [
    "AlertRecord",
    "AlertSource",
    "AlertStatus",
    "ConfidenceLevel",
    "Hypothesis",
    "Insight",
    "InsightCategory",
    "PatternSignature",
    "Report",
    "ReportType",
    "ScopeLevel",
    "Severity",
    "SourceServiceCall",
    "VerifiedFact",
]
