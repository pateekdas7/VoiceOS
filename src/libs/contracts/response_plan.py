"""ResponsePlan and all associated types.

The ResponsePlan is the sealed, versioned, immutable unit of agent output
produced by the Conversation Engine. Nothing reaches the LLM / TTS / playback
path without a valid ResponsePlan (AR-4). Every field is deterministic and
authoritative — the LLM only renders language *within* the envelope, never
invents values (Law of Authority, RI-5).

Architecture: V1 Ch10, V1 Appendix A; V2 Ch15; V6 AR-4; DocSuite-02 A.3;
              DocSuite-03 (Data Dictionary).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared enumerations used by intelligence engines and delivery
# ---------------------------------------------------------------------------


class IntentLabel(StrEnum):
    """The 13 intent labels recognised by the IntentEngine (Sprint-010).

    Defined here in Sprint-001 so the contracts layer is complete before the
    engine is built. Labels match V2 Ch3 exactly.
    """

    PAYMENT = "PAYMENT"
    PROMISE_TO_PAY = "PROMISE_TO_PAY"
    DISPUTE = "DISPUTE"
    HARDSHIP = "HARDSHIP"
    CALLBACK = "CALLBACK"
    UNAVAILABLE = "UNAVAILABLE"
    DISCONNECT = "DISCONNECT"
    ABUSE = "ABUSE"
    IDENTITY_VERIFY = "IDENTITY_VERIFY"
    CONSENT_GRANT = "CONSENT_GRANT"
    CONSENT_REVOKE = "CONSENT_REVOKE"
    SILENCE = "SILENCE"
    OTHER = "OTHER"


class StrategyLabel(StrEnum):
    """Actions available to the StrategyEngine (Sprint-011).

    The StrategyEngine selects exactly one action per turn. The selected
    action drives the DeliverySpec and NegotiationEnvelope fields of the
    ResponsePlan.

    Architecture: V2 Ch4.
    """

    ASK = "ASK"
    VERIFY = "VERIFY"
    NEGOTIATE = "NEGOTIATE"
    REASSURE = "REASSURE"
    ESCALATE = "ESCALATE"
    TRANSFER = "TRANSFER"
    CLOSE = "CLOSE"
    CONFIRM = "CONFIRM"


class RiskLevel(StrEnum):
    """Severity levels for risk flags raised by the RiskEngine (Sprint-011).

    Architecture: V2 Ch6.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class NegotiationMoveType(StrEnum):
    """Types of negotiation moves the NegotiationEngine may propose.

    Architecture: V2 Ch8.
    """

    FULL_PAYMENT = "FULL_PAYMENT"
    PARTIAL_PAYMENT = "PARTIAL_PAYMENT"
    EMI_RESTRUCTURE = "EMI_RESTRUCTURE"
    SETTLEMENT = "SETTLEMENT"
    CALLBACK_SCHEDULE = "CALLBACK_SCHEDULE"
    HARDSHIP_PLAN = "HARDSHIP_PLAN"


# ---------------------------------------------------------------------------
# Component types composing ResponsePlan
# ---------------------------------------------------------------------------


class IntentSignal(BaseModel):
    """A single intent recognised in the current turn.

    The IntentEngine produces a ranked list of IntentSignals. The Conversation
    Engine uses the top-ranked intent (highest confidence) as the primary
    driver; secondary intents inform context.
    """

    model_config = ConfigDict(frozen=True)

    label: IntentLabel
    """The classified intent label."""

    confidence: float = Field(ge=0.0, le=1.0)
    """Classifier confidence score [0.0, 1.0]."""

    source_span: str = ""
    """The utterance span that drove this intent classification (for explainability)."""


class EmotionSpec(BaseModel):
    """Emotion state signal derived from the EmotionIntelligenceEngine (Sprint-010).

    Carries sentiment polarity and arousal estimation. Used by EmpathyPlanner
    to adapt tone and pacing in the delivery layer.

    Architecture: V1 Ch19, V2 Ch13.
    """

    model_config = ConfigDict(frozen=True)

    sentiment: float = Field(ge=-1.0, le=1.0)
    """Sentiment polarity: -1.0 = very negative, 0.0 = neutral, 1.0 = very positive."""

    arousal: float = Field(ge=0.0, le=1.0)
    """Emotional arousal (activation) level [0.0, 1.0]. High = distressed/excited."""

    dominant_emotion: str = "neutral"
    """Human-readable dominant emotion label (e.g. 'frustrated', 'cooperative')."""


