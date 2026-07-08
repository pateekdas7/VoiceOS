"""WebhookRegistrationRepository, WebhookDeliveryRepository, APIKeyRepository --
Integration Platform + API Platform (V5 Ch15, Ch16; V4 Ch12).

Architecture: V5 Ch15 (Integration Platform); V5 Ch16 (API Platform);
V4 Ch12 (API Security).
"""

from __future__ import annotations

import json
from typing import Any

from ..contracts.models.integration import (
    APIKeyRecord,
    APIKeyUsageRecord,
    APIRateLimitConfig,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookDeliveryStatus,
    WebhookDLQEntry,
    WebhookRegistration,
)
from ..contracts.primitives import TenantId
from .base import BaseRepository

_WEBHOOK_REGISTRATIONS_TABLE = "webhook_registrations"
_WEBHOOK_DELIVERIES_TABLE = "webhook_deliveries"
_WEBHOOK_DELIVERY_ATTEMPTS_TABLE = "webhook_delivery_attempts"
_WEBHOOK_DLQ_TABLE = "webhook_dead_letter_queue"
_API_KEYS_TABLE = "api_keys"
_API_KEY_USAGE_TABLE = "api_key_usage"
_API_RATE_LIMITS_TABLE = "api_rate_limits"

_WEBHOOK_REGISTRATION_COLUMNS = ("webhook_id", "tenant_id", "url", "secret", "event_types", "is_active", "created_at")

_WEBHOOK_DELIVERY_COLUMNS = (
    "delivery_id",
    "webhook_id",
    "tenant_id",
    "event_type",
    "payload",
    "attempt",
    "status",
    "last_error",
    "created_at",
    "delivered_at",
)

_WEBHOOK_DELIVERY_ATTEMPT_COLUMNS = (
    "attempt_id",
    "webhook_id",
    "tenant_id",
    "event_type",
    "attempt_number",
    "http_status",
    "succeeded",
    "error",
    "attempted_at",
)

_WEBHOOK_DLQ_COLUMNS = (
    "dlq_id",
    "webhook_id",
    "tenant_id",
    "event_type",
    "payload",
    "attempts",
    "last_error",
    "created_at",
    "replayed_at",
)

_API_KEY_COLUMNS = (
    "api_key_id",
    "tenant_id",
    "key_hash",
    "role",
    "scopes",
    "is_revoked",
    "created_at",
    "revoked_at",
    "expires_at",
    "plan_tier",
)

_API_KEY_USAGE_COLUMNS = ("usage_id", "api_key_id", "tenant_id", "route", "status_code", "occurred_at")


