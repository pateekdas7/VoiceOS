"""DPDP compliance automated test suite -- Sprint-028 "5. Compliance Validation" (V4 Ch2, Sprint-028).

Runs entirely in-process (Sprint-028.md's own Phase 1 "PolicyEngine:
FakePolicyEngine" mock-backend note) against a real ``PolicyEngine`` with
no Redis/Postgres backends, plus lightweight in-memory fakes for the
audit/privacy protocols consumed by ``AuditLogger``/``DataErasureJob``/
``RetentionScheduler`` -- exercising the real Sprint-019/020 library
logic, not a re-implemented parallel ruleset.

Scenarios (Sprint-028.md literal spec):
  - DPDP consent gate: 10 test customers without consent -> all calls blocked
  - Audit completeness: run 100 calls -> verify audit trail has all required event types
  - Data retention: verify no data exists past configured retention period (aged records)
  - Right to erasure: trigger erasure -> DataErasureCertificate created, PII inaccessible
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from src.libs.audit.event import (
    ACTION_AUTHN_LOGIN,
    ACTION_CONSENT_GRANTED,
    ACTION_DATA_ERASURE,
    ACTION_POLICY_DENIED,
    ACTION_PTP_CREATED,
)
from src.libs.audit.logger import AuditLogger
from src.libs.encryption.crypto_shred import CryptoShredder
from src.libs.encryption.dek_store import InMemoryDEKStore
from src.libs.privacy.erasure import DataErasureCertificate, DataErasureJob
from src.libs.privacy.purpose_registry import DataClass
from src.libs.privacy.retention import RetentionScheduler
from src.services.policy_engine.decision import PolicyOutcome
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.rule import PolicyRequest


def FakePolicyEngine() -> PolicyEngine:  # noqa: N802 -- see test_rbi_compliance.py's identical factory
    """A real ``PolicyEngine`` with every backend omitted -- see ``test_rbi_compliance.py``."""
    return PolicyEngine()


# ---------------------------------------------------------------------------
# DPDP consent gate: 10 customers without consent -> all calls blocked
# ---------------------------------------------------------------------------


class TestDPDPConsentGate:
    def test_ten_customers_without_consent_all_blocked(self) -> None:
        engine = FakePolicyEngine()
        results: list[PolicyOutcome] = []

        for i in range(10):
            request = PolicyRequest(
                domain="dpdp",
                action="process_customer_data",
                subject="compliance_suite",
                resource=f"customer-{i}",
                context={"has_consent": False, "customer_id": f"customer-{i}"},
            )
            decision = engine.evaluate(request)
            results.append(decision.outcome)

        assert len(results) == 10
        assert all(outcome in (PolicyOutcome.DENY, PolicyOutcome.FORBID) for outcome in results), (
            f"expected all 10 no-consent customers blocked, got outcomes: {results}"
        )

    def test_customer_with_consent_is_permitted(self) -> None:
        engine = FakePolicyEngine()
        request = PolicyRequest(
            domain="dpdp",
            action="process_customer_data",
            subject="compliance_suite",
            resource="customer-consented",
            context={"has_consent": True},
        )
        decision = engine.evaluate(request)
        assert decision.outcome == PolicyOutcome.PERMIT


# ---------------------------------------------------------------------------
# Audit completeness: 100 calls -> audit trail has all required event types
# ---------------------------------------------------------------------------


@dataclass
class _FakeAuditRepo:
    """Minimal in-memory stand-in for ``AuditRepository`` -- records ``.append()`` calls, no hash chain."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def append(
        self,
        tenant_id: Any,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        *,
        event_payload: dict[str, Any] | None = None,
        ip_address: str = "",
    ) -> str:
        row_hash = f"fake-hash-{len(self.rows) + 1}"
        self.rows.append(
            {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "outcome": outcome,
                "event_payload": event_payload,
                "ip_address": ip_address,
                "hash": row_hash,
            }
        )
        return row_hash


_REQUIRED_EVENT_TYPES = (
    ACTION_AUTHN_LOGIN,
    ACTION_CONSENT_GRANTED,
    ACTION_PTP_CREATED,
    ACTION_POLICY_DENIED,
)
"""The subset of Sprint-020's mandatory-coverage audit event types a normal
call-handling day is expected to produce at least once across 100 calls."""


class TestAuditCompleteness:
    def test_100_calls_produce_all_required_event_types(self) -> None:
        repo = _FakeAuditRepo()
        logger = AuditLogger(repo)
        tenant_id = str(uuid4())

        for i in range(100):
            actor_id = f"agent-{i % 5}"
            logger.record_authn(tenant_id, actor_id, "success")
            if i % 3 == 0:
                logger.record_consent_change(tenant_id, actor_id, f"customer-{i}", granted=True)
            if i % 7 == 0:
                logger.record_ptp_created(tenant_id, actor_id, f"ptp-{i}")
            if i % 11 == 0:
                logger.record_policy_denied(tenant_id, actor_id, "RBI-CALLING-HOURS", "outside permitted window")

        recorded_actions = {row["action"] for row in repo.rows}
        missing = set(_REQUIRED_EVENT_TYPES) - recorded_actions
        assert not missing, f"audit trail missing required event types: {missing}"
        assert len(repo.rows) >= 100

    def test_audit_payload_is_pii_redacted(self) -> None:
        repo = _FakeAuditRepo()
        logger = AuditLogger(repo)
        tenant_id = str(uuid4())

        logger.record(
            tenant_id,
            "agent-1",
            ACTION_DATA_ERASURE,
            "Customer",
            "customer-1",
            "success",
            event_payload={"note": "contact phone 9876543210 for confirmation"},
        )

        stored_payload = repo.rows[0]["event_payload"]
        assert stored_payload is not None
        assert "9876543210" not in stored_payload["note"]


