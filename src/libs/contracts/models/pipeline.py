"""Persistent data models for pipeline and campaign lead management.

Pipelines are work queues scoped to one campaign. Campaign leads are contacts
imported via CSV upload and distributed across pipelines by the Lead
Distribution Engine.

Architecture: V5 Ch6 (Campaign Engine — Lead Distribution, ADR-005 §4.3/§14).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import CampaignId, LeadId, PipelineId, TenantId


class PipelineStatus(StrEnum):
    """Lifecycle status of a pipeline."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class Pipeline(BaseModel):
    """A work queue (pipeline) scoped to one campaign.

    Leads are distributed across a campaign's pipelines by the Lead
    Distribution Engine after import. Each pipeline operates independently
    — pausing one pipeline does not affect others in the same campaign.
    """

    model_config = ConfigDict(frozen=True)

    pipeline_id: PipelineId
    tenant_id: TenantId
    campaign_id: CampaignId
    name: str = Field(min_length=1, max_length=200)
    status: PipelineStatus = PipelineStatus.DRAFT
    created_at: datetime
    updated_at: datetime
    created_by: str = Field(min_length=1)


class LeadStatus(StrEnum):
    """Processing status of a campaign lead."""

    NEW = "NEW"
    VALIDATED = "VALIDATED"
    QUALIFIED = "QUALIFIED"
    ASSIGNED = "ASSIGNED"
    CALLED = "CALLED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class LeadQueueStatus(StrEnum):
    """Dialler queue position of a campaign lead."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    IN_CALL = "IN_CALL"
    DONE = "DONE"


class LeadLanguage(StrEnum):
    """Detected/declared language for the lead contact."""

    HINDI = "HINDI"
    TAMIL = "TAMIL"
    TELUGU = "TELUGU"
    MARATHI = "MARATHI"
    GUJARATI = "GUJARATI"
    KANNADA = "KANNADA"
    MALAYALAM = "MALAYALAM"
    BENGALI = "BENGALI"
    PUNJABI = "PUNJABI"
    ODIA = "ODIA"
    ENGLISH = "ENGLISH"


class CampaignLead(BaseModel):
    """One contact imported into a campaign via CSV upload.

    Distinct from ``CampaignAudienceMember``, which is built by AudienceSelector
    from CRM/loan-book criteria. CampaignLeads are client-supplied contacts
    that may or may not exist in the CRM yet.
    """

    model_config = ConfigDict(frozen=True)

    lead_id: LeadId
    tenant_id: TenantId
    campaign_id: CampaignId
    pipeline_id: PipelineId | None = None
    """Assigned pipeline; None until the Lead Distribution Engine processes the import."""
    import_id: str | None = None
    phone: str
    """Normalized E.164 phone number (e.g. '+919876543210')."""
    phone_raw: str
    """Original phone string as uploaded by the client."""
    name: str = ""
    email: str | None = None
    language: LeadLanguage = LeadLanguage.HINDI
    score: int = Field(default=0, ge=0, le=100)
    """Lead priority score 0–100; higher = more urgent. Drives pipeline distribution order."""
    status: LeadStatus = LeadStatus.NEW
    queue_status: LeadQueueStatus = LeadQueueStatus.PENDING
    is_duplicate: bool = False
    """True when another non-duplicate lead for the same phone exists in this campaign."""
    is_blacklisted: bool = False
    rejection_reason: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    """Free-form attributes from the CSV: dpd, outstanding, product_type, city, state, etc."""
    created_at: datetime


class LeadImportStatus(StrEnum):
    """Processing status of a bulk lead import operation."""

    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class LeadImport(BaseModel):
    """Audit record for one CSV bulk-upload operation."""

    model_config = ConfigDict(frozen=True)

    import_id: str
    tenant_id: TenantId
    campaign_id: CampaignId
    filename: str
    status: LeadImportStatus = LeadImportStatus.PROCESSING
    total_rows: int = Field(default=0, ge=0)
    valid_rows: int = Field(default=0, ge=0)
    invalid_rows: int = Field(default=0, ge=0)
    duplicate_rows: int = Field(default=0, ge=0)
    created_at: datetime
    completed_at: datetime | None = None


__all__ = [
    "CampaignLead",
    "LeadImport",
    "LeadImportStatus",
    "LeadLanguage",
    "LeadQueueStatus",
    "LeadStatus",
    "Pipeline",
    "PipelineStatus",
]
