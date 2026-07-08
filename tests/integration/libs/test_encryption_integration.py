"""Integration test for Sprint-019's Secrets/Encryption/Privacy libraries working together.

No CPU/GPU infrastructure is required (Sprint-019.md Phase 1): KMS is
``FakeKMSClient``, the vault backend is ``FakeVaultClient``, object storage
is ``FakeObjectStore`` — this test proves the *protocol* (SecretsManager →
EnvelopeEncryption → CryptoShredder → DataErasureJob) composes correctly
end-to-end, independent of which real backend Phase 2 wires in.
"""

from __future__ import annotations

import pytest

from src.libs.encryption import (
    CryptoShredder,
    EncryptionService,
    EnvelopeEncryption,
    InMemoryDEKStore,
    KeyNotFoundError,
)
from src.libs.privacy.erasure import ConsentNotRevokedError, DataErasureJob
from src.libs.secrets.manager import SecretsManager
from src.libs.secrets.providers.vault_provider import VaultProvider
from tests.fixtures.fake_kms import FakeKMSClient
from tests.fixtures.fake_object_store import FakeObjectStore
from tests.fixtures.fake_vault import FakeVaultClient


class _ConsentChecker:
    def __init__(self, status: str) -> None:
        self._status = status

    def check_consent(self, tenant_id: str, customer_id: str, consent_type: object) -> object:
        class _Consent:
            status = self._status

        return _Consent()


class _TombstoneStore:
    def tombstone_pii(self, tenant_id: str, customer_id: str) -> None:
        return None


class _CertificateStore:
    def __init__(self) -> None:
        self.saved: list[object] = []

    def save(self, certificate: object) -> None:
        self.saved.append(certificate)


class TestSecretsAndEncryptionComposition:
    def test_secrets_manager_and_envelope_encryption_share_no_state(self) -> None:
        """A DB credential fetched via SecretsManager and a PII field encrypted via
        EnvelopeEncryption are independent concerns that compose without interference."""
        vault_manager = SecretsManager(VaultProvider(FakeVaultClient(seed={"postgres/password": "s3cr3t"})))
        dek_store = InMemoryDEKStore()
        encryption_service = EncryptionService(
            EnvelopeEncryption(FakeKMSClient(), dek_store), CryptoShredder(dek_store)
        )

        db_password = vault_manager.get_secret("postgres/password")
        payload = encryption_service.encrypt(b"Asha Rao", "tenant-a")

        assert db_password == "s3cr3t"
        assert encryption_service.decrypt(payload, "tenant-a") == b"Asha Rao"


class TestErasureWorkflowEndToEnd:
    def test_erasure_workflow_end_to_end(self) -> None:
        """consent revoke -> DataErasureJob -> certificate created, audio deleted, DEK unrecoverable."""
        dek_store = InMemoryDEKStore()
        envelope = EnvelopeEncryption(FakeKMSClient(), dek_store)
        encryption_service = EncryptionService(envelope, CryptoShredder(dek_store))
        object_store = FakeObjectStore()
        certificate_store = _CertificateStore()

        # A customer's phone number is encrypted and their audio recording stored.
        payload = encryption_service.encrypt(b"+919876543210", "tenant-a")
        object_store.put("call-42.wav", b"raw audio bytes")

        job = DataErasureJob(
            _ConsentChecker(status="REVOKED"),
            CryptoShredder(dek_store),
            _TombstoneStore(),
            object_store,
            certificate_store,
        )

        result = job.execute(
            "tenant-a",
            "cust-42",
            "recording",
            dek_ids=(payload.dek_id,),
            audio_object_keys=("call-42.wav",),
        )

        assert result.verified is True
        assert len(certificate_store.saved) == 1
        assert object_store.exists("call-42.wav") is False
        with pytest.raises(KeyNotFoundError):
            encryption_service.decrypt(payload, "tenant-a")

    def test_erasure_blocked_without_consent_revocation(self) -> None:
        dek_store = InMemoryDEKStore()
        job = DataErasureJob(
            _ConsentChecker(status="GRANTED"),
            CryptoShredder(dek_store),
            _TombstoneStore(),
            FakeObjectStore(),
            _CertificateStore(),
        )

        with pytest.raises(ConsentNotRevokedError):
            job.execute("tenant-a", "cust-42", "recording", dek_ids=(), audio_object_keys=())
