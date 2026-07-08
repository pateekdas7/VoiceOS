"""DataErasureJob — orchestrates the right-to-erasure workflow (V4 Ch9 §9.11/§9.12).

Steps (Sprint-019.md, matching the architecture's erasure sequence diagram
V4 Ch9 §9.11): verify consent revocation → crypto-shred DEK(s) → tombstone
non-encrypted PII fields → delete audio recordings from object storage →
write a DataErasureCertificate → emit ``DataErasureCompleted``.

Every collaborator is a narrow Protocol so this module has no dependency on
``src.libs.repositories`` or ``src.libs.event_bus`` concrete classes —
callers (Sprint-022 CRM/Collections' real wiring) inject whatever
repository/event-bus instance already implements the matching method.

Architecture: V4 Ch9 §9.7 (``erasure_request``), §9.11 (sequence), §9.12
("crypto-shredding + tombstoning... reconciling erasure with immutable-audit
obligations").
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from src.libs.encryption.crypto_shred import CryptoShredder
from src.libs.privacy.metrics import record_erasure_job


class ConsentNotRevokedError(Exception):
    """Raised when erasure is requested but consent has not been revoked for this customer."""


class ConsentCheckProtocol(Protocol):
    """Read-only consent-state lookup (matches ConsentRepository.check_consent)."""

    def check_consent(self, tenant_id: str, customer_id: str, consent_type: Any) -> Any: ...


class TombstoneProtocol(Protocol):
    """Overwrites non-encrypted PII fields with a tombstone marker for a customer."""

    def tombstone_pii(self, tenant_id: str, customer_id: str) -> None: ...


class ObjectStoreProtocol(Protocol):
    """Deletes an object (e.g. an audio recording) by key."""

    def delete(self, key: str) -> None: ...


class CertificateStoreProtocol(Protocol):
    """Persists a DataErasureCertificate as durable proof of erasure."""

    def save(self, certificate: DataErasureCertificate) -> None: ...


class EventPublisherProtocol(Protocol):
    """Publishes a domain event (matches EventBusPort.publish semantics)."""

    def publish(self, event_type: str, payload: dict[str, Any]) -> None: ...


class DataErasureCertificate(BaseModel):
    """Immutable, durable proof that a customer's data was erased."""

    model_config = ConfigDict(frozen=True)

    certificate_id: str
    tenant_id: str
    customer_id: str
    scope: tuple[str, ...]
    method: str
    completed_at: datetime


class ErasureResult(BaseModel):
    """Outcome of a DataErasureJob run, per V4 Ch9 §9.6."""

    model_config = ConfigDict(frozen=True)

    customer_id: str
    scope: tuple[str, ...]
    method: str
    verified: bool
    completed_at: datetime


class DataErasureJob:
    """Orchestrates the full right-to-erasure workflow for one customer."""

    def __init__(
        self,
        consent_checker: ConsentCheckProtocol,
        crypto_shredder: CryptoShredder,
        tombstone_store: TombstoneProtocol,
        object_store: ObjectStoreProtocol,
        certificate_store: CertificateStoreProtocol,
        event_publisher: EventPublisherProtocol | None = None,
    ) -> None:
        self._consent_checker = consent_checker
        self._crypto_shredder = crypto_shredder
        self._tombstone_store = tombstone_store
        self._object_store = object_store
        self._certificate_store = certificate_store
        self._event_publisher = event_publisher

    def execute(
        self,
        tenant_id: str,
        customer_id: str,
        consent_type: Any,
        dek_ids: Sequence[str],
        audio_object_keys: Sequence[str],
        actor_id: str = "system",
    ) -> ErasureResult:
        """Run the full erasure workflow, raising if consent was never revoked.

        Raises:
            ConsentNotRevokedError: erasure was requested without a prior
                consent revocation on record (V4 Ch9 §9.11 step 1).
        """
        consent = self._consent_checker.check_consent(tenant_id, customer_id, consent_type)
        status = getattr(consent, "status", None)
        if consent is None or getattr(status, "value", status) != "REVOKED":
            record_erasure_job("consent_not_revoked")
            raise ConsentNotRevokedError(f"cannot erase customer={customer_id}: consent not revoked (status={status})")

        for dek_id in dek_ids:
            self._crypto_shredder.shred(tenant_id, dek_id, actor_id)

        self._tombstone_store.tombstone_pii(tenant_id, customer_id)

        for key in audio_object_keys:
            self._object_store.delete(key)

        completed_at = datetime.now(UTC)
        scope = ("postgres_pii", "audio_recordings", *(f"dek:{d}" for d in dek_ids))
        certificate = DataErasureCertificate(
            certificate_id=str(uuid4()),
            tenant_id=tenant_id,
            customer_id=customer_id,
            scope=scope,
            method="crypto_shred_plus_tombstone",
            completed_at=completed_at,
        )
        self._certificate_store.save(certificate)
        record_erasure_job("completed")

        if self._event_publisher is not None:
            self._event_publisher.publish(
                "DataErasureCompleted",
                {
                    "tenant_id": tenant_id,
                    "customer_id": customer_id,
                    "certificate_id": certificate.certificate_id,
                    "completed_at": completed_at.isoformat(),
                },
            )

        return ErasureResult(
            customer_id=customer_id,
            scope=scope,
            method="crypto_shred_plus_tombstone",
            verified=True,
            completed_at=completed_at,
        )
