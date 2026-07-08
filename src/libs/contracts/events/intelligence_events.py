"""Domain events from the Conversation Intelligence Layer (CIL) engines.

Emitted by the IntentEngine, EntityExtractor, RiskEngine, StrategyEngine,
NegotiationEngine, and Conversation Engine as they process each turn.
Consumed by the Replay system, Analytics, and the Audit trail.

Architecture: V2 Ch3 (IntentEngine), V2 Ch4 (StrategyEngine),
              V2 Ch6 (RiskEngine), V2 Ch8 (NegotiationEngine),
              V2 Ch15 (ResponsePlan); V3 Ch3; V6 Ch6 EV-1.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..primitives import CallId
from .envelope import DomainEvent


class IntentClassified(DomainEvent):
    """Emitted by the IntentEngine after classifying a customer turn (V2 Ch3).

    One event per turn. ``label`` uses the IntentLabel vocabulary defined in
    Sprint-001 ``response_plan.py``. The DecisionEnvelope records the full
    decision lineage; this event provides the lightweight audit signal.
    """

    event_type: Literal["intelligence.intent.classified"] = "intelligence.intent.classified"
    call_id: CallId
    turn_id: str
    label: str
    """IntentLabel value (e.g. 'PAYMENT', 'DISPUTE', 'HARDSHIP')."""
    confidence: float = Field(ge=0.0, le=1.0)
    source_span: str = ""
    """The transcript span that most strongly supported this classification."""


class EntityExtracted(DomainEvent):
    """Emitted for each entity extracted from a customer turn.

    One event per extracted entity (multiple events per turn are possible).
    Entity values are authoritative customer data and must be sourced from
    the CRM/collections system before being used in facts (RI-5).

    Architecture: V2 Ch3.7 (NER); Invariant RI-5.
    """

    event_type: Literal["intelligence.entity.extracted"] = "intelligence.entity.extracted"
    call_id: CallId
    turn_id: str
    entity_key: str
    """Canonical entity key (e.g. 'promised_amount', 'callback_date')."""
    entity_value: str
    """String representation of the extracted value (typed parsing done by consumer)."""
    confidence: float = Field(ge=0.0, le=1.0)


class RiskFlagRaised(DomainEvent):
    """Emitted by the RiskEngine when it detects a risk condition (V2 Ch6).

    Consumed by the PolicyEngine (Sprint-017) and the AlertRouter
    (Sprint-027) for escalation handling.
    """

    event_type: Literal["intelligence.risk.flag_raised"] = "intelligence.risk.flag_raised"
    call_id: CallId
    flag_id: str
    """Stable risk flag identifier (e.g. 'ABUSE_DETECTED', 'LEGAL_THREAT')."""
    level: str
    """RiskLevel value: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'."""
    description: str
    """Human-readable description of the detected condition."""


class StrategySelected(DomainEvent):
    """Emitted by the StrategyEngine when it selects an action for the turn (V2 Ch4).

    The ``action`` drives the DeliverySpec and NegotiationEnvelope fields of
    the ResponsePlan assembled by the Conversation Engine.
    """

    event_type: Literal["intelligence.strategy.selected"] = "intelligence.strategy.selected"
    call_id: CallId
    turn_id: str
    action: str
    """StrategyLabel value (e.g. 'NEGOTIATE', 'REASSURE', 'CLOSE')."""
    rationale: str
    """One-line explanation logged for deterministic replay and audit."""
    confidence: float = Field(ge=0.0, le=1.0)


class NegotiationMoveProposed(DomainEvent):
    """Emitted by the NegotiationEngine when it proposes a move (V2 Ch8).

    Carries the proposed amount so analytics can measure concession behaviour
    over the call without deserializing the full ResponsePlan.
    """

    event_type: Literal["intelligence.negotiation.move_proposed"] = "intelligence.negotiation.move_proposed"
    call_id: CallId
    turn_id: str
    move_type: str
    """NegotiationMoveType value (e.g. 'PARTIAL_PAYMENT', 'EMI_RESTRUCTURE')."""
    proposed_amount_minor: int = Field(ge=0)
    """Proposed amount in minor currency units (paise/cents)."""
    currency: str
    """ISO 4217 currency code (e.g. 'INR', 'USD')."""
    concession_count: int = Field(ge=0)
    """Number of concessions made so far in this call (floor tracking)."""


class ResponsePlanAssembled(DomainEvent):
    """Emitted by the Conversation Engine when a ResponsePlan is fully assembled.

    Immediately precedes ResponsePlanCreated — this event signals the
    assembly is done before the plan is sealed (AR-4). Used for latency
    measurement of the CIL → plan boundary.

    Architecture: V1 Ch10 (Conversation Engine); V2 Ch15; V6 AR-4.
    """

    event_type: Literal["intelligence.response_plan.assembled"] = "intelligence.response_plan.assembled"
    call_id: CallId
    turn_id: str
    plan_id: str
    plan_version: int = Field(ge=1)
    engine_latency_ms: int = Field(ge=0)
    """Total CIL processing time from TurnStarted to plan assembly in milliseconds."""


__all__ = [
    "EntityExtracted",
    "IntentClassified",
    "NegotiationMoveProposed",
    "ResponsePlanAssembled",
    "RiskFlagRaised",
    "StrategySelected",
]
