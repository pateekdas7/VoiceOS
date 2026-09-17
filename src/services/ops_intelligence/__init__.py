"""Intelligent Analysis Layer (ADR-006) -- monitoring & intelligent operations.

Split into two hard-bounded sub-components (ADR-006 Sec 3.0):
  - ``plumbing/``: always-on, non-AI alert lifecycle. No dependency on
    ``reasoning/`` or any LLM adapter.
  - ``reasoning/``: the killable, AI-gated analysis/reporting/capacity core.
    Gated end-to-end by :class:`service.OpsIntelligenceService`.

Reuses the existing observability stack (Prometheus/Thanos, Loki, Jaeger,
Alertmanager, the ``voiceos-events`` event bus) as its ONLY data source
(Sec 3.2.1) -- this package never emits a competing copy of telemetry.
"""

from __future__ import annotations

from .models import (
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
from .service import PLATFORM_FLAG_TENANT_ID, REASONING_FLAG_NAME, OpsIntelligenceService

__all__ = [
    "PLATFORM_FLAG_TENANT_ID",
    "REASONING_FLAG_NAME",
    "AlertRecord",
    "AlertSource",
    "AlertStatus",
    "ConfidenceLevel",
    "Hypothesis",
    "Insight",
    "InsightCategory",
    "OpsIntelligenceService",
    "PatternSignature",
    "Report",
    "ReportType",
    "ScopeLevel",
    "Severity",
    "SourceServiceCall",
    "VerifiedFact",
]
