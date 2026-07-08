"""Domain events for the SaaS platform layer.

Covers tenant lifecycle, customer CRM records, loan account mutations,
collections workflow (PTP, campaign), call disposition, usage metering,
and billing invoice generation.

Architecture: V5 Ch2 (Tenant Management), V5 Ch3 (Customer CRM),
              V5 Ch4 (Loan & Collections), V5 Ch6 (Campaigns),
              V5 Ch7 (Usage & Billing); V3 Ch3 (Event Bus); V6 Ch6 EV-1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..primitives import CallId, CampaignId, CustomerId, TenantId
from .envelope import DomainEvent


class TenantProvisioned(DomainEvent):
    """Emitted when a new tenant is created and provisioned in the platform (V5 Ch2).

    Triggers isolation-profile setup (schema creation, Redis namespace,
    S3 bucket prefix, Kubernetes namespace labels). The ``tier`` field
    determines feature flags and rate limits applied at provisioning time.
    """

    event_type: Literal["saas.tenant.provisioned"] = "saas.tenant.provisioned"
    tenant_id: TenantId
    org_name: str
    """Legal organisation name for the tenant."""
    tier: str
    """Subscription tier: 'STARTER' | 'GROWTH' | 'ENTERPRISE' | 'ENTERPRISE_PLUS'."""
    provisioned_by: str
    """Admin user ID or API key that triggered provisioning."""
    isolation_profile: str
    """IsolationProfile value: 'SHARED' | 'DEDICATED_SCHEMA' | 'DEDICATED_CLUSTER'."""


class TenantSuspended(DomainEvent):
    """Emitted when a tenant account is suspended (V5 Ch2.8).

    Suspension halts active campaigns, blocks new calls, and pauses billing.
    The ``reason`` determines whether automatic reactivation is possible.
    """

    event_type: Literal["saas.tenant.suspended"] = "saas.tenant.suspended"
    tenant_id: TenantId
    reason: str
    """Suspension reason: 'PAYMENT_OVERDUE' | 'COMPLIANCE_VIOLATION' | 'ADMIN_ACTION'."""
    suspended_by: str
    """Admin user ID or automated system that triggered the suspension."""
    reactivation_date: datetime | None = None
    """Scheduled reactivation date for payment-overdue suspensions (None = manual)."""


class CustomerCreated(DomainEvent):
    """Emitted when a new customer record is created in the CRM (V5 Ch3).

    Customer records are the authoritative source for all customer data
    in the VoiceOS system (RI-5 Law of Authority). The CRM ID is the
    stable external identifier from the lending system.
    """

    event_type: Literal["saas.customer.created"] = "saas.customer.created"
    tenant_id: TenantId
    customer_id: CustomerId
    crm_id: str
    """External CRM or lending system identifier for deduplication."""
    name: str
    """Customer's full name (used for personalised greetings)."""
    preferred_language: str = "en"
    """BCP-47 language tag (e.g. 'hi', 'en', 'ta') for TTS voice selection."""


class LoanAccountUpdated(DomainEvent):
    """Emitted when a loan account record is updated in the platform (V5 Ch4).

    Carries the fields that changed (JSON-patch style ``changed_fields``).
    Consumers must re-fetch the full record from the CRM for authoritative
    values — this event is a cache invalidation signal, not a data carrier.

    Architecture: Invariant RI-5 (Law of Authority applies — do not use
    the delta fields as authoritative facts without CRM verification).
    """

    event_type: Literal["saas.loan_account.updated"] = "saas.loan_account.updated"
    tenant_id: TenantId
    customer_id: CustomerId
    loan_account_id: str
    """Internal loan account identifier."""
    changed_fields: tuple[str, ...] = Field(default=())
    """Names of fields updated in this change set (cache invalidation hint)."""
    updated_by: str
    """Actor that triggered the update: system user ID or 'sync_job'."""


