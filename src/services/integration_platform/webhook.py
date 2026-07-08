"""WebhookService -- endpoint registration + domain-event-to-webhook fanout (V5 Ch15).

Registers tenant webhook endpoints and subscribes to the EventBus (same
``register(consumer)`` pattern as ``UsageCollector``, Sprint-024) to fan
each matching domain event out to every active, subscribed registration via
:class:`WebhookDeliveryEngine`.

Event mapping (Sprint-025.md names the webhook vocabulary; none of the 5
had an exact 1:1 domain event before this sprint except ``ptp.created`` --
same "spec names an event that doesn't exist" gap as Sprint-024's
``CallCompleted``, see CHANGELOG.md):

| Webhook event       | Source domain event          |
|---------------------|-------------------------------|
| ``call.completed``  | ``saas.call.dispositioned``   |
| ``ptp.created``     | ``saas.ptp.created``          |
| ``ptp.broken``      | ``saas.ptp.broken`` (net-new) |
| ``campaign.completed`` | ``saas.campaign.completed`` (net-new) |
| ``transfer.initiated`` | ``saas.call.transferred`` (fires once the agent bridge is live) |

Architecture: V5 Ch15 (Integration Platform).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from src.libs.contracts.models.integration import WEBHOOK_EVENT_TYPES, WebhookRegistration
from src.libs.contracts.primitives import TenantId

_SECRET_BYTES = 32
"""Length of a generated webhook secret (secrets.token_urlsafe input bytes)."""

_DISPATCH_IDEMPOTENCY_TTL_SECONDS = 86_400
_DISPATCH_RESOURCE_TYPE = "webhook_dispatch"

if TYPE_CHECKING:
    from .delivery import WebhookDeliveryEngine

DOMAIN_EVENT_TO_WEBHOOK_EVENT: dict[str, str] = {
    "saas.call.dispositioned": "call.completed",
    "saas.ptp.created": "ptp.created",
    "saas.ptp.broken": "ptp.broken",
    "saas.campaign.completed": "campaign.completed",
    "saas.call.transferred": "transfer.initiated",
}


class UnknownWebhookEventError(ValueError):
    """Raised when registering a webhook for an event type outside ``WEBHOOK_EVENT_TYPES``."""


class ConsumerPort(Protocol):
    def subscribe(self, event_type: str, handler: Any) -> None: ...


class WebhookNotFoundError(LookupError):
    """Raised when an operation targets a webhook_id that doesn't exist for the tenant."""


class WebhookRegistrationRepositoryPort(Protocol):
    def create(self, registration: WebhookRegistration) -> WebhookRegistration: ...
    def get(self, tenant_id: TenantId, webhook_id: str) -> WebhookRegistration | None: ...
    def find_active_for_event(self, tenant_id: TenantId, event_type: str) -> tuple[WebhookRegistration, ...]: ...
    def list_for_tenant(self, tenant_id: TenantId) -> tuple[WebhookRegistration, ...]: ...
    def update(self, tenant_id: TenantId, webhook_id: str, *, url: str, event_types: tuple[str, ...]) -> int: ...
    def deactivate(self, tenant_id: TenantId, webhook_id: str) -> int: ...
    def rotate_secret(self, tenant_id: TenantId, webhook_id: str, new_secret: str) -> int: ...


class IdempotencyRepositoryPort(Protocol):
    """Structural port for ``IdempotencyRepository`` (V3 Ch8, Sprint-015) -- used here to make
    domain-event -> webhook-delivery dispatch exactly-once even if the EventBus consumer group
    redelivers the same event (at-least-once upstream, exactly-once at the dispatch boundary)."""

    def claim(self, tenant_id: TenantId, key: str, resource_type: str, *, ttl_seconds: int = ...) -> bool: ...
    def complete(self, tenant_id: TenantId, key: str, result: Any) -> None: ...