class PolicyConstraint(BaseModel):
    """A single policy constraint injected by the Policy Engine (Sprint-017).

    Constraints populate must_say / must_not_say and gate negotiation moves.
    The LLM is never the source of policy constraints; they arrive pre-computed
    from the Policy Engine (AR-7).

    Architecture: V4 Ch4, V2 Ch8.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    """Stable rule identifier from the Policy DSL (e.g. 'RBI-FC-001')."""

    description: str
    """Human-readable description of the constraint for audit and explainability."""

    is_hard_rule: bool = True
    """True if this rule can never be waived; False if it can be overridden by supervisor."""


class RiskFlag(BaseModel):
    """A risk signal raised by the RiskEngine (Sprint-011).

    Risk flags gate strategy choices and escalation decisions. A CRITICAL flag
    may trigger immediate escalation or call termination.

    Architecture: V2 Ch6.
    """

    model_config = ConfigDict(frozen=True)

    flag_id: str
    """Stable risk flag identifier (e.g. 'ABUSE_DETECTED', 'ESCALATION_REQUIRED')."""

    level: RiskLevel
    """Severity of this risk flag."""

    description: str
    """Explanation for audit trail and supervisor display."""


class StrategyAction(BaseModel):
    """The selected strategy action for this turn.

    Produced by the StrategyEngine (Sprint-011). Exactly one StrategyAction
    per ResponsePlan. All negotiation moves are bounded by the NegotiationEnvelope.

    Architecture: V2 Ch4.
    """

    model_config = ConfigDict(frozen=True)

    action: StrategyLabel
    """The selected action type."""

    rationale: str = ""
    """Reasoning for this action selection (audit/explainability)."""


class NegotiationEnvelope(BaseModel):
    """Bounded negotiation parameters produced by the NegotiationEngine (Sprint-011).

    The floor/ceiling clamp is non-bypassable by the LLM or any downstream
    component. No offer may be generated outside this envelope (AR-4, RI-5).

    Architecture: V2 Ch8.
    """

    model_config = ConfigDict(frozen=True)

    floor_minor: int = Field(ge=0)
    """Minimum acceptable payment amount in minor currency units."""

    ceiling_minor: int = Field(ge=0)
    """Maximum offered amount in minor currency units (e.g., full outstanding)."""

    move_type: NegotiationMoveType
    """The class of negotiation move to attempt."""

    max_concession_count: int = Field(default=2, ge=0)
    """Maximum number of concessions allowed this turn."""

    proposed_amount_minor: int | None = None
    """Initial offer amount in minor units. Must be within [floor_minor, ceiling_minor]."""

    proposed_date: date | None = None
    """Proposed commitment/payment date for ACCEPT and PROPOSE_PTP moves. None for
    moves that don't finalize a specific date (OFFER/COUNTER/DECLINE/HOLD)."""

    is_finalized_commitment: bool = False
    """True only for the engine-level NegotiationMove.ACCEPT/PROPOSE_PTP moves —
    a customer commitment ready for Collections persistence. Deliberately NOT
    derived from move_type: NegotiationMoveType collapses ACCEPT/COUNTER/OFFER/
    DECLINE into overlapping contract categories (e.g. both HOLD and
    PROPOSE_PTP map to CALLBACK_SCHEDULE), so move_type alone cannot
    distinguish a finalized commitment from an in-progress negotiation move."""


class DeliverySpec(BaseModel):
    """How the response should be delivered: language, voice, pacing.

    Produced by the Conversation Engine and EmpathyPlanner. Drives the TTS
    and Speech Rendering layers.

    Architecture: V1 Ch15-16, V2 Ch14.
    """

    model_config = ConfigDict(frozen=True)

    language: str = "hi-IN"
    """BCP-47 language tag for the response (e.g., 'hi-IN', 'en-IN')."""

    voice_id: str = "veena-default"
    """Voice profile identifier from the approved catalog (V5 Ch14)."""

    target_speaking_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    """Speaking rate multiplier. 1.0 = natural cadence."""

    pause_after_greeting_ms: int = Field(default=200, ge=0)
    """Pause inserted after greeting phrases for naturalness."""

    max_response_tokens: int = Field(default=150, ge=10, le=500)
    """Maximum LLM output tokens. Prevents verbosity that violates must_not_say."""


class MustSayItem(BaseModel):
    """A mandatory phrase or disclosure that the agent must include.

    Injected by the Policy Engine (AR-7). The OutputValidator verifies these
    items appear in the generated text before allowing TTS.

    Architecture: V4 Ch4, V2 Ch8; V1 Ch14.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    """Stable identifier for this mandatory item (e.g., 'RBI_CONSENT_DISCLOSURE')."""

    text: str
    """The exact text or paraphrase requirement."""

    is_exact_match: bool = False
    """If True, the text must appear verbatim; if False, semantic matching is used."""


class MustNotSayItem(BaseModel):
    """A phrase or topic the agent must not mention or imply.

    Injected by the Policy Engine (AR-7). The OutputValidator rejects any
    LLM output that violates these constraints.

    Architecture: V4 Ch4, V2 Ch8; V1 Ch14.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    """Stable identifier for this prohibition (e.g., 'NO_THREAT_OF_LEGAL_ACTION')."""

    description: str
    """Human-readable description of the prohibition for audit."""

    pattern: str = ""
    """Optional regex or keyword pattern used by the OutputValidator."""