class WebhookRegistrationRepository(BaseRepository):
    """Tenant-scoped queries for the ``webhook_registrations`` domain."""

    def create(self, registration: WebhookRegistration) -> WebhookRegistration:
        self._execute(
            f"""
            INSERT INTO {_WEBHOOK_REGISTRATIONS_TABLE} (
                webhook_id, tenant_id, url, secret, event_types, is_active, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                registration.webhook_id,
                registration.tenant_id,
                registration.url,
                registration.secret,
                list(registration.event_types),
                registration.is_active,
                registration.created_at,
            ),
        )
        self._commit()
        return registration

    def get(self, tenant_id: TenantId, webhook_id: str) -> WebhookRegistration | None:
        row = self._tenant_select_one(
            _WEBHOOK_REGISTRATIONS_TABLE,
            _WEBHOOK_REGISTRATION_COLUMNS,
            tenant_id,
            extra_where="webhook_id = %s",
            extra_params=(webhook_id,),
        )
        return self._hydrate_registration(row) if row is not None else None

    def find_active_for_event(self, tenant_id: TenantId, event_type: str) -> tuple[WebhookRegistration, ...]:
        rows = self._tenant_select(
            _WEBHOOK_REGISTRATIONS_TABLE,
            _WEBHOOK_REGISTRATION_COLUMNS,
            tenant_id,
            extra_where="is_active = TRUE AND %s = ANY(event_types)",
            extra_params=(event_type,),
        )
        return tuple(self._hydrate_registration(row) for row in rows)

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[WebhookRegistration, ...]:
        rows = self._tenant_select(_WEBHOOK_REGISTRATIONS_TABLE, _WEBHOOK_REGISTRATION_COLUMNS, tenant_id)
        return tuple(self._hydrate_registration(row) for row in rows)

    def update(self, tenant_id: TenantId, webhook_id: str, *, url: str, event_types: tuple[str, ...]) -> int:
        return self._tenant_update(
            _WEBHOOK_REGISTRATIONS_TABLE,
            ("url", "event_types"),
            (url, list(event_types)),
            tenant_id,
            extra_where="webhook_id = %s",
            extra_params=(webhook_id,),
        )

    def deactivate(self, tenant_id: TenantId, webhook_id: str) -> int:
        return self._tenant_update(
            _WEBHOOK_REGISTRATIONS_TABLE,
            ("is_active",),
            (False,),
            tenant_id,
            extra_where="webhook_id = %s",
            extra_params=(webhook_id,),
        )

    def rotate_secret(self, tenant_id: TenantId, webhook_id: str, new_secret: str) -> int:
        return self._tenant_update(
            _WEBHOOK_REGISTRATIONS_TABLE,
            ("secret",),
            (new_secret,),
            tenant_id,
            extra_where="webhook_id = %s",
            extra_params=(webhook_id,),
        )

    def _hydrate_registration(self, row: tuple[Any, ...]) -> WebhookRegistration:
        webhook_id, tenant_id, url, secret, event_types, is_active, created_at = row
        return WebhookRegistration(
            webhook_id=str(webhook_id),
            tenant_id=TenantId(tenant_id),
            url=url,
            secret=secret,
            event_types=tuple(event_types or ()),
            is_active=is_active,
            created_at=created_at,
        )


class WebhookDeliveryRepository(BaseRepository):
    """Tenant-scoped queries for the ``webhook_deliveries`` domain (retry/DLQ tracking)."""

    def create(self, delivery: WebhookDelivery) -> WebhookDelivery:
        self._execute(
            f"""
            INSERT INTO {_WEBHOOK_DELIVERIES_TABLE} (
                delivery_id, webhook_id, tenant_id, event_type, payload, attempt,
                status, last_error, created_at, delivered_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                delivery.delivery_id,
                delivery.webhook_id,
                delivery.tenant_id,
                delivery.event_type,
                json.dumps(delivery.payload),
                delivery.attempt,
                delivery.status.value,
                delivery.last_error,
                delivery.created_at,
                delivery.delivered_at,
            ),
        )
        self._commit()
        return delivery

    def update_status(
        self, tenant_id: TenantId, delivery_id: str, status: WebhookDeliveryStatus, *, attempt: int, last_error: str
    ) -> int:
        return self._tenant_update(
            _WEBHOOK_DELIVERIES_TABLE,
            ("status", "attempt", "last_error"),
            (status.value, attempt, last_error),
            tenant_id,
            extra_where="delivery_id = %s",
            extra_params=(delivery_id,),
        )

    def find_dlq(self, tenant_id: TenantId) -> tuple[WebhookDelivery, ...]:
        rows = self._tenant_select(
            _WEBHOOK_DELIVERIES_TABLE,
            _WEBHOOK_DELIVERY_COLUMNS,
            tenant_id,
            extra_where="status = %s",
            extra_params=(WebhookDeliveryStatus.DLQ.value,),
        )
        return tuple(self._hydrate_delivery(row) for row in rows)

    def list_for_webhook(self, tenant_id: TenantId, webhook_id: str) -> tuple[WebhookDelivery, ...]:
        """Delivery history for one webhook registration, newest first."""
        rows = self._tenant_select(
            _WEBHOOK_DELIVERIES_TABLE,
            _WEBHOOK_DELIVERY_COLUMNS,
            tenant_id,
            extra_where="webhook_id = %s",
            extra_params=(webhook_id,),
            order_by="created_at DESC",
        )
        return tuple(self._hydrate_delivery(row) for row in rows)

    def _hydrate_delivery(self, row: tuple[Any, ...]) -> WebhookDelivery:
        (
            delivery_id,
            webhook_id,
            tenant_id,
            event_type,
            payload_json,
            attempt,
            status,
            last_error,
            created_at,
            delivered_at,
        ) = row
        payload = json.loads(payload_json) if isinstance(payload_json, str) else (payload_json or {})
        return WebhookDelivery(
            delivery_id=str(delivery_id),
            webhook_id=str(webhook_id),
            tenant_id=TenantId(tenant_id),
            event_type=event_type,
            payload=payload,
            attempt=attempt,
            status=WebhookDeliveryStatus(status),
            last_error=last_error or "",
            created_at=created_at,
            delivered_at=delivered_at,
        )


