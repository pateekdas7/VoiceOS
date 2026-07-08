"""IntegrationPlatformService -- façade over webhook registration and delivery (V5 Ch15).

Library-class facade, no standalone HTTP listener until Sprint-026 (same
precedent as every service since Sprint-013 -- see CPU_NODE_STATE.md §8.1).

Architecture: V5 Ch15 (Integration Platform).
"""

from __future__ import annotations

from src.libs.contracts.models.integration import WebhookDelivery, WebhookRegistration
from src.libs.contracts.primitives import TenantId

from .delivery import WebhookDeliveryEngine
from .webhook import ConsumerPort, WebhookService


class IntegrationPlatformService:
    """Façade over :class:`WebhookService` and :class:`WebhookDeliveryEngine`."""

    def __init__(self, webhook_service: WebhookService, delivery_engine: WebhookDeliveryEngine) -> None:
        self._webhooks = webhook_service
        self._delivery = delivery_engine

    @property
    def webhooks(self) -> WebhookService:
        return self._webhooks

    def register_webhook(
        self, tenant_id: TenantId, url: str, event_types: tuple[str, ...], secret: str | None = None
    ) -> WebhookRegistration:
        return self._webhooks.register_endpoint(tenant_id, url, event_types, secret)

    def update_webhook(self, tenant_id: TenantId, webhook_id: str, *, url: str, event_types: tuple[str, ...]) -> None:
        self._webhooks.update_endpoint(tenant_id, webhook_id, url=url, event_types=event_types)

    def deactivate_webhook(self, tenant_id: TenantId, webhook_id: str) -> None:
        self._webhooks.deactivate_endpoint(tenant_id, webhook_id)

    def rotate_webhook_secret(self, tenant_id: TenantId, webhook_id: str) -> str:
        return self._webhooks.rotate_secret(tenant_id, webhook_id)

    def delivery_history(self, tenant_id: TenantId, webhook_id: str) -> tuple[WebhookDelivery, ...]:
        return self._delivery.history(tenant_id, webhook_id)

    def dlq_entries(self, tenant_id: TenantId) -> tuple[WebhookDelivery, ...]:
        return self._delivery.dlq(tenant_id)

    def wire_event_bus(self, consumer: ConsumerPort) -> None:
        self._webhooks.register(consumer)


__all__ = ["IntegrationPlatformService"]
