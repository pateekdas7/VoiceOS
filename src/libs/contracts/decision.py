"""DecisionEnvelope and associated types for decision lineage.

Every consequential decision in VoiceOS is recorded as a DecisionEnvelope
and emitted to the event log (AR-5). This provides the shared substrate for
audit (V4 Ch11), deterministic replay (V3 Ch7), explainability (V4 Ch3),
and outcome analytics (V5 Ch11).

Architecture: V1 Appendix A; V2 Ch15; V4 Ch3, Ch11; V6 AR-5;
              DocSuite-02 A.3; DocSuite-03.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class GovernanceStatus(StrEnum):
    """Result of the AI Governance / Law-of-Authority check (Sprint-018).

    The GovernanceLayer evaluates every LLM output against the sealed
    ResponsePlan.facts before it reaches TTS (AR-6). The verdict determines
    whether output proceeds, requires human oversight, or is blocked.

    Architecture: V4 Ch3, Ch14; V2 Ch17.
    """

    APPROVE = "APPROVE"
    """Output is compliant with the ResponsePlan and Law of Authority."""

    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    """Output is borderline; route to HITL supervisor queue for review."""

    BLOCK = "BLOCK"
    """Output violates the Law of Authority or a hard policy rule; reject."""


class GovernanceVerdict(BaseModel):
    """Result of the AI Governance check for a single LLM output.

    Attached to the DecisionEnvelope so every verdict is auditable and
    replayable. A BLOCK verdict means the turn was discarded and re-planned.

    Architecture: V4 Ch3; V2 Ch17; Sprint-018.
    """

    model_config = ConfigDict(frozen=True)

    status: GovernanceStatus
    """The overall governance verdict for this output."""

    violations: tuple[str, ...] = ()
    """Identifiers of any violated rules (e.g., 'LAW_OF_AUTHORITY_RI5')."""

    explanation: str = ""
    """Human-readable explanation for audit trail and supervisor dashboard."""

    checked_at: datetime = Field(default_factory=datetime.utcnow)
    """UTC timestamp when the governance check was performed."""


class DecisionReason(BaseModel):
    """Structured reason record explaining a single decision step.

    Captures the evidence, confidence, and source engine for full
    explainability chains. Used by the DecisionEnvelope to provide
    a reproducible reasoning trace.

    Architecture: V2 Ch15; V4 Ch3 (explainability).
    """

    model_config = ConfigDict(frozen=True)

    decision_id: str
    """Unique identifier for this decision step (UUID)."""

    source_engine: str
    """Name of the engine that made this decision (e.g., 'IntentEngine', 'RiskEngine')."""

    decision: str
    """The decision made (e.g., 'INTENT=PROMISE_TO_PAY', 'STRATEGY=NEGOTIATE')."""

    confidence: float = Field(ge=0.0, le=1.0)
    """Confidence score for this decision [0.0, 1.0]."""

    evidence: tuple[str, ...] = ()
    """Evidence items (utterance spans, fact keys) supporting this decision."""

    reasoning: str = ""
    """Natural-language explanation for human reviewers and audit."""

    engine_version: str = "1.0.0"
    """Version of the engine at the time of this decision (for replay fidelity)."""

    decided_at: datetime = Field(default_factory=datetime.utcnow)
    """UTC timestamp of this decision step."""


class DecisionRecord(BaseModel):
    """A single record within a DecisionEnvelope.

    Wraps a DecisionReason with additional call/tenant scoping for the
    event log and audit system.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str
    """Unique record identifier (UUID)."""

    call_id: str
    """Call session this record belongs to."""

    tenant_id: str
    """Tenant scope (AR-8)."""

    reason: DecisionReason
    """The detailed decision reason."""


class DecisionEnvelope(BaseModel):
    """The complete decision audit record for one agent turn.

    Produced by the Conversation Engine after assembling the ResponsePlan.
    Emitted to the event log (AR-5) before any external effect (RI-4).
    Contains every engine's contribution and the governance verdict.

    This is the cross-volume substrate:
    - V3 Ch3: appended to the event log as a domain event.
    - V4 Ch11: the immutable audit trail entry.
    - V2 Ch15: the reasoning record for deterministic replay.
    - V5 Ch11: analytics / outcome attribution input.

    Architecture: V1 Appendix A; V2 Ch15; V4 Ch3; V6 AR-5.
    Invariant: RI-4 (commit before act) — this envelope is persisted before
               any TTS synthesis or external call effect is initiated.
    """

    model_config = ConfigDict(frozen=True)

    envelope_id: str
    """Globally unique envelope identifier (UUID). Dedup key for the event log."""

    call_id: str
    """Call session this envelope covers."""

    tenant_id: str
    """Tenant scope (AR-8)."""

    timestamp: datetime
    """UTC timestamp when this envelope was sealed by the Conversation Engine."""

    decisions: tuple[DecisionRecord, ...] = ()
    """Ordered list of decision records, one per engine that contributed."""

    response_plan_id: str
    """ID of the ResponsePlan that was produced based on these decisions (RI-6)."""

    governance_verdict: GovernanceVerdict = Field(
        default_factory=lambda: GovernanceVerdict(status=GovernanceStatus.APPROVE)
    )
    """Result of the AI Governance / Law-of-Authority check (AR-6; Sprint-018)."""

    correlation_id: str = ""
    """Correlation ID linking this envelope to its parent request chain (EV-8)."""

    causation_id: str | None = None
    """The event ID that caused this decision envelope to be produced."""

    trace_id: str = ""
    """OpenTelemetry trace ID (V3 Ch17)."""
