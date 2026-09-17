"""EvidenceBundler -- deterministic anomaly/regression detection + evidence assembly.

ADR-006 Sec 3.2.4: "detection/regression logic is plain code, never the
LLM." This module NEVER calls a reasoning model -- it only reads the
existing observability stack (Prometheus/Loki/Jaeger/event bus, ADR-006
Sec 3.2.1 single-source-of-truth) and produces a closed set of
:class:`VerifiedFact` objects the reasoning adapter is later handed
(``adapters/protocol.py``'s ``NarrationRequest``).

Handles the ADR-006 Sec 13.11 failure-mode matrix directly:
  - a source query returning nothing -> that source is simply excluded,
    never fabricated (partial telemetry).
  - a source's data older than ``staleness_threshold_seconds`` -> excluded
    from ``verified_facts`` rather than silently presented as current
    (stale data).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from src.services.ops_intelligence.models import InsightCategory, Severity, VerifiedFact

DEFAULT_STALENESS_THRESHOLD_SECONDS = 300
DEFAULT_WARNING_PCT_CHANGE = 20.0
DEFAULT_CRITICAL_PCT_CHANGE = 50.0


@dataclass(frozen=True)
class MetricSample:
    value: float
    observed_at: datetime


class MetricsQueryPort(Protocol):
    """Prometheus/Thanos read port. Returns ``None`` when the query has no data (unavailable)."""

    def instant(self, query: str) -> MetricSample | None: ...


class LogQueryPort(Protocol):
    """Loki read port -- optional; correlation degrades gracefully without it."""

    def query(self, logql: str, *, limit: int = 20) -> tuple[str, ...]: ...


class TraceQueryPort(Protocol):
    """Jaeger/OTel read port -- optional; correlation degrades gracefully without it."""

    def query(self, trace_query: str, *, limit: int = 5) -> tuple[str, ...]: ...


class EventBusQueryPort(Protocol):
    """``voiceos-events`` read port -- also surfaces ComplianceAlerter-published signals."""

    def recent(self, event_type: str, *, tenant_id: str | None, limit: int = 20) -> tuple[dict[str, object], ...]: ...


@dataclass(frozen=True)
class MetricCheckSpec:
    """One metric to check for a regression/anomaly, plain-code thresholded."""

    name: str
    query: str
    baseline_query: str
    comparison: Literal["higher_is_worse", "lower_is_worse"]
    affected_component: str
    warning_pct_change: float = DEFAULT_WARNING_PCT_CHANGE
    critical_pct_change: float = DEFAULT_CRITICAL_PCT_CHANGE
    category: InsightCategory = InsightCategory.REGRESSION


@dataclass(frozen=True)
class EvidenceBundle:
    """Deterministically-assembled evidence for one flagged deviation."""

    category: InsightCategory
    severity: Severity
    verified_facts: tuple[VerifiedFact, ...]
    affected_components: tuple[str, ...]
    tenant_id: str | None


class EvidenceBundler:
    """Runs plain-code threshold checks and assembles the resulting evidence."""

    def __init__(
        self,
        metrics: MetricsQueryPort,
        logs: LogQueryPort | None = None,
        traces: TraceQueryPort | None = None,
        event_bus: EventBusQueryPort | None = None,
        *,
        staleness_threshold_seconds: int = DEFAULT_STALENESS_THRESHOLD_SECONDS,
        now_fn: object = datetime.now,
    ) -> None:
        self._metrics = metrics
        self._logs = logs
        self._traces = traces
        self._event_bus = event_bus
        self._staleness_threshold_seconds = staleness_threshold_seconds
        self._now_fn = now_fn

    def check_regression(self, spec: MetricCheckSpec, *, tenant_id: str | None = None) -> EvidenceBundle | None:
        """Returns an :class:`EvidenceBundle` only if the metric deviated past the warning threshold.

        Returns ``None`` when: the current metric is unavailable (partial
        telemetry), the baseline is unavailable or zero (division-by-zero
        guard), or the deviation is within the acceptable range (no
        anomaly). This is the deterministic gate -- nothing past this point
        is ever handed to the LLM to "decide" is or isn't anomalous.
        """
        current = self._metrics.instant(spec.query)
        if current is None:
            return None

        baseline = self._metrics.instant(spec.baseline_query)
        if baseline is None or baseline.value == 0:
            return None

        raw_pct_change = ((current.value - baseline.value) / baseline.value) * 100.0
        signed_pct_change = raw_pct_change if spec.comparison == "higher_is_worse" else -raw_pct_change

        if signed_pct_change < spec.warning_pct_change:
            return None

        severity = Severity.CRITICAL if signed_pct_change >= spec.critical_pct_change else Severity.WARNING

        facts: list[VerifiedFact] = []
        current_fact = self._fact_from_sample(
            claim=f"{spec.name} is currently {current.value:g} (baseline {baseline.value:g}, "
            f"{signed_pct_change:+.1f}% change)",
            source="prometheus",
            query=spec.query,
            sample=current,
        )
        if current_fact is not None:
            facts.append(current_fact)
        baseline_fact = self._fact_from_sample(
            claim=f"{spec.name} baseline value",
            source="prometheus",
            query=spec.baseline_query,
            sample=baseline,
        )
        if baseline_fact is not None:
            facts.append(baseline_fact)

        if not facts:
            # Both samples existed but were too stale to cite -- no evidence
            # survives, so no Insight may be constructed (ADR-006 Sec 3.2.2).
            return None

        facts.extend(self._correlate_logs(spec, tenant_id))
        facts.extend(self._correlate_traces(spec))

        return EvidenceBundle(
            category=spec.category,
            severity=severity,
            verified_facts=tuple(facts),
            affected_components=(spec.affected_component,),
            tenant_id=tenant_id,
        )

    def _fact_from_sample(self, *, claim: str, source: str, query: str, sample: MetricSample) -> VerifiedFact | None:
        age_seconds = (self._now() - sample.observed_at).total_seconds()
        if age_seconds > self._staleness_threshold_seconds:
            return None
        return VerifiedFact(claim=claim, source=source, query=query, value=f"{sample.value:g}", observed_at=sample.observed_at)

    def _correlate_logs(self, spec: MetricCheckSpec, tenant_id: str | None) -> tuple[VerifiedFact, ...]:
        if self._logs is None:
            return ()
        logql = f'{{service="{spec.affected_component}"}} |= "error"' if tenant_id is None else (
            f'{{service="{spec.affected_component}", tenant_id="{tenant_id}"}} |= "error"'
        )
        lines = self._logs.query(logql, limit=5)
        if not lines:
            return ()
        return (
            VerifiedFact(
                claim=f"{len(lines)} recent error log line(s) from {spec.affected_component}",
                source="loki",
                query=logql,
                value=str(len(lines)),
                observed_at=self._now(),
            ),
        )

    def _correlate_traces(self, spec: MetricCheckSpec) -> tuple[VerifiedFact, ...]:
        if self._traces is None:
            return ()
        trace_query = f"service={spec.affected_component}"
        traces = self._traces.query(trace_query, limit=5)
        if not traces:
            return ()
        return (
            VerifiedFact(
                claim=f"{len(traces)} slow/errored trace(s) found for {spec.affected_component}",
                source="jaeger",
                query=trace_query,
                value=str(len(traces)),
                observed_at=self._now(),
            ),
        )

    def _now(self) -> datetime:
        return self._now_fn() if callable(self._now_fn) else datetime.now(UTC)


__all__ = [
    "DEFAULT_CRITICAL_PCT_CHANGE",
    "DEFAULT_STALENESS_THRESHOLD_SECONDS",
    "DEFAULT_WARNING_PCT_CHANGE",
    "EventBusQueryPort",
    "EvidenceBundle",
    "EvidenceBundler",
    "LogQueryPort",
    "MetricCheckSpec",
    "MetricSample",
    "MetricsQueryPort",
    "TraceQueryPort",
]
