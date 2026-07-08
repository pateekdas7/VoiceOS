"""Unit tests for the Integration Platform -- webhooks (Sprint-025, V5 Ch15).

All tests run fully in-process -- no live Postgres/Redis/network required
(Phase 1). The HTTP transport is a small fake double, mirroring the
``_Fake*Repository``/fake-transport precedent used throughout this suite.

Required named tests (Sprint-025.md):
    test_webhook_signature_valid -- verify HMAC signature on delivery
    test_webhook_retry_on_failure -- delivery fails 3 times -> DLQ entry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from src.libs.contracts.models.integration import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookDLQEntry,
    WebhookRegistration,
)
from src.libs.contracts.primitives import TenantId
from src.services.integration_platform.delivery import WebhookDeliveryEngine
from src.services.integration_platform.signature import SIGNATURE_HEADER, WebhookSigner
from src.services.integration_platform.webhook import UnknownWebhookEventError, WebhookNotFoundError, WebhookService

_TENANT = TenantId("tenant-a")


@dataclass
class _FakeResponse:
    status_code: int


@dataclass
class _FakeHTTPClient:
    """Records every call; returns a scripted sequence of status codes."""

    responses: list[int] = field(default_factory=lambda: [200])
    calls: list[dict[str, object]] = field(default_factory=list)

    def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float = 10.0) -> _FakeResponse:
        self.calls.append({"url": url, "content": content, "headers": headers})
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return _FakeResponse(status_code=self.responses[index])


class _FakeWebhookRegistrationRepository:
    def __init__(self) -> None:
        self._registrations: dict[str, WebhookRegistration] = {}

    def create(self, registration: WebhookRegistration) -> WebhookRegistration:
        self._registrations[registration.webhook_id] = registration
        return registration

    def get(self, tenant_id: TenantId, webhook_id: str) -> WebhookRegistration | None:
        reg = self._registrations.get(webhook_id)
        return reg if reg is not None and reg.tenant_id == tenant_id else None

    def find_active_for_event(self, tenant_id: TenantId, event_type: str) -> tuple[WebhookRegistration, ...]:
        return tuple(
            r
            for r in self._registrations.values()
            if r.tenant_id == tenant_id and r.is_active and event_type in r.event_types
        )

    def list_for_tenant(self, tenant_id: TenantId) -> tuple[WebhookRegistration, ...]:
        return tuple(r for r in self._registrations.values() if r.tenant_id == tenant_id)

    def update(self, tenant_id: TenantId, webhook_id: str, *, url: str, event_types: tuple[str, ...]) -> int:
        reg = self.get(tenant_id, webhook_id)
        if reg is None:
            return 0
        self._registrations[webhook_id] = reg.model_copy(update={"url": url, "event_types": event_types})
        return 1

    def deactivate(self, tenant_id: TenantId, webhook_id: str) -> int:
        reg = self.get(tenant_id, webhook_id)
        if reg is None:
            return 0
        self._registrations[webhook_id] = reg.model_copy(update={"is_active": False})
        return 1

    def rotate_secret(self, tenant_id: TenantId, webhook_id: str, new_secret: str) -> int:
        reg = self.get(tenant_id, webhook_id)
        if reg is None:
            return 0
        self._registrations[webhook_id] = reg.model_copy(update={"secret": new_secret})
        return 1


class _FakeWebhookDeliveryRepository:
    def __init__(self) -> None:
        self.deliveries: list[WebhookDelivery] = []

    def create(self, delivery: WebhookDelivery) -> WebhookDelivery:
        self.deliveries.append(delivery)
        return delivery

    def list_for_webhook(self, tenant_id: TenantId, webhook_id: str) -> tuple[WebhookDelivery, ...]:
        return tuple(d for d in self.deliveries if d.tenant_id == tenant_id and d.webhook_id == webhook_id)

    def find_dlq(self, tenant_id: TenantId) -> tuple[WebhookDelivery, ...]:
        return tuple(d for d in self.deliveries if d.tenant_id == tenant_id and d.status == WebhookDeliveryStatus.DLQ)


def _registration(secret: str = "top-secret") -> WebhookRegistration:
    return WebhookRegistration(
        webhook_id="wh-1",
        tenant_id=_TENANT,
        url="https://tenant.example.com/webhooks/voiceos",
        secret=secret,
        event_types=("call.completed", "ptp.created"),
        created_at=datetime.now(UTC),
    )


class TestWebhookSigner:
    def test_webhook_signature_valid(self) -> None:
        payload: dict[str, object] = {"tenant_id": str(_TENANT), "call_id": "call-1", "outcome_code": "PTP_MADE"}
        signature = WebhookSigner.sign(payload, "top-secret")

        assert signature.startswith("sha256=")
        assert WebhookSigner.verify(payload, "top-secret", signature)
        assert not WebhookSigner.verify(payload, "wrong-secret", signature)
        assert not WebhookSigner.verify({**payload, "call_id": "tampered"}, "top-secret", signature)

    def test_delivery_sends_signature_header(self) -> None:
        client = _FakeHTTPClient(responses=[200])
        engine = WebhookDeliveryEngine(client)
        registration = _registration()
        payload: dict[str, object] = {"tenant_id": str(_TENANT), "call_id": "call-1"}

        delivery = engine.deliver(registration, "call.completed", payload)

        assert delivery.status == WebhookDeliveryStatus.DELIVERED
        assert delivery.attempt == 1
        sent_headers = client.calls[0]["headers"]
        assert isinstance(sent_headers, dict)
        assert SIGNATURE_HEADER in sent_headers
        assert WebhookSigner.verify(payload, registration.secret, sent_headers[SIGNATURE_HEADER])


class TestWebhookDeliveryEngine:
    def test_webhook_retry_on_failure(self) -> None:
        client = _FakeHTTPClient(responses=[500, 500, 500])
        delivery_repo = _FakeWebhookDeliveryRepository()
        engine = WebhookDeliveryEngine(client, delivery_repo, sleep_fn=lambda _seconds: None)
        registration = _registration()

        delivery = engine.deliver(registration, "call.completed", {"tenant_id": str(_TENANT)})

        assert delivery.status == WebhookDeliveryStatus.DLQ
        assert delivery.attempt == 3
        assert len(client.calls) == 3
        assert len(delivery_repo.deliveries) == 1

    def test_succeeds_on_second_attempt(self) -> None:
        client = _FakeHTTPClient(responses=[500, 200])
        engine = WebhookDeliveryEngine(client, sleep_fn=lambda _seconds: None)
        registration = _registration()

        delivery = engine.deliver(registration, "call.completed", {"tenant_id": str(_TENANT)})

        assert delivery.status == WebhookDeliveryStatus.DELIVERED
        assert delivery.attempt == 2
        assert len(client.calls) == 2


class TestWebhookService:
    def test_register_endpoint_rejects_unknown_event_type(self) -> None:
        service = WebhookService(_FakeWebhookRegistrationRepository(), WebhookDeliveryEngine(_FakeHTTPClient()))
        with pytest.raises(UnknownWebhookEventError):
            service.register_endpoint(_TENANT, "https://example.com", ("not.a.real.event",), "secret")

    def test_register_endpoint_generates_secret_when_omitted(self) -> None:
        service = WebhookService(_FakeWebhookRegistrationRepository(), WebhookDeliveryEngine(_FakeHTTPClient()))

        registration = service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",))

        assert registration.secret
        another = service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",))
        assert another.secret != registration.secret

    def test_dispatch_delivers_to_matching_registrations(self) -> None:
        client = _FakeHTTPClient(responses=[200])
        repo = _FakeWebhookRegistrationRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(client))
        service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",), "secret")

        service.dispatch("ptp.created", {"tenant_id": str(_TENANT), "ptp_id": "ptp-1"})

        assert len(client.calls) == 1

    def test_dispatch_skips_unregistered_event_type(self) -> None:
        client = _FakeHTTPClient(responses=[200])
        repo = _FakeWebhookRegistrationRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(client))
        service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",), "secret")

        service.dispatch("campaign.completed", {"tenant_id": str(_TENANT)})

        assert len(client.calls) == 0

    def test_update_endpoint_changes_url_and_events(self) -> None:
        repo = _FakeWebhookRegistrationRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(_FakeHTTPClient()))
        registration = service.register_endpoint(_TENANT, "https://old.example.com", ("ptp.created",), "secret")

        service.update_endpoint(
            _TENANT, registration.webhook_id, url="https://new.example.com", event_types=("campaign.completed",)
        )

        updated = repo.get(_TENANT, registration.webhook_id)
        assert updated is not None
        assert updated.url == "https://new.example.com"
        assert updated.event_types == ("campaign.completed",)

    def test_update_endpoint_rejects_unknown_event_type(self) -> None:
        repo = _FakeWebhookRegistrationRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(_FakeHTTPClient()))
        registration = service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",), "secret")

        with pytest.raises(UnknownWebhookEventError):
            service.update_endpoint(
                _TENANT, registration.webhook_id, url="https://example.com", event_types=("not.a.real.event",)
            )

    def test_update_endpoint_missing_webhook_raises(self) -> None:
        service = WebhookService(_FakeWebhookRegistrationRepository(), WebhookDeliveryEngine(_FakeHTTPClient()))
        with pytest.raises(WebhookNotFoundError):
            service.update_endpoint(_TENANT, "no-such-id", url="https://example.com", event_types=("ptp.created",))

    def test_deactivate_endpoint_stops_dispatch(self) -> None:
        client = _FakeHTTPClient(responses=[200])
        repo = _FakeWebhookRegistrationRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(client))
        registration = service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",), "secret")

        service.deactivate_endpoint(_TENANT, registration.webhook_id)
        service.dispatch("ptp.created", {"tenant_id": str(_TENANT), "ptp_id": "ptp-1"})

        assert len(client.calls) == 0

    def test_deactivate_endpoint_missing_webhook_raises(self) -> None:
        service = WebhookService(_FakeWebhookRegistrationRepository(), WebhookDeliveryEngine(_FakeHTTPClient()))
        with pytest.raises(WebhookNotFoundError):
            service.deactivate_endpoint(_TENANT, "no-such-id")

    def test_rotate_secret_changes_signing_secret(self) -> None:
        repo = _FakeWebhookRegistrationRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(_FakeHTTPClient()))
        registration = service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",), "old-secret")

        new_secret = service.rotate_secret(_TENANT, registration.webhook_id)

        assert new_secret != "old-secret"
        updated = repo.get(_TENANT, registration.webhook_id)
        assert updated is not None
        assert updated.secret == new_secret

    def test_rotate_secret_missing_webhook_raises(self) -> None:
        service = WebhookService(_FakeWebhookRegistrationRepository(), WebhookDeliveryEngine(_FakeHTTPClient()))
        with pytest.raises(WebhookNotFoundError):
            service.rotate_secret(_TENANT, "no-such-id")


class TestWebhookDeliveryHistory:
    def test_history_returns_deliveries_for_webhook(self) -> None:
        delivery_repo = _FakeWebhookDeliveryRepository()
        engine = WebhookDeliveryEngine(_FakeHTTPClient(responses=[200]), delivery_repo)
        registration = _registration()

        engine.deliver(registration, "call.completed", {"tenant_id": str(_TENANT)})
        history = engine.history(_TENANT, registration.webhook_id)

        assert len(history) == 1
        assert history[0].status == WebhookDeliveryStatus.DELIVERED

    def test_dlq_returns_only_dlq_entries(self) -> None:
        delivery_repo = _FakeWebhookDeliveryRepository()
        engine = WebhookDeliveryEngine(
            _FakeHTTPClient(responses=[500, 500, 500]), delivery_repo, sleep_fn=lambda _seconds: None
        )
        registration = _registration()

        engine.deliver(registration, "call.completed", {"tenant_id": str(_TENANT)})
        dlq = engine.dlq(_TENANT)

        assert len(dlq) == 1
        assert dlq[0].status == WebhookDeliveryStatus.DLQ

    def test_history_and_dlq_empty_without_repository(self) -> None:
        engine = WebhookDeliveryEngine(_FakeHTTPClient(responses=[200]))
        registration = _registration()
        engine.deliver(registration, "call.completed", {"tenant_id": str(_TENANT)})

        assert engine.history(_TENANT, registration.webhook_id) == ()
        assert engine.dlq(_TENANT) == ()


class _FakeWebhookDeliveryAttemptRepository:
    def __init__(self) -> None:
        self.attempts: list[tuple[TenantId, str, str, int, int | None, bool, str]] = []

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
        self.attempts.append((tenant_id, webhook_id, event_type, attempt_number, http_status, succeeded, error))


class _FakeWebhookDLQRepository:
    def __init__(self) -> None:
        self.entries: list[WebhookDLQEntry] = []

    def create(self, entry: WebhookDLQEntry) -> WebhookDLQEntry:
        self.entries.append(entry)
        return entry


class TestWebhookDeliveryAttemptsAndDLQTable:
    """Sprint-025 Part-3: webhook_delivery_attempts (per-attempt log) + dedicated
    webhook_dead_letter_queue store, additive alongside the existing webhook_deliveries
    summary row."""

    def test_every_attempt_recorded_on_success(self) -> None:
        attempt_repo = _FakeWebhookDeliveryAttemptRepository()
        engine = WebhookDeliveryEngine(_FakeHTTPClient(responses=[200]), attempt_repository=attempt_repo)
        registration = _registration()

        engine.deliver(registration, "call.completed", {"tenant_id": str(_TENANT)})

        assert len(attempt_repo.attempts) == 1
        _, webhook_id, event_type, attempt_number, http_status, succeeded, error = attempt_repo.attempts[0]
        assert webhook_id == registration.webhook_id
        assert event_type == "call.completed"
        assert attempt_number == 1
        assert http_status == 200
        assert succeeded is True
        assert error == ""

    def test_every_attempt_recorded_on_retry_then_dlq(self) -> None:
        attempt_repo = _FakeWebhookDeliveryAttemptRepository()
        dlq_repo = _FakeWebhookDLQRepository()
        engine = WebhookDeliveryEngine(
            _FakeHTTPClient(responses=[500, 500, 500]),
            attempt_repository=attempt_repo,
            dlq_repository=dlq_repo,
            sleep_fn=lambda _seconds: None,
        )
        registration = _registration()

        engine.deliver(registration, "call.completed", {"tenant_id": str(_TENANT)})

        assert len(attempt_repo.attempts) == 3
        assert all(not succeeded for (*_rest, succeeded, _error) in attempt_repo.attempts)
        assert len(dlq_repo.entries) == 1
        assert dlq_repo.entries[0].attempts == 3
        assert dlq_repo.entries[0].webhook_id == registration.webhook_id

    def test_dlq_not_recorded_when_delivery_succeeds(self) -> None:
        dlq_repo = _FakeWebhookDLQRepository()
        engine = WebhookDeliveryEngine(_FakeHTTPClient(responses=[200]), dlq_repository=dlq_repo)
        registration = _registration()

        engine.deliver(registration, "call.completed", {"tenant_id": str(_TENANT)})

        assert dlq_repo.entries == []


class _FakeIdempotencyRepository:
    def __init__(self) -> None:
        self._claimed: set[tuple[str, str]] = set()
        self.completed: list[tuple[str, str, object]] = []

    def claim(self, tenant_id: TenantId, key: str, resource_type: str, *, ttl_seconds: int = 86_400) -> bool:
        marker = (str(tenant_id), key)
        if marker in self._claimed:
            return False
        self._claimed.add(marker)
        return True

    def complete(self, tenant_id: TenantId, key: str, result: object) -> None:
        self.completed.append((str(tenant_id), key, result))


class TestWebhookDispatchIdempotency:
    """Sprint-025 Part-3: EventBus -> webhook delivery is exactly-once per
    (webhook_id, event_type, payload) when an idempotency repository is wired."""

    def test_duplicate_dispatch_delivers_only_once(self) -> None:
        client = _FakeHTTPClient(responses=[200])
        repo = _FakeWebhookRegistrationRepository()
        idempotency = _FakeIdempotencyRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(client), idempotency_repository=idempotency)
        service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",), "secret")
        payload = {"tenant_id": str(_TENANT), "ptp_id": "ptp-1"}

        service.dispatch("ptp.created", payload)
        service.dispatch("ptp.created", payload)  # simulated EventBus redelivery

        assert len(client.calls) == 1

    def test_different_payload_still_dispatches(self) -> None:
        client = _FakeHTTPClient(responses=[200, 200])
        repo = _FakeWebhookRegistrationRepository()
        idempotency = _FakeIdempotencyRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(client), idempotency_repository=idempotency)
        service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",), "secret")

        service.dispatch("ptp.created", {"tenant_id": str(_TENANT), "ptp_id": "ptp-1"})
        service.dispatch("ptp.created", {"tenant_id": str(_TENANT), "ptp_id": "ptp-2"})

        assert len(client.calls) == 2

    def test_without_idempotency_repository_dispatches_every_call(self) -> None:
        """Pre-Sprint-025-Part-3 behavior preserved when unwired."""
        client = _FakeHTTPClient(responses=[200, 200])
        repo = _FakeWebhookRegistrationRepository()
        service = WebhookService(repo, WebhookDeliveryEngine(client))
        service.register_endpoint(_TENANT, "https://example.com", ("ptp.created",), "secret")
        payload = {"tenant_id": str(_TENANT), "ptp_id": "ptp-1"}

        service.dispatch("ptp.created", payload)
        service.dispatch("ptp.created", payload)

        assert len(client.calls) == 2
