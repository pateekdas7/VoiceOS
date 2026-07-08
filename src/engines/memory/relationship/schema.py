"""RelationshipMemory schema — cross-call per-customer persistent state.

RelationshipMemory is the long-term memory store for a customer across all
calls. It persists PTP history, sentiment trends, best contact time, and
escalation count. Backed by PostgreSQL (relationship_memory table).

Architecture: V2 Ch12 (Relationship Memory).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PromiseRecord(BaseModel):
    """A single promise-to-pay (PTP) record from a past call."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    """The call in which the PTP was made."""

    promised_date: str
    """ISO 8601 date the customer promised to pay (e.g., '2026-07-05')."""

    amount_minor: int = Field(ge=0)
    """Amount promised in minor currency units (paise)."""

    fulfilled: bool = False
    """True if the payment was received by the promised date."""


class RelationshipMemory(BaseModel):
    """Per-customer relationship memory, persisted across calls.

    Architecture: V2 Ch12.
    """

    model_config = ConfigDict(frozen=True)

    customer_id: str
    """Authoritative customer identifier from the CRM system."""

    total_calls: int = Field(default=0, ge=0)
    """Total number of outbound/inbound calls handled for this customer."""

    ptp_history: tuple[PromiseRecord, ...] = ()
    """Ordered history of promise-to-pay records, newest last."""

    sentiment_history: tuple[str, ...] = ()
    """Ordered sentiment labels from past calls (Sentiment.value strings)."""

    best_contact_time: str = ""
    """Best known time window for reaching this customer (e.g., 'morning')."""

    preferred_language: str = "hi-IN"
    """BCP-47 language tag most effective for this customer."""

    escalation_count: int = Field(default=0, ge=0)
    """Number of calls that ended in escalation."""

    last_call_outcome: str = ""
    """Outcome label from the most recent completed call (e.g., 'ptp_made')."""


class CallSummary(BaseModel):
    """Summary of a completed call, used to update RelationshipMemory."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    sentiment: str
    """Sentiment.value label from the call's emotion analysis."""

    outcome: str
    """Outcome label (e.g., 'ptp_made', 'dispute_raised', 'callback_scheduled')."""

    escalated: bool = False
    ptp: PromiseRecord | None = None
    contact_time_label: str = ""
    language: str = "hi-IN"
