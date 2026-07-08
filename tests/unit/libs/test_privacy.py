"""Unit tests for src/libs/privacy/ (V4 Ch9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.libs.encryption import CryptoShredder, InMemoryDEKStore
from src.libs.privacy.engine import PrivacyEngine
from src.libs.privacy.erasure import ConsentNotRevokedError, DataErasureJob
from src.libs.privacy.minimizer import DataMinimizer
from src.libs.privacy.purpose_registry import DataClass, Purpose, PurposeRegistry
from src.libs.privacy.retention import RetentionScheduler
from tests.fixtures.fake_object_store import FakeObjectStore


class TestDataMinimizer:
    def test_data_minimizer_strips_unconsented_fields(self) -> None:
        minimizer = DataMinimizer()
        record = {"name": "Asha Rao", "phone": "+919876543210"}

        result = minimizer.minimize(record, allowed_fields=frozenset({"name"}))

        assert result == {"name": "Asha Rao"}
        assert "phone" not in result


class TestPurposeRegistry:
    def test_collections_purpose_allowed_for_phone(self) -> None:
        registry = PurposeRegistry()

        assert registry.is_allowed(DataClass.PHONE, Purpose.COLLECTIONS) is True

    def test_marketing_purpose_denied_for_financial_data(self) -> None:
        registry = PurposeRegistry()

        assert registry.is_allowed(DataClass.FINANCIAL, Purpose.MARKETING) is False


class TestPrivacyEngine:
    def test_check_purpose_denied_without_consent(self) -> None:
        engine = PrivacyEngine(PurposeRegistry())

        decision = engine.check_purpose(DataClass.PHONE, Purpose.COLLECTIONS, consent_granted=False)

        assert decision.allowed is False

    def test_check_purpose_allowed_with_consent_and_valid_purpose(self) -> None:
        engine = PrivacyEngine(PurposeRegistry())

        decision = engine.check_purpose(DataClass.PHONE, Purpose.COLLECTIONS, consent_granted=True)

        assert decision.allowed is True

    def test_check_purpose_denied_when_purpose_not_registered(self) -> None:
        engine = PrivacyEngine(PurposeRegistry())

        decision = engine.check_purpose(DataClass.FINANCIAL, Purpose.MARKETING, consent_granted=True)

        assert decision.allowed is False


class TestRetentionScheduler:
    def test_flags_recording_past_90_days(self) -> None:
        scheduler = RetentionScheduler()
        now = datetime(2026, 7, 5, tzinfo=UTC)
        old_created_at = now - timedelta(days=91)

        assert scheduler.is_expired(DataClass.VOICE_RECORDING, old_created_at, now=now) is True

    def test_does_not_flag_recent_recording(self) -> None:
        scheduler = RetentionScheduler()
        now = datetime(2026, 7, 5, tzinfo=UTC)
        recent = now - timedelta(days=10)

        assert scheduler.is_expired(DataClass.VOICE_RECORDING, recent, now=now) is False

    def test_legal_hold_overrides_expiry(self) -> None:
        scheduler = RetentionScheduler()
        now = datetime(2026, 7, 5, tzinfo=UTC)
        old_created_at = now - timedelta(days=365)

        assert scheduler.is_expired(DataClass.VOICE_RECORDING, old_created_at, legal_hold=True, now=now) is False


class _FakeConsentChecker:
    def __init__(self, status: str | None) -> None:
        self._status = status

    def check_consent(self, tenant_id: str, customer_id: str, consent_type: object) -> object:
        if self._status is None:
            return None

        class _Consent:
            status = self._status

        return _Consent()


class _FakeTombstoneStore:
    def __init__(self) -> None:
        self.tombstoned: list[tuple[str, str]] = []

    def tombstone_pii(self, tenant_id: str, customer_id: str) -> None:
        self.tombstoned.append((tenant_id, customer_id))


class _FakeCertificateStore:
    def __init__(self) -> None:
        self.saved: list[object] = []

    def save(self, certificate: object) -> None:
        self.saved.append(certificate)


class TestDataErasureJob:
    def _job(
        self, consent_status: str | None
    ) -> tuple[DataErasureJob, _FakeTombstoneStore, FakeObjectStore, _FakeCertificateStore]:
        dek_store = InMemoryDEKStore()
        crypto_shredder = CryptoShredder(dek_store)
        tombstone_store = _FakeTombstoneStore()
        object_store = FakeObjectStore()
        certificate_store = _FakeCertificateStore()
        job = DataErasureJob(
            _FakeConsentChecker(consent_status),
            crypto_shredder,
            tombstone_store,
            object_store,
            certificate_store,
        )
        return job, tombstone_store, object_store, certificate_store

    def test_erasure_raises_when_consent_not_revoked(self) -> None:
        job, _, _, _ = self._job(consent_status="GRANTED")

        with pytest.raises(ConsentNotRevokedError):
            job.execute("tenant-a", "cust-1", "recording", dek_ids=(), audio_object_keys=())

    def test_erasure_raises_when_no_consent_record(self) -> None:
        job, _, _, _ = self._job(consent_status=None)

        with pytest.raises(ConsentNotRevokedError):
            job.execute("tenant-a", "cust-1", "recording", dek_ids=(), audio_object_keys=())

    def test_erasure_deletes_audio_and_writes_certificate(self) -> None:
        job, tombstone_store, object_store, certificate_store = self._job(consent_status="REVOKED")
        object_store.put("call-1.wav", b"audio bytes")

        result = job.execute("tenant-a", "cust-1", "recording", dek_ids=("dek-1",), audio_object_keys=("call-1.wav",))

        assert result.verified is True
        assert object_store.exists("call-1.wav") is False
        assert tombstone_store.tombstoned == [("tenant-a", "cust-1")]
        assert len(certificate_store.saved) == 1

    def test_erasure_publishes_completed_event(self) -> None:
        published: list[tuple[str, dict[str, object]]] = []

        class _Publisher:
            def publish(self, event_type: str, payload: dict[str, object]) -> None:
                published.append((event_type, payload))

        dek_store = InMemoryDEKStore()
        crypto_shredder = CryptoShredder(dek_store)
        job = DataErasureJob(
            _FakeConsentChecker("REVOKED"),
            crypto_shredder,
            _FakeTombstoneStore(),
            FakeObjectStore(),
            _FakeCertificateStore(),
            event_publisher=_Publisher(),
        )

        job.execute("tenant-a", "cust-1", "recording", dek_ids=(), audio_object_keys=())

        assert published[0][0] == "DataErasureCompleted"
        assert published[0][1]["customer_id"] == "cust-1"
