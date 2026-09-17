"""WorkingMemory schema — per-call Redis-backed in-flight state.

WorkingMemory is the short-term state store for an active call. It tracks
turn count, last intent, extracted entity slots, negotiation state, strategy,
and utterance history. The TTL is 4 hours (maximum call duration).

Architecture: V2 Ch11 (Working Memory).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.libs.contracts.response_plan import IntentLabel


class WorkingMemory(BaseModel):
    """Per-call working memory snapshot.

    Stored as a JSON blob in Redis with a 4-hour TTL. The WorkingMemoryStore
    serializes and deserializes this type transparently.

    Architecture: V2 Ch11.
    """

    model_config = ConfigDict(frozen=True)

    call_id: str
    """Parent call session identifier. Used as the Redis key namespace."""

    turn_count: int = Field(default=0, ge=0)
    """Number of customer turns processed so far in this call."""

    last_intent: IntentLabel | None = None
    """The intent classified on the most recent customer turn."""

    extracted_entities: dict[str, str] = Field(default_factory=dict)
    """Latest entity slots extracted: {EntityType.value → normalized_value}."""

    negotiation_state: str = "initial"
    """Current negotiation state label (e.g., 'initial', 'offer_made', 'accepted')."""

    last_strategy: str | None = None
    """Strategy action applied on the most recent turn (StrategyLabel.value)."""

    agent_utterances: tuple[str, ...] = ()
    """Ordered history of agent utterance texts for this call."""

    customer_utterances: tuple[str, ...] = ()
    """Ordered history of customer utterance texts for this call."""

    sales_state: dict | None = None
    """Serialized SalesState dict from the Sales Intelligence Layer (Phase 2).
    None when the sales layer is not wired. Persisted across turns so
    SalesStateUpdater can access the previous turn's state."""


class WorkingMemoryDelta(BaseModel):
    """Partial update applied to an existing WorkingMemory.

    Non-None fields replace the corresponding WorkingMemory fields.
    None fields are left unchanged (merge semantics).

    Architecture: V2 Ch11.
    """

    model_config = ConfigDict(frozen=True)

    turn_count: int | None = None
    last_intent: IntentLabel | None = None
    extracted_entities: dict[str, str] | None = None
    negotiation_state: str | None = None
    last_strategy: str | None = None
    agent_utterances: tuple[str, ...] | None = None
    customer_utterances: tuple[str, ...] | None = None
    sales_state: dict | None = None
