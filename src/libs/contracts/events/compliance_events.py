"""Domain events for compliance, policy, and privacy.

Covers consent lifecycle, policy decisions, audit emission, PII redaction,
and DPDP/GDPR data erasure requests. Every event here is persisted to the
immutable audit log (V4 Ch11) in addition to the primary event log.

Architecture: V4 Ch2 (DPDP Compliance), V4 Ch3 (AI Governance),
              V4 Ch5 (Privacy), V4 Ch11 (Audit Trail);
              V3 Ch3 (Event Bus); V6 Ch6 EV-1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..primitives import CallId, CustomerId
from .envelope import DomainEvent


class ConsentRecorded(DomainEvent):
    """Emitted when explicit consent is recorded or renewed for a customer.

    Consent must be recorded before a call may proceed past the greeting
    (V4 Ch2). The ``channel`` field identifies where consent was captured
    (IVR, SMS, WhatsApp, web portal).

    Architecture: V4 Ch2 (DPDP §7); V5 Ch4.
    """

    event_type: Literal["compliance.consent.recorded"] = "compliance.consent.recorded"
    customer_id: CustomerId
    consent_type: str
    """ConsentType value: 'CONTACT' | 'DATA_PROCESSING' | 'VOICE_RECORDING' | etc."""
    granted_at: datetime
    channel: str
    """Capture channel: 'ivr' | 'sms' | 'whatsapp' | 'web' | 'agent'."""
    granted_by_actor: str
    """The system or agent ID that recorded the consent."""


class ConsentRevoked(DomainEvent):
    """Emitted when a customer revokes consent (opt-out).

    All future calls for this customer must be blocked at the Policy Engine
    until consent is re-granted. Triggers an immediate do-not-call flag.

    Architecture: V4 Ch2 (DPDP §8).
    """

    event_type: Literal["compliance.consent.revoked"] = "compliance.consent.revoked"
    customer_id: CustomerId
    consent_type: str
    revoked_at: datetime
    reason: str = ""
    """Customer-provided reason, if captured."""
    revoked_by_actor: str
    """The system or customer portal session that processed the revocation."""


class PolicyDecisionMade(DomainEvent):
    """Emitted by the Policy Engine for every consequential policy decision (V4 Ch14).

    Provides the audit trail for regulator enquiries. The ``decision`` field
    uses stable codes; ``explanation`` is the human-readable rationale.
    """

    event_type: Literal["compliance.policy.decision_made"] = "compliance.policy.decision_made"
    call_id: CallId
    rule_id: str
    """Stable policy rule identifier (e.g. 'RBI-FC-001', 'DPDP-CONSENT-CHECK')."""
    decision: str
    """Policy outcome: 'ALLOW' | 'BLOCK' | 'REQUIRE_HUMAN' | 'WARN'."""
    explanation: str
    """Human-readable rationale for this decision (persisted for audit)."""


class AuditEventEmitted(DomainEvent):
    """Emitted when an auditable action occurs in the system (V4 Ch11).

    Every mutation to customer data, loan data, or consent records generates
    an AuditEvent. Consumed by the immutable audit-log writer.
    """

    event_type: Literal["compliance.audit.event_emitted"] = "compliance.audit.event_emitted"
    actor_id: str
    """The authenticated user or service account that performed the action."""
    action: str
    """Stable action code (e.g. 'customer.create', 'loan.update', 'ptp.record')."""
    resource_type: str
    """The type of resource acted upon (e.g. 'Customer', 'LoanAccount', 'PTP')."""
    resource_id: str
    """The ID of the resource acted upon."""
    outcome: str
    """Action outcome: 'SUCCESS' | 'FAILURE' | 'PARTIAL'."""
    ip_address: str = ""
    """Source IP address of the request, if available (privacy: not logged for calls)."""


class PIIRedacted(DomainEvent):
    """Emitted when PII is detected and redacted from a log entry or stored record.

    Architecture: V4 Ch5 (PII Detection & Redaction). Used for compliance
    reporting and to verify the PII pipeline is operating correctly.
    """

    event_type: Literal["compliance.pii.redacted"] = "compliance.pii.redacted"
    resource_type: str
    """Type of resource where PII was found (e.g. 'CallTranscript', 'AuditLog')."""
    resource_id: str
    """ID of the resource that was redacted."""
    fields_redacted: tuple[str, ...] = Field(default=())
    """Names of fields that were redacted (e.g. ('phone_number', 'aadhaar_number'))."""
    redaction_count: int = Field(ge=0)
    """Number of PII tokens redacted in this operation."""


class DataErasureRequested(DomainEvent):
    """Emitted when a data erasure request is received (DPDP §13 / GDPR Art.17).

    Triggers the DataErasureJob pipeline in Sprint-020. The request must be
    completed within the regulatory window (30 days for DPDP).

    Architecture: V4 Ch5.8 (Data Erasure); V4 Ch2.
    """

    event_type: Literal["compliance.data.erasure_requested"] = "compliance.data.erasure_requested"
    customer_id: CustomerId
    requested_by: str
    """The portal session or actor ID that submitted the erasure request."""
    requested_at: datetime
    regulation: str
    """Governing regulation: 'DPDP' | 'GDPR' | 'RBI' | 'INTERNAL'."""
    due_by: datetime
    """Deadline for completing the erasure (regulatory window)."""


class HITLItemEnqueued(DomainEvent):
    """Emitted when a REQUIRE_HUMAN verdict is durably enqueued (V4 Ch15).

    Successor to Sprint-020's ``human_oversight.review_required`` (still
    published independently by ``HumanOversightRouter``/``GovernanceLayer``)
    — this event marks the point of durable persistence into ``hitl_queue``.
    """

    event_type: Literal["compliance.hitl.item_enqueued"] = "compliance.hitl.item_enqueued"
    call_id: CallId
    hitl_item_id: str
    priority: str
    """HITLPriority value: 'CRITICAL' | 'HIGH' | 'MEDIUM'."""
    reason: str


class HITLSLABreached(DomainEvent):
    """Emitted when a HITL queue item exceeds its priority's SLA deadline (V4 Ch15).

    Consumed by compliance monitoring (Sprint-020's ``ComplianceMonitoring``)
    and the on-call escalation path.
    """

    event_type: Literal["compliance.hitl.sla_breached"] = "compliance.hitl.sla_breached"
    call_id: CallId
    hitl_item_id: str
    priority: str
    age_seconds: int = Field(ge=0)


class HITLDecisionRecorded(DomainEvent):
    """Emitted when a supervisor records a decision on a HITL item (V4 Ch15 §15.12).

    Every human override is audited: who reviewed, what the decision was,
    and the rationale — this event is the audit-trail source for
    ``OverrideLogger``.
    """

    event_type: Literal["compliance.hitl.decision_recorded"] = "compliance.hitl.decision_recorded"
    call_id: CallId
    hitl_item_id: str
    supervisor_id: str
    decision: str
    rationale: str


__all__ = [
    "AuditEventEmitted",
    "ConsentRecorded",
    "ConsentRevoked",
    "DataErasureRequested",
    "HITLDecisionRecorded",
    "HITLItemEnqueued",
    "HITLSLABreached",
    "PIIRedacted",
    "PolicyDecisionMade",
]