class PTPCreated(DomainEvent):
    """Emitted when a Promise-To-Pay is recorded after a successful collection call.

    The PTP is the primary outcome metric for the collections workflow.
    Immediately triggers idempotency key registration (V3 Ch8) to prevent
    duplicate PTP recording from barge-in or network retry scenarios.

    Architecture: V5 Ch4.3 (PTP Workflow); Invariant RI-5.
    """

    event_type: Literal["saas.ptp.created"] = "saas.ptp.created"
    tenant_id: TenantId
    call_id: CallId
    customer_id: CustomerId
    loan_account_id: str
    promised_amount_minor: int = Field(ge=0)
    """Promised payment in minor currency units (paise/cents)."""
    currency: str
    """ISO 4217 currency code."""
    promise_date: datetime
    """Date by which the customer committed to pay."""


class PTPBroken(DomainEvent):
    """Emitted when a recorded Promise-To-Pay is marked BROKEN (V5 Ch4.3, Sprint-025).

    Net-new event -- ``PromiseToPayService.update_status()`` previously only
    incremented the ``ptp_broken`` metric on this transition (Sprint-022);
    Sprint-025's ``WebhookService`` is the first EventBus consumer, so a
    domain event is now published alongside the existing metric.
    """

    event_type: Literal["saas.ptp.broken"] = "saas.ptp.broken"
    tenant_id: TenantId
    ptp_id: str


class SettlementOffered(DomainEvent):
    """Emitted when a settlement offer is proposed to a customer (V5 Ch4.4).

    The offer is authoritative (RI-5) — the agent may present it verbatim
    but may not alter ``settlement_amount_minor``/``waiver_amount_minor``.
    """

    event_type: Literal["saas.settlement.offered"] = "saas.settlement.offered"
    tenant_id: TenantId
    customer_id: CustomerId
    loan_account_id: str
    settlement_id: str
    settlement_amount_minor: int = Field(ge=0)
    waiver_amount_minor: int = Field(ge=0)
    currency: str


class SettlementAccepted(DomainEvent):
    """Emitted when a customer accepts a settlement offer (V5 Ch4.4)."""

    event_type: Literal["saas.settlement.accepted"] = "saas.settlement.accepted"
    tenant_id: TenantId
    customer_id: CustomerId
    settlement_id: str
    accepted_at: datetime


class SettlementAuthorized(DomainEvent):
    """Emitted when a human approver authorizes an over-threshold settlement (V5 §5.13).

    Required before ``SettlementService.disburse()`` for settlements above
    the ``approval_threshold`` (50,000 minor units) — the authorization gate
    is metadata on the existing ``ACCEPTED`` record, not a new lifecycle
    status (Sprint-022 deviation; see CHANGELOG.md).
    """

    event_type: Literal["saas.settlement.authorized"] = "saas.settlement.authorized"
    tenant_id: TenantId
    settlement_id: str
    approved_by: str
    """Human approver's user ID (V4 Ch15 human-in-the-loop requirement)."""


class SettlementDisbursed(DomainEvent):
    """Emitted when a settlement payment is confirmed and the loan is closed out (V5 Ch4.4)."""

    event_type: Literal["saas.settlement.disbursed"] = "saas.settlement.disbursed"
    tenant_id: TenantId
    customer_id: CustomerId
    settlement_id: str
    disbursed_at: datetime


class CallbackScheduled(DomainEvent):
    """Emitted when a customer callback request is recorded (V5 Ch4.5)."""

    event_type: Literal["saas.callback.scheduled"] = "saas.callback.scheduled"
    tenant_id: TenantId
    call_id: CallId
    customer_id: CustomerId
    loan_account_id: str
    callback_id: str
    preferred_time: datetime


