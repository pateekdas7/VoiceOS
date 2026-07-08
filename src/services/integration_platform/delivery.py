"""WebhookDeliveryEngine -- HTTP POST delivery with retry + signature (V5 Ch15, Sprint-025.md).

Retry: 3 attempts with exponential backoff (5s, 30s, 5min); the 4th failure
routes to the DLQ (Sprint-025.md AC). ``sleep_fn``/``backoff_seconds`` are
constructor parameters (not hardcoded ``time.sleep`` calls) purely so tests
can exercise the full 3-retry-then-DLQ path without actually waiting
~5.5 minutes -- the production default is the real spec backoff.

Architecture: V5 Ch15 (Integration Platform -- Webhook Delivery).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from src.libs.contracts.models.integration import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookDLQEntry,
    WebhookRegistration,
)
from src.libs.contracts.primitives import TenantId

from . import metrics
from .signature import SIGNATURE_HEADER, WebhookSigner

_logger = logging.getLogger(__name__)

DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (5.0, 30.0, 300.0)
MAX_ATTEMPTS = len(DEFAULT_BACKOFF_SECONDS)


class HTTPResponse(Protocol):
    status_code: int


class HTTPClientPort(Protocol):
    def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float = ...) -> HTTPResponse: ...


class WebhookDeliveryRepositoryPort(Protocol):
    def create(self, delivery: WebhookDelivery) -> WebhookDelivery: ...
    def list_for_webhook(self, tenant_id: TenantId, webhook_id: str) -> tuple[WebhookDelivery, ...]: ...
    def find_dlq(self, tenant_id: TenantId) -> tuple[WebhookDelivery, ...]: ...


class WebhookDeliveryAttemptRepositoryPort(Protocol):
    def record(
        self,
        tenant_id: TenantId,
        webhook_id: str,
        event_type: str,
        attempt_number: int,
        http_status: int | None,
        succeeded: bool,
        error: str,
    ) -> None: ...


class WebhookDLQRepositoryPort(Protocol):
    def create(self, entry: WebhookDLQEntry) -> WebhookDLQEntry: ...


class WebhookDeliveryEngine:
    """Delivers a signed webhook POST, retrying on failure, DLQ-ing after exhaustion.

    ``attempt_repository``/``dlq_repository`` are additive (Sprint-025 Part-3):
    when wired, every individual HTTP attempt is appended to
    ``webhook_delivery_attempts`` and an exhausted delivery is also recorded
    in the dedicated ``webhook_dead_letter_queue`` store -- in addition to,
    not instead of, the existing ``webhook_deliveries`` summary row via
    ``delivery_repository``.
    """

    def __init__(
        self,
        http_client: HTTPClientPort,
        delivery_repository: WebhookDeliveryRepositoryPort | None = None,
        *,
        backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
        sleep_fn: Any = time.sleep,
        attempt_repository: WebhookDeliveryAttemptRepositoryPort | None = None,
        dlq_repository: WebhookDLQRepositoryPort | None = None,
    ) -> None:
        self._http = http_client
        self._repo = delivery_repository
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep_fn
        self._attempt_repo = attempt_repository
        self._dlq_repo = dlq_repository

    def deliver(
        self, registration: WebhookRegistration, event_type: str, payload: dict[str, object]
    ) -> WebhookDelivery:
        """POST ``payload`` to ``registration.url``, retrying up to ``MAX_ATTEMPTS`` times."""
        signature = WebhookSigner.sign(payload, registration.secret)
        body = WebhookSigner.canonical_payload(payload)
        headers = {"Content-Type": "application/json", SIGNATURE_HEADER: signature}

        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            _logger.info("webhook delivery attempt", extra={"webhook_id": registration.webhook_id, "attempt": attempt})
            http_status: int | None = None
            try:
                response = self._http.post(registration.url, content=body, headers=headers, timeout=10.0)
                http_status = response.status_code
                if 200 <= response.status_code < 300:
                    metrics.record_delivery_attempt("success")
                    _logger.info(
                        "webhook delivery succeeded",
                        extra={"webhook_id": registration.webhook_id, "attempt": attempt},
                    )
                    self._record_attempt(registration, event_type, attempt, http_status, True, "")
                    return self._record(registration, event_type, payload, attempt, WebhookDeliveryStatus.DELIVERED, "")
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = str(exc)

            metrics.record_delivery_attempt("failure")
            _logger.warning(
                "webhook delivery attempt failed",
                extra={"webhook_id": registration.webhook_id, "attempt": attempt, "error": last_error},
            )
            self._record_attempt(registration, event_type, attempt, http_status, False, last_error)
            if attempt < MAX_ATTEMPTS:
                self._sleep(self._backoff_seconds[attempt - 1])

        metrics.record_dlq_entry()
        _logger.error(
            "webhook delivery exhausted retries -> DLQ",
            extra={"webhook_id": registration.webhook_id, "attempts": MAX_ATTEMPTS, "error": last_error},
        )
        if self._dlq_repo is not None:
            self._dlq_repo.create(
                WebhookDLQEntry(
                    dlq_id=str(uuid.uuid4()),
                    webhook_id=registration.webhook_id,
                    tenant_id=TenantId(registration.tenant_id),
                    event_type=event_type,
                    payload=payload,
                    attempts=MAX_ATTEMPTS,
                    last_error=last_error,
                    created_at=datetime.now(UTC),
                )
            )
        return self._record(registration, event_type, payload, MAX_ATTEMPTS, WebhookDeliveryStatus.DLQ, last_error)

    def _record_attempt(
        self,
        registration: WebhookRegistration,
        event_type: str,
        attempt: int,
        http_status: int | None,
        succeeded: bool,
        error: str,
    ) -> None:
        if self._attempt_repo is not None:
            self._attempt_repo.record(
                TenantId(registration.tenant_id),
                registration.webhook_id,
                event_type,
                attempt,
                http_status,
                succeeded,
                error,
            )

    def history(self, tenant_id: TenantId, webhook_id: str) -> tuple[WebhookDelivery, ...]:
        """Delivery history for one webhook, newest first. Empty when running without persistence."""
        return self._repo.list_for_webhook(tenant_id, webhook_id) if self._repo is not None else ()

    def dlq(self, tenant_id: TenantId) -> tuple[WebhookDelivery, ...]:
        """All deliveries currently in the DLQ for a tenant, across every webhook."""
        return self._repo.find_dlq(tenant_id) if self._repo is not None else ()

    def _record(
        self,
        registration: WebhookRegistration,
        event_type: str,
        payload: dict[str, object],
        attempt: int,
        status: WebhookDeliveryStatus,
        last_error: str,
    ) -> WebhookDelivery:
        delivery = WebhookDelivery(
            delivery_id=str(uuid.uuid4()),
            webhook_id=registration.webhook_id,
            tenant_id=TenantId(registration.tenant_id),
            event_type=event_type,
            payload=payload,
            attempt=attempt,
            status=status,
            last_error=last_error,
            created_at=datetime.now(UTC),
            delivered_at=datetime.now(UTC) if status == WebhookDeliveryStatus.DELIVERED else None,
        )
        if self._repo is not None:
            self._repo.create(delivery)
        return delivery


__all__ = [
    "DEFAULT_BACKOFF_SECONDS",
    "MAX_ATTEMPTS",
    "WebhookDLQRepositoryPort",
    "WebhookDeliveryAttemptRepositoryPort",
    "WebhookDeliveryEngine",
    "WebhookDeliveryRepositoryPort",
]
