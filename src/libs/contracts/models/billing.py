"""Persistent data models for subscription billing and usage metering.

VoiceOS uses a usage-based billing model. Usage events are aggregated
hourly and invoiced monthly per tenant. All monetary amounts in minor
currency units.

Architecture: V5 Ch7 (Usage Metering); V5 Ch8 (Billing & Invoicing).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId


class SubscriptionTier(StrEnum):
    """Billing tier determining base rate and feature access (V5 Ch8.2).

    ``TRIAL`` added Sprint-024 (V5 Ch9): a 30-day, 100-call, no-SLA
    onboarding tier distinct from ``STARTER`` — additive, does not replace
    or remove any Sprint-014 tier (see CHANGELOG.md Sprint-024 deviations).
    """

    TRIAL = "TRIAL"
    STARTER = "STARTER"
    GROWTH = "GROWTH"
    ENTERPRISE = "ENTERPRISE"
    ENTERPRISE_PLUS = "ENTERPRISE_PLUS"


class UsageType(StrEnum):
    """Billable usage dimension (V5 Ch7.2).

    ``STT_TOKEN``/``LLM_TOKEN``/``GPU_SECOND`` added Sprint-024 (V5 Ch10) to
    give AI-inference usage the per-model-stage granularity the Sprint-024
    invoice line items require; ``AI_TOKEN`` is kept for any caller that
    still emits a stage-agnostic token count (see CHANGELOG.md Sprint-024
    deviations).
    """

    CALL_MINUTE = "CALL_MINUTE"
    """Per-minute billing for active calls."""
    SMS_MESSAGE = "SMS_MESSAGE"
    """Per-message billing for outbound SMS."""
    API_CALL = "API_CALL"
    """Per-call billing for external API usage."""
    STORAGE_MB = "STORAGE_MB"
    """Per-MB billing for call recording / transcript storage."""
    AI_TOKEN = "AI_TOKEN"
    """Per-token billing for LLM inference (Enterprise tier only)."""
    STT_TOKEN = "STT_TOKEN"
    """Per-token billing for speech-to-text transcription (V5 Ch10)."""
    LLM_TOKEN = "LLM_TOKEN"
    """Per-token billing for LLM response generation (V5 Ch10)."""
    GPU_SECOND = "GPU_SECOND"
    """Per-second billing for allocated GPU inference time (V5 Ch10)."""


class InvoiceStatus(StrEnum):
    """Lifecycle status of an invoice."""

    DRAFT = "DRAFT"
    """Invoice being assembled (not yet sent)."""
    ISSUED = "ISSUED"
    """Invoice sent to the tenant."""
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    VOID = "VOID"
    """Invoice cancelled (e.g. billing error correction)."""


class UsageEvent(BaseModel):
    """A single billable consumption event for a tenant (V5 Ch7.3).

    High-frequency events (CALL_MINUTE, AI_TOKEN) are pre-aggregated
    into hourly buckets before being written to this model to keep
    the table size manageable.
    """

    model_config = ConfigDict(frozen=True)

    usage_event_id: str = Field(min_length=1)
    tenant_id: TenantId
    usage_type: UsageType
    quantity: int = Field(ge=0)
    """Quantity of units consumed."""
    unit_cost_minor: int = Field(ge=0)
    """Cost per unit in minor currency units (from rate card at time of usage)."""
    total_cost_minor: int = Field(ge=0)
    """Total cost = quantity x unit_cost_minor."""
    currency: str = Field(min_length=3, max_length=3)
    resource_id: str = ""
    """Optional reference to the resource (e.g. call_id, campaign_id)."""
    occurred_at_bucket: str
    """ISO-8601 UTC hour bucket (e.g. '2026-06-30T14:00:00Z') for aggregation."""
    invoice_id: str = ""
    """Set when this event has been included in an invoice."""


class InvoiceLineItem(BaseModel):
    """A single aggregated usage line on an invoice (V5 Ch9, Sprint-024).

    One line item per ``UsageType`` billed in the period (plus the
    subscription base fee, ``usage_type=None``). Persisted as a JSONB array
    on ``invoices.line_items`` (migration 0021) — additive to the flat
    ``invoices`` schema from Sprint-014, not a separate table (see
    CHANGELOG.md Sprint-024 deviations: "billing_invoices" in the sprint
    spec is this repo's pre-existing ``invoices`` table).
    """

    model_config = ConfigDict(frozen=True)

    usage_type: UsageType | None = None
    """None for the flat subscription base-fee line item."""
    description: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    unit_cost_minor: int = Field(ge=0)
    total_minor: int = Field(ge=0)


class Invoice(BaseModel):
    """A monthly billing invoice generated for a tenant (V5 Ch8.3).

    Invoices are generated on the 1st of each month for the prior period.
    The ``line_items`` tuple contains aggregated usage lines by UsageType.
    """

    model_config = ConfigDict(frozen=True)

    invoice_id: str = Field(min_length=1)
    tenant_id: TenantId
    billing_period_start: datetime
    billing_period_end: datetime
    subtotal_minor: int = Field(ge=0)
    """Sum of all line item totals before tax."""
    tax_minor: int = Field(default=0, ge=0)
    """Tax amount in minor currency units (GST / VAT)."""
    total_minor: int = Field(ge=0)
    """Final invoice total = subtotal + tax."""
    currency: str = Field(min_length=3, max_length=3)
    status: InvoiceStatus = InvoiceStatus.DRAFT
    issued_at: datetime | None = None
    paid_at: datetime | None = None
    due_date: datetime | None = None
    """Payment due date (typically 30 days after issued_at)."""
    payment_reference: str = ""
    """Payment gateway transaction reference, if paid."""
    line_items: tuple[InvoiceLineItem, ...] = ()
    """Aggregated usage line items (Sprint-024, additive — persisted as JSONB)."""
    created_at: datetime
    updated_at: datetime


class BillingSubscription(BaseModel):
    """A tenant's active billing subscription (V5 Ch8.2).

    Tracks the current tier, contract dates, and rate card version.
    Rate card changes are effective from the next billing cycle.
    """

    model_config = ConfigDict(frozen=True)

    subscription_id: str = Field(min_length=1)
    tenant_id: TenantId
    tier: SubscriptionTier
    rate_card_version: str = Field(min_length=1)
    """Version identifier for the rate card applied to this subscription."""
    contract_start: datetime
    contract_end: datetime | None = None
    """None for month-to-month subscriptions."""
    base_fee_minor: int = Field(default=0, ge=0)
    """Monthly base/platform fee in minor currency units."""
    currency: str = Field(default="INR", min_length=3, max_length=3)
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


__all__ = [
    "BillingSubscription",
    "Invoice",
    "InvoiceLineItem",
    "InvoiceStatus",
    "SubscriptionTier",
    "UsageEvent",
    "UsageType",
]
