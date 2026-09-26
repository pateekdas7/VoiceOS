"""InsightService -- turns an EvidenceBundle + reasoning-model narration into a stored Insight.

Enforces ADR-006 Sec 3.2.2 end to end: an Insight is only ever constructed
(and only ever persisted) with a non-empty ``verified_facts`` set --
``Insight.__post_init__`` already guards this; this service treats that
guard tripping as "no insight generated this cycle," never as an error that
corrupts the analysis run.

Also implements the recurring-issue fingerprinting design (ADR-006 Sec 3.5):
plain deterministic hashing over (category, affected_components,
root_cause_summary) via :class:`PatternSignatureRepositoryPort` -- no model
retraining, no learned state, just a hash + counter.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from src.libs.contracts.events.saas_events import OpsIntelligenceAnalysisPerformed
from src.libs.contracts.primitives import TenantId
from src.services.ops_intelligence.models import Insight, PatternSignature
from src.services.ops_intelligence.reasoning.adapters.protocol import NarrationRequest, ReasoningAdapter
from src.services.ops_intelligence.reasoning.evidence_bundler import EvidenceBundle
from src.services.ops_intelligence.reasoning.repository import InsightRepositoryPort, PatternSignatureRepositoryPort


class UsageEventPublisherPort(Protocol):
    """Publishes the tenant-scoped cost/usage event (ADR-006 Sec 13.10) -- never a telemetry event.

    Distinct from the Sec 3.2.1 telemetry-emission ban: this is ONE
    business/usage domain event, the same pattern ``STTTranscribed``/
    ``LLMGenerated``/``GPUAllocated`` already use to feed ``UsageCollector``
    -- not a competing copy of metrics/logs/traces.
    """

    def publish(self, event: OpsIntelligenceAnalysisPerformed) -> None: ...


class CostObservabilityPort(Protocol):
    """Records platform-wide (non-tenant-billable) reasoning-model token spend.

    Kept as an injected port -- rather than importing ``prometheus_client``
    directly here -- so ``reasoning/`` never needs a telemetry-emission
    import (Rule 7, ``scripts/check_boundaries.py``); the concrete
    Prometheus counter lives in ``ops_intelligence.metrics`` (package root,
    outside ``reasoning/``).
    """

    def record_platform_wide_tokens(self, token_count: int) -> None: ...


class InsightService:
    def __init__(
        self,
        repository: InsightRepositoryPort,
        reasoning_adapter: ReasoningAdapter,
        pattern_repository: PatternSignatureRepositoryPort | None = None,
        usage_publisher: UsageEventPublisherPort | None = None,
        cost_observability: CostObservabilityPort | None = None,
    ) -> None:
        self._repository = repository
        self._reasoning_adapter = reasoning_adapter
        self._pattern_repository = pattern_repository
        self._usage_publisher = usage_publisher
        self._cost_observability = cost_observability

    async def generate(self, evidence: EvidenceBundle) -> Insight | None:
        """Produce and persist one Insight from ``evidence``, or ``None`` if it can't be evidenced.

        ``None`` is returned (never raised) when ``evidence.verified_facts``
        is empty -- this is the normal "nothing to report this cycle"
        outcome, not a failure.
        """
        if not evidence.verified_facts:
            return None

        request = NarrationRequest(
            category=evidence.category,
            tenant_id=evidence.tenant_id,
            verified_facts=evidence.verified_facts,
            candidate_affected_components=evidence.affected_components,
        )
        narration = await self._reasoning_adapter.narrate(request)

        insight = Insight(
            insight_id=str(uuid.uuid4()),
            tenant_id=evidence.tenant_id,
            category=evidence.category,
            severity=evidence.severity,
            verified_facts=evidence.verified_facts,
            hypotheses=narration.hypotheses,
            affected_components=evidence.affected_components,
            confidence_level=narration.confidence_level,
            model=narration.model,
            prompt_version=narration.prompt_version,
            generated_at=datetime.now(UTC),
            recommendation=narration.recommendation,
        )
        persisted = self._repository.create(insight)

        if self._pattern_repository is not None:
            self._record_pattern(persisted)

        self._publish_usage_event(persisted, narration.total_tokens)

        return persisted

    def _publish_usage_event(self, insight: Insight, total_tokens: int) -> None:
        """Meter this reasoning-model call (ADR-006 Sec 13.10).

        Tenant-scoped runs publish ``OpsIntelligenceAnalysisPerformed`` (fed
        into the standard tenant billing pipeline via ``UsageCollector``).
        Platform-wide runs (``insight.tenant_id is None``) are deliberately
        never published as a tenant usage event -- publishing with a
        placeholder tenant would misrepresent an infra cost as a customer's
        usage -- so they instead increment the plain Prometheus counter in
        ``ops_intelligence.metrics`` via :class:`CostObservabilityPort`.
        """
        if total_tokens <= 0:
            return
        if insight.tenant_id is not None:
            if self._usage_publisher is not None:
                self._usage_publisher.publish(
                    OpsIntelligenceAnalysisPerformed(
                        tenant_id=TenantId(insight.tenant_id),
                        insight_id=insight.insight_id,
                        token_count=total_tokens,
                    )
                )
        elif self._cost_observability is not None:
            self._cost_observability.record_platform_wide_tokens(total_tokens)

    def _record_pattern(self, insight: Insight) -> PatternSignature:
        root_cause_summary = insight.hypotheses[0].claim if insight.hypotheses else insight.category.value
        fingerprint_hash = _fingerprint(insight.category.value, insight.affected_components, root_cause_summary)
        existing = self._pattern_repository.find(fingerprint_hash, tenant_id=insight.tenant_id)  # type: ignore[union-attr]
        now = insight.generated_at
        if existing is None:
            signature = PatternSignature(
                signature_id=str(uuid.uuid4()),
                tenant_id=insight.tenant_id,
                fingerprint_hash=fingerprint_hash,
                category=insight.category.value,
                affected_components=insight.affected_components,
                root_cause_summary=root_cause_summary,
                first_seen=now,
                last_seen=now,
                occurrence_count=1,
            )
        else:
            signature = replace(existing, last_seen=now, occurrence_count=existing.occurrence_count + 1)
        return self._pattern_repository.upsert_occurrence(signature)  # type: ignore[union-attr]


def _fingerprint(category: str, affected_components: tuple[str, ...], root_cause_summary: str) -> str:
    raw = f"{category}:{','.join(sorted(affected_components))}:{root_cause_summary}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


__all__ = ["CostObservabilityPort", "InsightService", "UsageEventPublisherPort"]
