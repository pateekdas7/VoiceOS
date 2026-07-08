"""Persistent data models for customer and party records.

These are the authoritative CRM models stored in PostgreSQL (V5 Ch3).
They are immutable Pydantic models used for reading from and writing to
the persistence layer via the repository pattern.

Architecture: V5 Ch3 (Customer CRM); V4 Ch5 (Privacy); V6 Ch3.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import CustomerId, TenantId


class PartyRole(StrEnum):
    """Role of a party relative to a loan account."""

    PRIMARY_BORROWER = "PRIMARY_BORROWER"
    CO_BORROWER = "CO_BORROWER"
    GUARANTOR = "GUARANTOR"
    NOMINEE = "NOMINEE"


class Address(BaseModel):
    """Physical or mailing address for a customer (V5 Ch3.2)."""

    model_config = ConfigDict(frozen=True)

    line1: str = Field(min_length=1, max_length=200)
    line2: str = ""
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    pincode: str = Field(min_length=1, max_length=20)
    country: str = Field(default="IN", min_length=2, max_length=2)
    """ISO 3166-1 alpha-2 country code."""


class CustomerContact(BaseModel):
    """A single contact entry for a customer (phone or email).

    Named CustomerContact (not ContactInfo) to avoid collision with the
    runtime ContactInfo type in context.py.
    """

    model_config = ConfigDict(frozen=True)

    contact_type: str
    """Contact type: 'MOBILE' | 'HOME' | 'OFFICE' | 'EMAIL' | 'WHATSAPP'."""
    value: str = Field(min_length=1, max_length=200)
    """Phone number (E.164) or email address."""
    is_primary: bool = False
    is_dnc: bool = False
    """Do-not-contact flag (DNC list or customer opt-out)."""
    consent_captured: bool = False
    """Whether DPDP contact consent has been captured for this contact."""


class Party(BaseModel):
    """A person associated with a loan account in a specific role (V5 Ch3.3)."""

    model_config = ConfigDict(frozen=True)

    party_id: str = Field(min_length=1)
    """Internal party identifier."""
    customer_id: CustomerId
    role: PartyRole
    name: str = Field(min_length=1, max_length=200)
    contacts: tuple[CustomerContact, ...] = Field(default=())
    address: Address | None = None


class Customer(BaseModel):
    """Authoritative customer record (V5 Ch3).

    This is the primary entity that all collection calls are placed against.
    The ``crm_id`` is the stable identifier from the lending/origination system
    and must be treated as the Law of Authority source for customer identity
    (Invariant RI-5).

    All PII fields (name, contacts, address) are subject to data erasure
    under DPDP §13 / GDPR Art.17 (V4 Ch5.8).
    """

    model_config = ConfigDict(frozen=True)

    customer_id: CustomerId
    tenant_id: TenantId
    crm_id: str = Field(min_length=1, max_length=100)
    """External CRM identifier — authoritative source of truth (RI-5)."""
    name: str = Field(min_length=1, max_length=200)
    preferred_language: str = Field(default="en", min_length=2, max_length=10)
    """BCP-47 language tag for TTS voice selection."""
    contacts: tuple[CustomerContact, ...] = Field(default=())
    address: Address | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
    data_erasure_requested: bool = False
    """Set when a DataErasureRequested event has been processed for this customer."""


__all__ = [
    "Address",
    "Customer",
    "CustomerContact",
    "Party",
    "PartyRole",
]