class EscalationTriggered(DomainEvent):
    """Emitted when a call is escalated to a human agent, supervisor, or legal (V5 Ch4.6).

    Triggered by ``RiskFlagRaised`` events (CRITICAL/HIGH) or explicit
    customer request. Full escalation routing/paging pipeline is Sprint-027
    scope — this event only records that an escalation occurred.
    """

    event_type: Literal["saas.escalation.triggered"] = "saas.escalation.triggered"
    tenant_id: TenantId
    call_id: CallId
    customer_id: CustomerId
    escalation_id: str
    reason: str
    escalated_to: str


class CampaignStarted(DomainEvent):
    """Emitted when a dialler campaign transitions from SCHEDULED to ACTIVE (V5 Ch6).

    Triggers audience list loading and the first batch of dialler slot
    allocations. The ``target_call_count`` is the audience size at launch;
    actual calls may differ due to DNC filtering.

    Architecture: V5 Ch6 (Campaign Engine).
    """

    event_type: Literal["saas.campaign.started"] = "saas.campaign.started"
    tenant_id: TenantId
    campaign_id: CampaignId
    campaign_name: str
    target_call_count: int = Field(ge=0)
    """Audience size before DNC / consent filtering."""
    started_by: str
    """Admin user ID that triggered the campaign launch."""


class CampaignCompleted(DomainEvent):
    """Emitted when a dialler campaign transitions to COMPLETED (V5 Ch6, Sprint-025).

    Net-new event -- ``CampaignService.complete()`` previously performed the
    ``ACTIVE -> COMPLETED`` lifecycle transition without publishing anything
    (Sprint-023); Sprint-025's ``WebhookService`` is the first EventBus
    consumer for campaign completion.
    """

    event_type: Literal["saas.campaign.completed"] = "saas.campaign.completed"
    tenant_id: TenantId
    campaign_id: CampaignId
    completed_call_count: int = Field(ge=0)


class CallTransferred(DomainEvent):
    """Emitted when a live call is transferred from AI to a human agent (V5 Ch7).

    Triggered by ``LiveTransferService.initiate_transfer()`` — the AI is
    muted and the agent takes over with a fully-assembled
    ``AgentScreenContext`` (transcript, decision lineage, customer/loan
    data). ``context_preserved`` mirrors V5 Ch7's ``TransferResult`` shape.
    """

    event_type: Literal["saas.call.transferred"] = "saas.call.transferred"
    tenant_id: TenantId
    call_id: CallId
    reason: str
    """Transfer trigger: 'ESCALATE_STRATEGY' | 'REQUIRE_HUMAN' | 'SUPERVISOR_OVERRIDE'."""
    routed_to_agent_id: str
    context_preserved: bool = True


class CallDispositioned(DomainEvent):
    """Emitted when a call disposition is recorded at the end of a call (V5 Ch4.5).

    Disposition codes drive CRM updates, campaign analytics, and retry
    scheduling. ``outcome_code`` is the stable disposition vocabulary.
    """

    event_type: Literal["saas.call.dispositioned"] = "saas.call.dispositioned"
    tenant_id: TenantId
    call_id: CallId
    customer_id: CustomerId
    loan_account_id: str
    outcome_code: str
    """Stable disposition code (e.g. 'PTP_MADE', 'PROMISE_BROKEN', 'NOT_REACHABLE',
    'DISPUTE_RAISED', 'CALLBACK_REQUESTED', 'SETTLEMENT_AGREED')."""
    duration_ms: int = Field(ge=0)
    """Total call duration in milliseconds."""
    dispositioned_at: datetime


class STTTranscribed(DomainEvent):
    """Emitted when the STT adapter completes transcription of an audio segment (V5 Ch10).

    Feeds ``UsageCollector`` (Sprint-024): ``token_count`` is metered as
    ``UsageType.STT_TOKEN``. Net-new event — the STT service previously
    published nothing to the EventBus (see CHANGELOG.md Sprint-024
    deviations).
    """

    event_type: Literal["saas.stt.transcribed"] = "saas.stt.transcribed"
    tenant_id: TenantId
    call_id: CallId
    token_count: int = Field(ge=0)
    """Approximate token count of the transcribed segment, for usage metering."""


