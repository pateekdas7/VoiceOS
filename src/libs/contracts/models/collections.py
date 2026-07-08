"""Persistent data models for the collections workflow.

Covers Promise-To-Pay records, settlement agreements, callback scheduling,
and escalation tracking. All monetary amounts in minor currency units.

Architecture: V5 Ch4 (Collections Workflow); V5 Ch4.3 (PTP);
              V5 Ch4.5 (Disposition); V5 Ch4.6 (Escalation).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import CallId, CustomerId, TenantId


class PTPStatus(StrEnum):
    """Lifecycle status of a Promise-To-Pay."""

    PENDING = "PENDING"
    """PTP recorded; payment window not yet open."""
    KEPT = "KEPT"
    """Payment received on or before promise_date."""
    BROKEN = "BROKEN"
    """Payment not received by promise_date."""
    PARTIAL = "PARTIAL"
    """Partial payment received; promise not fully honoured."""
    CANCELLED = "CANCELLED"
    """PTP cancelled by agent or system (e.g. customer called back)."""


class SettlementStatus(StrEnum):
    """Lifecycle status of a settlement offer."""

    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    PAID = "PAID"


class PromiseToPay(BaseModel):
    """Record of a customer's commitment to pay on a specific date (V5 Ch4.3).

    PTPs are idempotency-protected: duplicate recording from barge-in or
    retry scenarios must be prevented using the idempotency key tied to the
    call_id + turn_id combination (V3 Ch8, Invariant EV-7).
    """

    model_config = ConfigDict(frozen=True)

    ptp_id: str = Field(min_length=1)
    """Internal PTP record identifier."""
    tenant_id: TenantId
    call_id: CallId
    customer_id: CustomerId
    loan_account_id: str = Field(min_length=1)
    promised_amount_minor: int = Field(ge=0)
    """Committed payment in minor currency units."""
    currency: str = Field(min_length=3, max_length=3)
    promise_date: datetime
    """Date by which the customer committed to pay."""
    status: PTPStatus = PTPStatus.PENDING
    recorded_at: datetime
    updated_at: datetime
    notes: str = ""
    """Agent-recorded notes about the commitment context."""


class Settlement(BaseModel):
    """A one-time settlement offer for closing a delinquent loan (V5 Ch4.4).

    Settlement amounts and expiry are authoritative CRM values (RI-5).
    The agent may present the offer but may not modify the amounts.
    """

    model_config = ConfigDict(frozen=True)

    settlement_id: str = Field(min_length=1)
    tenant_id: TenantId
    customer_id: CustomerId
    loan_account_id: str = Field(min_length=1)
    settlement_amount_minor: int = Field(ge=0)
    """Agreed settlement amount in minor currency units."""
    currency: str = Field(min_length=3, max_length=3)
    waiver_amount_minor: int = Field(default=0, ge=0)
    """Amount waived as part of the settlement."""
    offer_expiry: datetime
    """Offer is invalid after this timestamp."""
    status: SettlementStatus = SettlementStatus.PROPOSED
    proposed_at: datetime
    updated_at: datetime


class CallbackRequest(BaseModel):
    """A customer request to be called back at a specific time (V5 Ch4.5)."""

    model_config = ConfigDict(frozen=True)

    callback_id: str = Field(min_length=1)
    tenant_id: TenantId
    call_id: CallId
    customer_id: CustomerId
    loan_account_id: str = Field(min_length=1)
    preferred_time: datetime
    """Customer's requested callback datetime (in tenant's timezone)."""
    timezone: str = Field(default="Asia/Kolkata")
    """IANA timezone identifier for the preferred_time."""
    phone_number: str = Field(min_length=5, max_length=20)
    """Phone number to call back (may differ from primary contact)."""
    recorded_at: datetime
    is_fulfilled: bool = False


class EscalationRecord(BaseModel):
    """Record of a call escalation to a human agent or supervisor (V5 Ch4.6).

    Escalations are triggered by RiskFlagRaised events (CRITICAL/HIGH level)
    or explicit customer requests. The escalation pipeline is Sprint-027 scope.
    """

    model_config = ConfigDict(frozen=True)

    escalation_id: str = Field(min_length=1)
    tenant_id: TenantId
    call_id: CallId
    customer_id: CustomerId
    reason: str = Field(min_length=1)
    """Escalation reason code (e.g. 'ABUSE_DETECTED', 'DISPUTE', 'LEGAL_THREAT',
    'CUSTOMER_REQUEST', 'SYSTEM_ERROR')."""
    escalated_to: str = ""
    """Queue or agent ID the call was escalated to."""
    escalated_at: datetime
    resolved_at: datetime | None = None
    resolution_notes: str = ""


__all__ = [
    "CallbackRequest",
    "EscalationRecord",
    "PTPStatus",
    "PromiseToPay",
    "Settlement",
    "SettlementStatus",
]