class APIKeyRepository(BaseRepository):
    """Tenant-scoped queries for the ``api_keys`` domain (V5 Ch16, V4 Ch12).

    Keyed for lookup by ``key_hash`` (SHA-256 of the raw key) across all
    tenants -- a public API request arrives with only the raw key, not a
    tenant_id, so :meth:`find_by_hash` is deliberately not tenant-scoped
    (it *establishes* the tenant scope for everything downstream).
    """

    def create(self, record: APIKeyRecord) -> APIKeyRecord:
        self._execute(
            f"""
            INSERT INTO {_API_KEYS_TABLE} (
                api_key_id, tenant_id, key_hash, role, scopes, is_revoked, created_at, revoked_at,
                expires_at, plan_tier
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.api_key_id,
                record.tenant_id,
                record.key_hash,
                record.role,
                list(record.scopes),
                record.is_revoked,
                record.created_at,
                record.revoked_at,
                record.expires_at,
                record.plan_tier,
            ),
        )
        self._commit()
        return record

    def find_by_hash(self, key_hash: str) -> APIKeyRecord | None:
        cur = self._execute(
            f"SELECT {', '.join(_API_KEY_COLUMNS)} FROM {_API_KEYS_TABLE} WHERE key_hash = %s", (key_hash,)
        )
        row = cur.fetchone()
        return self._hydrate(row) if row is not None else None

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[APIKeyRecord, ...]:
        rows = self._tenant_select(_API_KEYS_TABLE, _API_KEY_COLUMNS, tenant_id)
        return tuple(self._hydrate(row) for row in rows)

    def revoke(self, tenant_id: TenantId, api_key_id: str, revoked_at: Any) -> int:
        return self._tenant_update(
            _API_KEYS_TABLE,
            ("is_revoked", "revoked_at"),
            (True, revoked_at),
            tenant_id,
            extra_where="api_key_id = %s",
            extra_params=(api_key_id,),
        )

    def rotate(self, tenant_id: TenantId, api_key_id: str, new_key_hash: str) -> int:
        """Replace the hash backing an existing key (API key lifecycle: rotation).

        Preserves ``api_key_id``/tenant ownership/scopes/plan association --
        only the credential material changes, so existing entitlement/usage
        history stays attributed to the same key.
        """
        return self._tenant_update(
            _API_KEYS_TABLE,
            ("key_hash",),
            (new_key_hash,),
            tenant_id,
            extra_where="api_key_id = %s",
            extra_params=(api_key_id,),
        )

    def _hydrate(self, row: tuple[Any, ...]) -> APIKeyRecord:
        (
            api_key_id,
            tenant_id,
            key_hash,
            role,
            scopes,
            is_revoked,
            created_at,
            revoked_at,
            expires_at,
            plan_tier,
        ) = row
        return APIKeyRecord(
            api_key_id=str(api_key_id),
            tenant_id=TenantId(tenant_id),
            key_hash=key_hash,
            role=role or "",
            scopes=tuple(scopes or ()),
            is_revoked=is_revoked,
            created_at=created_at,
            revoked_at=revoked_at,
            expires_at=expires_at,
            plan_tier=plan_tier or "",
        )


class WebhookDeliveryAttemptRepository(BaseRepository):
    """Append-only per-attempt log for webhook deliveries (Sprint-025 Part-3, migration 0025).

    Enforced append-only by the ``webhook_delivery_attempts_immutable`` DB
    trigger -- this repository never exposes an update/delete method,
    matching the ``AuditRepository`` precedent (Python-layer + DB-layer
    defense in depth).
    """

    def record(
        self,
        tenant_id: TenantId,
        webhook_id: str,
        event_type: str,
        attempt_number: int,
        http_status: int | None,
        succeeded: bool,
        error: str,
    ) -> None:
        self._execute(
            f"""
            INSERT INTO {_WEBHOOK_DELIVERY_ATTEMPTS_TABLE} (
                webhook_id, tenant_id, event_type, attempt_number, http_status, succeeded, error
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (webhook_id, tenant_id, event_type, attempt_number, http_status, succeeded, error),
        )
        self._commit()

    def list_for_webhook(self, tenant_id: TenantId, webhook_id: str) -> tuple[WebhookDeliveryAttempt, ...]:
        rows = self._tenant_select(
            _WEBHOOK_DELIVERY_ATTEMPTS_TABLE,
            _WEBHOOK_DELIVERY_ATTEMPT_COLUMNS,
            tenant_id,
            extra_where="webhook_id = %s",
            extra_params=(webhook_id,),
            order_by="attempted_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> WebhookDeliveryAttempt:
        attempt_id, webhook_id, tenant_id, event_type, attempt_number, http_status, succeeded, error, attempted_at = row
        return WebhookDeliveryAttempt(
            attempt_id=str(attempt_id),
            webhook_id=str(webhook_id),
            tenant_id=TenantId(tenant_id),
            event_type=event_type,
            attempt_number=attempt_number,
            http_status=http_status,
            succeeded=succeeded,
            error=error or "",
            attempted_at=attempted_at,
        )


class WebhookDLQRepository(BaseRepository):
    """Dedicated dead-letter-queue store for exhausted webhook deliveries
    (Sprint-025 Part-3, migration 0025) -- supports future replay via ``mark_replayed``."""

    def create(self, entry: WebhookDLQEntry) -> WebhookDLQEntry:
        self._execute(
            f"""
            INSERT INTO {_WEBHOOK_DLQ_TABLE} (
                dlq_id, webhook_id, tenant_id, event_type, payload, attempts, last_error, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry.dlq_id,
                entry.webhook_id,
                entry.tenant_id,
                entry.event_type,
                json.dumps(entry.payload),
                entry.attempts,
                entry.last_error,
                entry.created_at,
            ),
        )
        self._commit()
        return entry

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[WebhookDLQEntry, ...]:
        rows = self._tenant_select(
            _WEBHOOK_DLQ_TABLE, _WEBHOOK_DLQ_COLUMNS, tenant_id, extra_where="replayed_at IS NULL"
        )
        return tuple(self._hydrate(row) for row in rows)

    def mark_replayed(self, tenant_id: TenantId, dlq_id: str, replayed_at: Any) -> int:
        return self._tenant_update(
            _WEBHOOK_DLQ_TABLE,
            ("replayed_at",),
            (replayed_at,),
            tenant_id,
            extra_where="dlq_id = %s",
            extra_params=(dlq_id,),
        )

    def _hydrate(self, row: tuple[Any, ...]) -> WebhookDLQEntry:
        dlq_id, webhook_id, tenant_id, event_type, payload_json, attempts, last_error, created_at, replayed_at = row
        payload = json.loads(payload_json) if isinstance(payload_json, str) else (payload_json or {})
        return WebhookDLQEntry(
            dlq_id=str(dlq_id),
            webhook_id=str(webhook_id),
            tenant_id=TenantId(tenant_id),
            event_type=event_type,
            payload=payload,
            attempts=attempts,
            last_error=last_error or "",
            created_at=created_at,
            replayed_at=replayed_at,
        )


class APIKeyUsageRepository(BaseRepository):
    """Per-call usage log for API keys (Sprint-025 Part-3, migration 0025).

    Backs the API key lifecycle's "audit logging"/"rate limit association"
    requirements: every Public API request attributed to a key is recorded here.
    """

    def record(self, tenant_id: TenantId, api_key_id: str, route: str, status_code: int) -> None:
        self._execute(
            f"""
            INSERT INTO {_API_KEY_USAGE_TABLE} (api_key_id, tenant_id, route, status_code)
            VALUES (%s, %s, %s, %s)
            """,
            (api_key_id, tenant_id, route, status_code),
        )
        self._commit()

    def count_since(self, tenant_id: TenantId, api_key_id: str, since: Any) -> int:
        rows = self._tenant_select(
            _API_KEY_USAGE_TABLE,
            ("usage_id",),
            tenant_id,
            extra_where="api_key_id = %s AND occurred_at >= %s",
            extra_params=(api_key_id, since),
        )
        return len(rows)

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[APIKeyUsageRecord, ...]:
        rows = self._tenant_select(_API_KEY_USAGE_TABLE, _API_KEY_USAGE_COLUMNS, tenant_id, order_by="occurred_at DESC")
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> APIKeyUsageRecord:
        usage_id, api_key_id, tenant_id, route, status_code, occurred_at = row
        return APIKeyUsageRecord(
            usage_id=str(usage_id),
            api_key_id=str(api_key_id),
            tenant_id=TenantId(tenant_id),
            route=route,
            status_code=status_code,
            occurred_at=occurred_at,
        )


class APIRateLimitRepository(BaseRepository):
    """Persisted per-tier rate-limit configuration (Sprint-025 Part-3, migration 0025).

    Not tenant-scoped -- ``api_rate_limits`` is a small, plan-level (not
    per-tenant) configuration table, same shape as a lookup/reference table.
    """

    def get(self, tier: str) -> APIRateLimitConfig | None:
        cur = self._execute(
            "SELECT tier, requests_per_second, burst_capacity, updated_at "
            f"FROM {_API_RATE_LIMITS_TABLE} WHERE tier = %s",
            (tier,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        tier_value, rps, burst, updated_at = row
        return APIRateLimitConfig(tier=tier_value, requests_per_second=rps, burst_capacity=burst, updated_at=updated_at)

    def upsert(self, config: APIRateLimitConfig) -> APIRateLimitConfig:
        self._execute(
            f"""
            INSERT INTO {_API_RATE_LIMITS_TABLE} (tier, requests_per_second, burst_capacity, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tier) DO UPDATE SET
                requests_per_second = EXCLUDED.requests_per_second,
                burst_capacity = EXCLUDED.burst_capacity,
                updated_at = EXCLUDED.updated_at
            """,
            (config.tier, config.requests_per_second, config.burst_capacity, config.updated_at),
        )
        self._commit()
        return config


__all__ = [
    "APIKeyRepository",
    "APIKeyUsageRepository",
    "APIRateLimitRepository",
    "WebhookDLQRepository",
    "WebhookDeliveryAttemptRepository",
    "WebhookDeliveryRepository",
    "WebhookRegistrationRepository",
]