class WebhookService:
    """Registers webhook endpoints and fans out matching domain events to them (V5 Ch15).

    ``idempotency_repository`` is additive (Sprint-025 Part-3): when wired,
    ``dispatch()`` claims a deterministic key per (webhook_id, event_type,
    payload) before delivering, so redelivery of the same domain event (the
    EventBus's own at-least-once consumer-group semantics) never causes a
    second delivery attempt to the same webhook for the same event.
    """

    def __init__(
        self,
        repository: WebhookRegistrationRepositoryPort,
        delivery_engine: WebhookDeliveryEngine,
        idempotency_repository: IdempotencyRepositoryPort | None = None,
    ) -> None:
        self._repo = repository
        self._delivery = delivery_engine
        self._idempotency = idempotency_repository

    def register_endpoint(
        self, tenant_id: TenantId, url: str, event_types: tuple[str, ...], secret: str | None = None
    ) -> WebhookRegistration:
        """Register a webhook endpoint. ``secret`` is server-generated when omitted (Sprint-025.md:
        "secret generation") -- the caller must capture it from the returned registration, as it is
        never re-shown by ``list_for_tenant``/admin listings."""
        unknown = set(event_types) - set(WEBHOOK_EVENT_TYPES)
        if unknown:
            raise UnknownWebhookEventError(f"unknown webhook event type(s): {sorted(unknown)}")
        registration = WebhookRegistration(
            webhook_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            url=url,
            secret=secret if secret is not None else self.generate_secret(),
            event_types=event_types,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        return self._repo.create(registration)

    @staticmethod
    def generate_secret() -> str:
        return secrets.token_urlsafe(_SECRET_BYTES)

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[WebhookRegistration, ...]:
        return self._repo.list_for_tenant(tenant_id)

    def update_endpoint(self, tenant_id: TenantId, webhook_id: str, *, url: str, event_types: tuple[str, ...]) -> None:
        unknown = set(event_types) - set(WEBHOOK_EVENT_TYPES)
        if unknown:
            raise UnknownWebhookEventError(f"unknown webhook event type(s): {sorted(unknown)}")
        if self._repo.update(tenant_id, webhook_id, url=url, event_types=event_types) == 0:
            raise WebhookNotFoundError(webhook_id)

    def deactivate_endpoint(self, tenant_id: TenantId, webhook_id: str) -> None:
        """Deactivate (soft-delete) a webhook endpoint -- preserves delivery history, per the
        codebase's no-hard-delete convention (Tenant/Campaign lifecycle use status transitions,
        never row deletion)."""
        if self._repo.deactivate(tenant_id, webhook_id) == 0:
            raise WebhookNotFoundError(webhook_id)

    def rotate_secret(self, tenant_id: TenantId, webhook_id: str) -> str:
        """Generate and persist a new signing secret, returned once to the caller."""
        new_secret = self.generate_secret()
        if self._repo.rotate_secret(tenant_id, webhook_id, new_secret) == 0:
            raise WebhookNotFoundError(webhook_id)
        return new_secret

    def register(self, consumer: ConsumerPort) -> None:
        """Subscribe the domain-event handlers on ``consumer`` (a ``Consumer`` or test double)."""
        for domain_event_type in DOMAIN_EVENT_TO_WEBHOOK_EVENT:
            consumer.subscribe(domain_event_type, self._make_handler(domain_event_type))

    def _make_handler(self, domain_event_type: str) -> Any:
        webhook_event_type = DOMAIN_EVENT_TO_WEBHOOK_EVENT[domain_event_type]

        def _handler(payload: dict[str, Any]) -> None:
            self.dispatch(webhook_event_type, payload)

        return _handler

    def dispatch(self, webhook_event_type: str, payload: dict[str, Any]) -> None:
        """Deliver ``payload`` to every active registration subscribed to ``webhook_event_type``,
        exactly once per (webhook_id, event) when an idempotency repository is wired."""
        tenant_id = TenantId(str(payload["tenant_id"]))
        for registration in self._repo.find_active_for_event(tenant_id, webhook_event_type):
            if self._idempotency is not None:
                key = self._dispatch_key(registration.webhook_id, webhook_event_type, payload)
                if not self._idempotency.claim(
                    tenant_id, key, _DISPATCH_RESOURCE_TYPE, ttl_seconds=_DISPATCH_IDEMPOTENCY_TTL_SECONDS
                ):
                    continue  # already dispatched (or in-flight) for this event -- exactly-once
                self._delivery.deliver(registration, webhook_event_type, payload)
                self._idempotency.complete(tenant_id, key, {"dispatched": True})
            else:
                self._delivery.deliver(registration, webhook_event_type, payload)

    @staticmethod
    def _dispatch_key(webhook_id: str, event_type: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload_hash = hashlib.sha256(canonical).hexdigest()
        return f"{_DISPATCH_RESOURCE_TYPE}:{webhook_id}:{event_type}:{payload_hash}"


__all__ = [
    "DOMAIN_EVENT_TO_WEBHOOK_EVENT",
    "IdempotencyRepositoryPort",
    "UnknownWebhookEventError",
    "WebhookNotFoundError",
    "WebhookRegistrationRepositoryPort",
    "WebhookService",
]
