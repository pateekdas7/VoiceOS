"""Persistent data models for tenant, organisation, and business unit hierarchy.

Tenants are the top-level isolation boundary in VoiceOS (V5 Ch2).
Each tenant has exactly one Organisation, which may contain multiple
BusinessUnits and Branches.

Architecture: V5 Ch2 (Tenant Management); V4 Ch6 (Multi-tenancy);
              V7 Ch4 (Kubernetes Isolation).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId


class TenantStatus(StrEnum):
    """Lifecycle status of a tenant account (V5 Ch3 Tenant Lifecycle, Sprint-021).

    Valid transition graph::

        TRIAL -> SANDBOX -> PRODUCTION -> SUSPENDED -> CANCELLED -> DELETING -> DELETED
                    ^_________|                            |
                    (reactivate)                       (suspend)
    """

    TRIAL = "TRIAL"
    """Newly created tenant — a lightweight DB record, not yet provisioned."""
    SANDBOX = "SANDBOX"
    """Tenant is testing/configuring before going live."""
    PRODUCTION = "PRODUCTION"
    """Fully provisioned and active — calls may be admitted."""
    SUSPENDED = "SUSPENDED"
    """Billing overdue or compliance violation — calls blocked."""
    CANCELLED = "CANCELLED"
    """Customer-terminated subscription — awaiting deletion."""
    DELETING = "DELETING"
    """Async deletion in progress (crypto-shred + tombstone)."""
    DELETED = "DELETED"


class IsolationProfile(StrEnum):
    """Data and compute isolation tier for the tenant (V5 Ch2.3; V7 Ch4).

    Determines schema isolation, Redis namespace strategy, and whether
    the tenant gets a dedicated Kubernetes namespace.
    """

    SHARED = "SHARED"
    """Shared schema with row-level security (default for Starter tier)."""
    DEDICATED_SCHEMA = "DEDICATED_SCHEMA"
    """Dedicated PostgreSQL schema; shared cluster (Growth tier)."""
    DEDICATED_CLUSTER = "DEDICATED_CLUSTER"
    """Dedicated Kubernetes namespace and database cluster (Enterprise tier)."""


class Branch(BaseModel):
    """A geographic or functional branch within a BusinessUnit (V5 Ch2.5)."""

    model_config = ConfigDict(frozen=True)

    branch_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    city: str = ""
    state: str = ""
    is_active: bool = True


class BusinessUnit(BaseModel):
    """A business unit or division within the Organisation (V5 Ch2.4)."""

    model_config = ConfigDict(frozen=True)

    bu_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    """Business unit name (e.g. 'Retail Collections', 'SME Loans')."""
    branches: tuple[Branch, ...] = Field(default=())
    is_active: bool = True


class Organization(BaseModel):
    """The legal entity associated with a tenant (V5 Ch2.2)."""

    model_config = ConfigDict(frozen=True)

    org_id: str = Field(min_length=1)
    tenant_id: TenantId
    legal_name: str = Field(min_length=1, max_length=300)
    """Registered legal name of the organisation."""
    country: str = Field(default="IN", min_length=2, max_length=2)
    """ISO 3166-1 alpha-2 country code."""
    registration_number: str = ""
    """Company/CIN registration number."""
    business_units: tuple[BusinessUnit, ...] = Field(default=())
    created_at: datetime
    updated_at: datetime


class Tenant(BaseModel):
    """Top-level tenant record in VoiceOS (V5 Ch2).

    Every resource in the system is scoped to a tenant via ``tenant_id``.
    The ``isolation_profile`` determines the data and compute boundaries
    enforced at the infrastructure layer (V7 Ch4).
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: TenantId
    slug: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9-]+$")
    """URL-safe tenant slug (lowercase alphanumeric + hyphens, max 63 chars)."""
    display_name: str = Field(min_length=1, max_length=200)
    subscription_tier: str
    """Billing tier: 'STARTER' | 'GROWTH' | 'ENTERPRISE' | 'ENTERPRISE_PLUS'."""
    isolation_profile: IsolationProfile = IsolationProfile.SHARED
    status: TenantStatus = TenantStatus.TRIAL
    timezone: str = Field(default="Asia/Kolkata")
    """Default IANA timezone for campaign scheduling and reporting."""
    currency: str = Field(default="INR", min_length=3, max_length=3)
    """ISO 4217 default billing currency."""
    max_concurrent_calls: int = Field(default=10, ge=1)
    """Licence limit on concurrent active calls."""
    feature_flags: tuple[str, ...] = Field(default=())
    """Enabled feature flag names for this tenant."""
    created_at: datetime
    updated_at: datetime
    organization: Organization | None = None


__all__ = [
    "Branch",
    "BusinessUnit",
    "IsolationProfile",
    "Organization",
    "Tenant",
    "TenantStatus",
]