# ---------------------------------------------------------------------------
# Data retention: no data past the configured retention period
# ---------------------------------------------------------------------------


class TestDataRetention:
    def test_records_past_retention_are_flagged_expired(self) -> None:
        scheduler = RetentionScheduler()
        now = datetime.now(UTC)
        stale_recording_created_at = now - timedelta(days=200)  # > 90d voice-recording retention

        assert scheduler.is_expired(DataClass.VOICE_RECORDING, stale_recording_created_at, now=now) is True

    def test_records_within_retention_are_not_flagged(self) -> None:
        scheduler = RetentionScheduler()
        now = datetime.now(UTC)
        fresh_recording_created_at = now - timedelta(days=10)

        assert scheduler.is_expired(DataClass.VOICE_RECORDING, fresh_recording_created_at, now=now) is False

    def test_legal_hold_overrides_retention_expiry(self) -> None:
        scheduler = RetentionScheduler()
        now = datetime.now(UTC)
        stale_created_at = now - timedelta(days=400)

        assert scheduler.is_expired(DataClass.VOICE_RECORDING, stale_created_at, legal_hold=True, now=now) is False

    def test_flag_expired_returns_only_the_aged_record_ids(self) -> None:
        scheduler = RetentionScheduler()
        now = datetime.now(UTC)
        records = [
            ("rec-fresh", now - timedelta(days=5)),
            ("rec-stale-1", now - timedelta(days=120)),
            ("rec-stale-2", now - timedelta(days=95)),
            ("rec-stale-under-legal-hold", now - timedelta(days=300)),
        ]

        expired = scheduler.flag_expired(
            DataClass.VOICE_RECORDING, records, legal_holds=frozenset({"rec-stale-under-legal-hold"}), now=now
        )

        assert set(expired) == {"rec-stale-1", "rec-stale-2"}


# ---------------------------------------------------------------------------
# Right to erasure: erasure -> DataErasureCertificate created, PII inaccessible
# ---------------------------------------------------------------------------


class _FakeConsentChecker:
    def __init__(self, *, revoked: bool) -> None:
        self._revoked = revoked

    def check_consent(self, tenant_id: str, customer_id: str, consent_type: Any) -> Any:
        return _RevokedConsent() if self._revoked else _ActiveConsent()


@dataclass
class _RevokedConsent:
    status: Any = "REVOKED"


@dataclass
class _ActiveConsent:
    status: Any = "GRANTED"


class _FakeTombstoneStore:
    def __init__(self) -> None:
        self.tombstoned: list[tuple[str, str]] = []

    def tombstone_pii(self, tenant_id: str, customer_id: str) -> None:
        self.tombstoned.append((tenant_id, customer_id))


class _FakeObjectStore:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted_keys.append(key)


class _FakeCertificateStore:
    def __init__(self) -> None:
        self.certificates: list[DataErasureCertificate] = []

    def save(self, certificate: DataErasureCertificate) -> None:
        self.certificates.append(certificate)


def _build_erasure_job() -> tuple[DataErasureJob, _FakeTombstoneStore, _FakeCertificateStore, InMemoryDEKStore]:
    dek_store = InMemoryDEKStore()
    crypto_shredder = CryptoShredder(dek_store)
    tombstone_store = _FakeTombstoneStore()
    object_store = _FakeObjectStore()
    certificate_store = _FakeCertificateStore()
    job = DataErasureJob(
        _FakeConsentChecker(revoked=True),
        crypto_shredder,
        tombstone_store,
        object_store,
        certificate_store,
    )
    return job, tombstone_store, certificate_store, dek_store


class TestRightToErasure:
    def test_erasure_creates_certificate_and_removes_pii_access(self) -> None:
        job, tombstone_store, certificate_store, dek_store = _build_erasure_job()
        dek_store.put("tenant-1", "dek-1", b"wrapped-dek-1", "kek-1")
        dek_store.put("tenant-1", "dek-2", b"wrapped-dek-2", "kek-1")

        result = job.execute(
            tenant_id="tenant-1",
            customer_id="customer-1",
            consent_type="DATA_PROCESSING",
            dek_ids=["dek-1", "dek-2"],
            audio_object_keys=["audio/call-1.wav"],
        )

        assert result.verified is True
        assert len(certificate_store.certificates) == 1
        certificate = certificate_store.certificates[0]
        assert certificate.customer_id == "customer-1"
        assert certificate.method == "crypto_shred_plus_tombstone"

        # PII inaccessible: DEKs shredded (ciphertext unrecoverable) and the
        # customer record tombstoned.
        assert dek_store.get("tenant-1", "dek-1") is None
        assert dek_store.get("tenant-1", "dek-2") is None
        assert ("tenant-1", "customer-1") in tombstone_store.tombstoned

    def test_erasure_without_revoked_consent_is_refused(self) -> None:
        from src.libs.privacy.erasure import ConsentNotRevokedError

        dek_store = InMemoryDEKStore()
        crypto_shredder = CryptoShredder(dek_store)
        job = DataErasureJob(
            _FakeConsentChecker(revoked=False),
            crypto_shredder,
            _FakeTombstoneStore(),
            _FakeObjectStore(),
            _FakeCertificateStore(),
        )

        with pytest.raises(ConsentNotRevokedError):
            job.execute(
                tenant_id="tenant-1",
                customer_id="customer-2",
                consent_type="DATA_PROCESSING",
                dek_ids=["dek-3"],
                audio_object_keys=[],
            )
