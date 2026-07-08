"""Unit tests for src/libs/encryption/ (V4 Ch8)."""

from __future__ import annotations

import ssl

import pytest
from cryptography.exceptions import InvalidTag

from src.libs.encryption.aes_gcm import AESGCMEncryptor
from src.libs.encryption.crypto_shred import CryptoShredder
from src.libs.encryption.dek_store import InMemoryDEKStore
from src.libs.encryption.envelope import EncryptedPayload, EnvelopeEncryption, KeyNotFoundError
from src.libs.encryption.service import EncryptionService
from src.libs.encryption.tls_config import TLSConfig
from tests.fixtures.fake_kms import FakeKMSClient


class _Recorder:
    """Minimal AuditRepository.append() double — records calls, no assertions on shape."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def append(self, tenant_id: str, **kwargs: object) -> None:
        self.calls.append({"tenant_id": tenant_id, **kwargs})


@pytest.fixture
def encryption_service() -> tuple[EncryptionService, InMemoryDEKStore]:
    dek_store = InMemoryDEKStore()
    envelope = EnvelopeEncryption(FakeKMSClient(), dek_store)
    shredder = CryptoShredder(dek_store)
    return EncryptionService(envelope, shredder), dek_store


class TestAESGCMEncryptor:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        aes = AESGCMEncryptor()
        key = aes.generate_key()
        ciphertext, iv, tag = aes.encrypt(key, b"hello world")

        assert aes.decrypt(key, ciphertext, iv, tag) == b"hello world"

    def test_wrong_key_fails_authentication(self) -> None:
        aes = AESGCMEncryptor()
        key = aes.generate_key()
        other_key = aes.generate_key()
        ciphertext, iv, tag = aes.encrypt(key, b"hello world")

        with pytest.raises(InvalidTag):
            aes.decrypt(other_key, ciphertext, iv, tag)


class TestEnvelopeEncryption:
    def test_envelope_encrypt_decrypt_roundtrip(
        self, encryption_service: tuple[EncryptionService, InMemoryDEKStore]
    ) -> None:
        service, _ = encryption_service
        payload = service.encrypt(b"sensitive PII", "tenant-a")

        assert service.decrypt(payload, "tenant-a") == b"sensitive PII"

    def test_different_tenants_get_different_keks(self) -> None:
        dek_store = InMemoryDEKStore()
        kms = FakeKMSClient()
        envelope = EnvelopeEncryption(kms, dek_store)

        envelope.encrypt(b"data", "tenant-a")
        envelope.encrypt(b"data", "tenant-b")

        assert kms.generate_calls == ["tenant-tenant-a", "tenant-tenant-b"]

    def test_decrypt_unknown_dek_id_raises_key_not_found(
        self, encryption_service: tuple[EncryptionService, InMemoryDEKStore]
    ) -> None:
        service, _ = encryption_service
        bogus = EncryptedPayload(ciphertext=b"x", dek_id="does-not-exist", iv=b"0" * 12, tag=b"0" * 16)

        with pytest.raises(KeyNotFoundError):
            service.decrypt(bogus, "tenant-a")

    def test_payload_to_bytes_from_bytes_roundtrip(self) -> None:
        original = EncryptedPayload(ciphertext=b"abc123", dek_id="a" * 36, iv=b"1" * 12, tag=b"2" * 16)

        restored = EncryptedPayload.from_bytes(original.to_bytes())

        assert restored == original


class TestCryptoShredder:
    def test_crypto_shred_prevents_decrypt(
        self, encryption_service: tuple[EncryptionService, InMemoryDEKStore]
    ) -> None:
        service, _ = encryption_service
        payload = service.encrypt(b"erase me", "tenant-a")

        service.crypto_shred("tenant-a", payload.dek_id)

        with pytest.raises(KeyNotFoundError):
            service.decrypt(payload, "tenant-a")

    def test_shred_is_idempotent(self) -> None:
        dek_store = InMemoryDEKStore()
        shredder = CryptoShredder(dek_store)

        shredder.shred("tenant-a", "never-existed")
        shredder.shred("tenant-a", "never-existed")  # no error

    def test_shred_emits_audit_event(self) -> None:
        dek_store = InMemoryDEKStore()
        recorder = _Recorder()
        shredder = CryptoShredder(dek_store, audit_repository=recorder)

        shredder.shred("tenant-a", "dek-123", actor_id="dpo-1")

        assert recorder.calls[0]["action"] == "crypto_shred"
        assert recorder.calls[0]["resource_id"] == "dek-123"


class TestTLSConfig:
    def test_build_ssl_context_enforces_tls13(self) -> None:
        # Isolation: this only asserts the version-pinning logic, not a real
        # handshake (no live listener exists yet — TT-006/CPU_NODE_STATE.md §9.1).
        config = TLSConfig(cert_path="unused.crt", key_path="unused.key")

        assert config.minimum_version == ssl.TLSVersion.TLSv1_3

    def test_requires_ca_path_when_client_cert_required(self) -> None:
        config = TLSConfig(cert_path="a", key_path="b", require_client_cert=True, ca_path=None)

        with pytest.raises(ValueError, match="ca_path"):
            config.build_ssl_context()