class Snippet(BaseModel):
    """A retrieved knowledge snippet attached to the ResponsePlan.

    Populated by the KnowledgeRetrievalService (Sprint-012). Snippets are
    *evidence* for the LLM — they are never authoritative data (RI-5). The
    LLM may reference them for phrasing but must not quote amounts or dates
    from them as authoritative.

    Architecture: V2 Ch20 (Retrieval); Sprint-001 spec.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    """Identifier of the knowledge source (e.g., 'faq_v3', 'product_guide_2024')."""

    content: str
    """The retrieved text snippet."""

    relevance_score: float = Field(ge=0.0, le=1.0)
    """Retrieval relevance score [0.0, 1.0]."""


# RetrievalResult is an alias for Snippet for backward-compatibility
# with references in CURRENT_SPRINT.md.
RetrievalResult = Snippet


# FactMap: authoritative facts injected from the system of record.
# Values are typed primitives — never inferred, never from the LLM (RI-5).
# Using object here rather than Any to keep strict typing; callers must
# type-narrow when reading individual values.
FactValue = str | int | float | bool | None
FactMap = dict[str, FactValue]
"""Mapping of authoritative fact keys to typed values.

Populated from the CRM and collections system of record (V5 Ch4-5).
These facts drive amounts, dates, and disclosures in the LLM prompt.
The Law of Authority (RI-5) requires that all customer-facing facts
originate here, never from the LLM.
"""


# ---------------------------------------------------------------------------
# ResponsePlan — the sealed, versioned unit of agent output (AR-4)
# ---------------------------------------------------------------------------


class ResponsePlan(BaseModel):
    """Sealed, versioned, immutable unit of agent output for one turn.

    The Conversation Engine produces exactly one ResponsePlan per turn.
    Nothing reaches the LLM prompt builder or TTS without a valid plan.
    The plan carries both *what to say* (from policy and authority sources)
    and *how to say it* (delivery, emotion, strategy) — never raw decisions.

    Immutability: model_config frozen=True prevents attribute mutation after
    construction (AC-7). Mutation attempts raise ValidationError at runtime.

    Architecture: V1 Ch10, V1 Appendix A; V2 Ch15; V6 AR-4.
    Invariants: RI-5 (facts from authoritative source), RI-6 (output coherence).
    """

    model_config = ConfigDict(frozen=True)

    plan_id: str
    """Globally unique ResponsePlan identifier (UUID).
    Also the key for RI-6 output coherence checks."""

    version: int = Field(ge=1)
    """Plan schema version. Increments when the shape changes (EV-4)."""

    call_id: str
    """Parent call session this plan was generated for."""

    tenant_id: str
    """Tenant scope. All downstream components must respect this (AR-8)."""

    created_at: datetime
    """UTC timestamp when this plan was sealed by the Conversation Engine."""

    intents: tuple[IntentSignal, ...] = ()
    """Ranked list of intents detected in the current turn (IntentEngine output)."""

    entities: dict[str, Any] = Field(default_factory=dict)
    """Extracted entity slots from the current turn.
    Values are unstructured by design (different intents have different slot shapes).
    NOTE: This is an explicitly permitted use of Any per AC-6 and the architecture
    spec (V1 Appendix A). Callers must type-narrow when consuming values."""

    emotion: EmotionSpec = Field(default_factory=lambda: EmotionSpec(sentiment=0.0, arousal=0.3))
    """Emotion signal from the EmotionIntelligenceEngine."""

    policy_constraints: tuple[PolicyConstraint, ...] = ()
    """Policy constraints injected by the Policy Engine (AR-7)."""

    risk_flags: tuple[RiskFlag, ...] = ()
    """Risk flags raised by the RiskEngine."""

    goal: str = ""
    """Natural-language statement of the agent's goal for this turn."""

    strategy: StrategyAction = Field(default_factory=lambda: StrategyAction(action=StrategyLabel.ASK))
    """Selected strategy action for this turn (StrategyEngine output)."""

    negotiation_envelope: NegotiationEnvelope | None = None
    """Negotiation bounds when strategy.action == NEGOTIATE. None otherwise."""

    delivery: DeliverySpec = Field(default_factory=DeliverySpec)
    """Delivery specification for TTS and Speech Rendering layers."""

    facts: FactMap = Field(default_factory=dict)
    """Authoritative facts from the system of record (RI-5).
    Never populated from LLM output. Keys match the Data Dictionary (DocSuite-03)."""

    retrieval: list[Snippet] = Field(default_factory=list)
    """Knowledge snippets for evidence context. Evidence only — not authority (RI-5)."""

    must_say: tuple[MustSayItem, ...] = ()
    """Mandatory disclosures/phrases injected by the Policy Engine (AR-7)."""

    must_not_say: tuple[MustNotSayItem, ...] = ()
    """Prohibited phrases/topics injected by the Policy Engine (AR-7)."""
