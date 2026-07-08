"""Persistent data models for DPDP/GDPR consent management.

ConsentStatus is re-exported from context.py (the single authoritative
definition) to avoid duplication. These models represent the persistent
consent records stored in PostgreSQL.

Architecture: V4 Ch2 (DPDP Compliance); V4 Ch5 (Privacy);
              V5 Ch4 (Customer CRM).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..context import ConsentStatus
from ..primitives import CustomerId, TenantId


class ConsentType(StrEnum):
    """Type of consent governed by DPDP / RBI Fair Practice Code."""

    CONTACT = "CONTACT"
    """Permission to contact the customer via phone or SMS."""
    DATA_PROCESSING = "DATA_PROCESSING"
    """Permission to process personal data for collections purposes."""
    VOICE_RECORDING = "VOICE_RECORDING"
    """Permission to record the call for quality and compliance."""
    WHATSAPP = "WHATSAPP"
    """Permission to contact via WhatsApp."""
    EMAIL = "EMAIL"
    """Permission to contact via email."""
    DATA_SHARING = "DATA_SHARING"
    """Permission to share data with third-party agencies."""


class ConsentRecord(BaseModel):
    """An audit trail entry for a single consent grant or revocation (V4 Ch2).

    Every consent change (grant or revoke) creates a new ConsentRecord.
    The Consent model aggregates the current active state; ConsentRecord
    provides the full immutable history for regulators.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(min_length=1)
    consent_id: str = Field(min_length=1)
    """Foreign key to Consent.consent_id."""
    action: str
    """Consent action: 'GRANTED' | 'REVOKED' | 'RENEWED' | 'EXPIRED'."""
    actor_id: str = Field(min_length=1)
    """System, agent, or portal session that recorded this change."""
    channel: str
    """Capture channel: 'ivr' | 'sms' | 'whatsapp' | 'web' | 'agent'."""
    recorded_at: datetime
    ip_address: str = ""
    """Source IP (omitted for IVR/call channels for privacy reasons)."""
    notes: str = ""


class Consent(BaseModel):
    """Current active consent state for a customer-consent-type pair (V4 Ch2).

    The ``status`` field reflects the most recent grant/revoke action.
    Full history is in ConsentRecord. The Policy Engine queries this model
    before permitting any call to proceed.

    Architecture: V4 Ch2; Invariant RI-5 (consent state is authoritative
    — the LLM must never infer or override consent status).
    """

    model_config = ConfigDict(frozen=True)

    consent_id: str = Field(min_length=1)
    tenant_id: TenantId
    customer_id: CustomerId
    consent_type: ConsentType
    status: ConsentStatus
    granted_at: datetime | None = None
    """Timestamp of the most recent grant. None if never granted."""
    revoked_at: datetime | None = None
    """Timestamp of the most recent revoke. None if never revoked."""
    expires_at: datetime | None = None
    """Optional expiry — after this timestamp, status becomes REVOKED."""
    created_at: datetime
    updated_at: datetime


__all__ = [
    "Consent",
    "ConsentRecord",
    "ConsentStatus",
    "ConsentType",
]