class LLMGenerated(DomainEvent):
    """Emitted when the LLM adapter completes a generation turn (V5 Ch10).

    Feeds ``UsageCollector`` (Sprint-024): ``token_count`` is metered as
    ``UsageType.LLM_TOKEN``. Net-new event — the LLM runtime service
    previously published nothing to the EventBus (see CHANGELOG.md
    Sprint-024 deviations).
    """

    event_type: Literal["saas.llm.generated"] = "saas.llm.generated"
    tenant_id: TenantId
    call_id: CallId
    token_count: int = Field(ge=0)
    """Total prompt + completion tokens for this generation, for usage metering."""


class GPUAllocated(DomainEvent):
    """Emitted when the GPU Scheduler grants a GPU time-slice to a call (V5 Ch10).

    Feeds ``UsageCollector`` (Sprint-024): ``allocated_seconds`` is metered
    as ``UsageType.GPU_SECOND``. Net-new event — the GPU Scheduler
    previously published nothing to the EventBus (see CHANGELOG.md
    Sprint-024 deviations).
    """

    event_type: Literal["saas.gpu.allocated"] = "saas.gpu.allocated"
    tenant_id: TenantId
    call_id: CallId
    allocated_seconds: float = Field(ge=0)
    """Duration of the granted GPU allocation, in seconds, for usage metering."""


class UsageEventRecorded(DomainEvent):
    """Emitted for every billable unit consumed by a tenant (V5 Ch7).

    Usage events feed the metering pipeline. Downstream, the BillingEngine
    aggregates them into invoices. ``unit_type`` determines the pricing
    dimension (per-minute, per-sms, per-api-call, per-mb-storage).

    Architecture: V5 Ch7 (Usage Metering); V5 Ch8 (Billing).
    """

    event_type: Literal["saas.usage.event_recorded"] = "saas.usage.event_recorded"
    tenant_id: TenantId
    unit_type: str
    """Usage dimension: 'CALL_MINUTE' | 'SMS_MESSAGE' | 'API_CALL' |
    'STORAGE_MB' | 'AI_TOKEN'."""
    quantity: int = Field(ge=0)
    """Quantity of units consumed in this event."""
    resource_id: str = ""
    """Optional reference to the resource that generated the usage (e.g. call_id)."""
    occurred_at_bucket: str
    """ISO-8601 UTC hour bucket for aggregation (e.g. '2026-06-30T14:00:00Z')."""


class BillingInvoiceGenerated(DomainEvent):
    """Emitted when the BillingEngine generates a monthly invoice for a tenant (V5 Ch8).

    Triggers invoice delivery (email + portal) and payment collection.
    ``total_amount_minor`` is in the tenant's billing currency.
    """

    event_type: Literal["saas.billing.invoice_generated"] = "saas.billing.invoice_generated"
    tenant_id: TenantId
    invoice_id: str
    """Stable invoice identifier (unique per tenant per billing period)."""
    billing_period_start: datetime
    billing_period_end: datetime
    total_amount_minor: int = Field(ge=0)
    """Invoice total in minor currency units."""
    currency: str
    """ISO 4217 billing currency code."""
    line_item_count: int = Field(ge=0)
    """Number of aggregated usage line items in this invoice."""


__all__ = [
    "BillingInvoiceGenerated",
    "CallDispositioned",
    "CallTransferred",
    "CallbackScheduled",
    "CampaignCompleted",
    "CampaignStarted",
    "CustomerCreated",
    "EscalationTriggered",
    "GPUAllocated",
    "LLMGenerated",
    "LoanAccountUpdated",
    "PTPBroken",
    "PTPCreated",
    "STTTranscribed",
    "SettlementAccepted",
    "SettlementAuthorized",
    "SettlementDisbursed",
    "SettlementOffered",
    "TenantProvisioned",
    "TenantSuspended",
    "UsageEventRecorded",
]
