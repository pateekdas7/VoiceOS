"""Persistent data models for the Integration Platform (webhooks) and API Platform (API keys).

Architecture: V5 Ch15 (Integration Platform -- webhooks); V5 Ch16 (API
Platform); V4 Ch12 (API Security -- signed webhooks, API key auth).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import TenantId

WEBHOOK_EVENT_TYPES: tuple[str, ...] = (
    "call.completed",
    "ptp.created",
    "ptp.broken",
    "campaign.completed",
    "transfer.initiated",
)
"""The fixed webhook event vocabulary a tenant may subscribe to (Sprint-025.md)."""


class WebhookRegistration(BaseModel):
    """A tenant-registered webhook endpoint (V5 Ch15)."""

    model_config = ConfigDict(frozen=True)

    webhook_id: str
    tenant_id: TenantId
    url: str
    secret: str
    """Shared secret used to HMAC-SHA256-sign every delivery (never returned by list endpoints)."""
    event_types: tuple[str, ...]
    is_active: bool = True
    created_at: datetime


class WebhookDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    RETRYING = "RETRYING"
    DLQ = "DLQ"


class WebhookDelivery(BaseModel):
    """One delivery attempt record for a webhook event (V5 Ch15 -- retry/DLQ tracking)."""

    model_config = ConfigDict(frozen=True)

    delivery_id: str
    webhook_id: str
    tenant_id: TenantId
    event_type: str
    payload: dict[str, object]
    attempt: int = Field(ge=0)
    status: WebhookDeliveryStatus
    last_error: str = ""
    created_at: datetime
    delivered_at: datetime | None = None


class APIKeyRecord(BaseModel):
    """A persisted, hashed public-API key (V5 Ch16, V4 Ch12).

    Only ``key_hash`` (SHA-256 hex of the raw key) is ever stored -- the raw
    key is shown to the caller exactly once, at issuance. ``expires_at``/
    ``plan_tier`` are additive lifecycle fields (Sprint-025 Part-3, migration
    0025): ``expires_at=None`` means the key never expires; ``plan_tier=""``
    means no plan association was set at issuance time.
    """

    model_config = ConfigDict(frozen=True)

    api_key_id: str
    tenant_id: TenantId
    key_hash: str
    role: str = ""
    scopes: tuple[str, ...] = Field(default=())
    is_revoked: bool = False
    created_at: datetime
    revoked_at: datetime | None = None
    expires_at: datetime | None = None
    plan_tier: str = ""


class WebhookDeliveryAttempt(BaseModel):
    """One individual HTTP delivery attempt (Sprint-025 Part-3, migration 0025).

    Finer-grained than :class:`WebhookDelivery` (which stores one row
    summarizing the final outcome of a whole retry sequence) -- this is an
    append-only log of every individual POST attempt, enforced append-only
    by a DB trigger (``webhook_delivery_attempts_immutable``).
    """

    model_config = ConfigDict(frozen=True)

    attempt_id: str
    webhook_id: str
    tenant_id: TenantId
    event_type: str
    attempt_number: int = Field(ge=1)
    http_status: int | None = None
    succeeded: bool
    error: str = ""
    attempted_at: datetime


class WebhookDLQEntry(BaseModel):
    """One dead-lettered webhook delivery (Sprint-025 Part-3, migration 0025).

    A dedicated, queryable DLQ store separate from ``WebhookDelivery.status
    == DLQ`` -- supports future replay via ``replayed_at``.
    """

    model_config = ConfigDict(frozen=True)

    dlq_id: str
    webhook_id: str
    tenant_id: TenantId
    event_type: str
    payload: dict[str, object]
    attempts: int = Field(ge=1)
    last_error: str = ""
    created_at: datetime
    replayed_at: datetime | None = None


class APIKeyUsageRecord(BaseModel):
    """One Public API request attributed to an API key (Sprint-025 Part-3, migration 0025)."""

    model_config = ConfigDict(frozen=True)

    usage_id: str
    api_key_id: str
    tenant_id: TenantId
    route: str
    status_code: int
    occurred_at: datetime


class APIRateLimitConfig(BaseModel):
    """Persisted per-tier rate-limit configuration (Sprint-025 Part-3, migration 0025).

    Replaces the hardcoded ``TIER_RPS_LIMITS`` dict as the source of truth
    when a Postgres-backed repository is wired -- "billing plans determine
    API capabilities" backed by real, tenant-editable rows.
    """

    model_config = ConfigDict(frozen=True)

    tier: str
    requests_per_second: int = Field(gt=0)
    burst_capacity: int = Field(gt=0)
    updated_at: datetime


__all__ = [
    "WEBHOOK_EVENT_TYPES",
    "APIKeyRecord",
    "APIKeyUsageRecord",
    "APIRateLimitConfig",
    "WebhookDLQEntry",
    "WebhookDelivery",
    "WebhookDeliveryAttempt",
    "WebhookDeliveryStatus",
    "WebhookRegistration",
]
