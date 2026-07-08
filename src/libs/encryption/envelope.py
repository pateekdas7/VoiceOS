"""EnvelopeEncryption — DEK-per-record encrypted by a tenant KEK (V4 Ch8 §8.9).

Architecture: V4 Ch8 §8.7 (Public Interfaces), §8.9 (data flow), §8.11
(sequence diagram), §8.12 ("envelope encryption: DEK encrypts data; KEK...
wraps the DEK. Rotation rewraps DEKs under a new KEK — fast, no bulk
re-encryption").
"""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from src.libs.encryption.aes_gcm import AESGCMEncryptor
from src.libs.encryption.dek_store import DEKStoreProtocol
from src.libs.encryption.kms_client import KMSClientProtocol


class KeyNotFoundError(Exception):
    """Raised when a payload's DEK has been crypto-shredded (or never existed)."""


class EncryptedPayload(BaseModel):
    """Ciphertext + everything needed to decrypt it, except the KEK itself.

    ``dek_id`` is the lookup key into the DEK store — callers persist it
    alongside ``ciphertext``/``iv``/``tag`` (e.g. as a sibling column) so a
    later ``CryptoShredder.shred(tenant_id, dek_id)`` can make this payload
    permanently unrecoverable.
    """

    model_config = ConfigDict(frozen=True)

    ciphertext: bytes
    dek_id: str
    iv: bytes
    tag: bytes

    def to_bytes(self) -> bytes:
        """Serialize to a single fixed-layout blob for one-column Postgres storage.

        Layout: ``dek_id_len(1B) | dek_id | iv(12B) | tag(16B) | ciphertext``.
        ``dek_id`` is always a UUID4 string (36 bytes) in practice, but the
        length prefix keeps this forward-compatible with any ASCII id.
        """
        dek_id_bytes = self.dek_id.encode("ascii")
        return len(dek_id_bytes).to_bytes(1, "big") + dek_id_bytes + self.iv + self.tag + self.ciphertext

    @classmethod
    def from_bytes(cls, data: bytes) -> EncryptedPayload:
        """Inverse of ``to_bytes()``."""
        dek_id_len = data[0]
        offset = 1
        dek_id = data[offset : offset + dek_id_len].decode("ascii")
        offset += dek_id_len
        iv = data[offset : offset + 12]
        offset += 12
        tag = data[offset : offset + 16]
        offset += 16
        ciphertext = data[offset:]
        return cls(ciphertext=ciphertext, dek_id=dek_id, iv=iv, tag=tag)


class EnvelopeEncryption:
    """Encrypts/decrypts field-level data using a fresh DEK per call, wrapped by a per-tenant KEK."""

    def __init__(self, kms_client: KMSClientProtocol, dek_store: DEKStoreProtocol) -> None:
        self._kms = kms_client
        self._dek_store = dek_store
        self._aes = AESGCMEncryptor()

    def encrypt(self, plaintext: bytes, tenant_id: str) -> EncryptedPayload:
        """Encrypt ``plaintext`` under a fresh DEK, itself wrapped by ``tenant_id``'s KEK."""
        kek_id = self._kek_id_for(tenant_id)
        plaintext_dek, wrapped_dek = self._kms.generate_data_key(kek_id)
        ciphertext, iv, tag = self._aes.encrypt(plaintext_dek, plaintext)

        dek_id = str(uuid4())
        self._dek_store.put(tenant_id, dek_id, wrapped_dek, kek_id)

        return EncryptedPayload(ciphertext=ciphertext, dek_id=dek_id, iv=iv, tag=tag)

    def decrypt(self, payload: EncryptedPayload, tenant_id: str) -> bytes:
        """Decrypt ``payload`` — fetches its DEK from KMS via the stored wrapped form.

        Raises:
            KeyNotFoundError: the DEK was crypto-shredded (or never existed
                for this tenant/dek_id pair).
        """
        record = self._dek_store.get(tenant_id, payload.dek_id)
        if record is None:
            raise KeyNotFoundError(f"no DEK for tenant={tenant_id} dek_id={payload.dek_id} (shredded or unknown)")
        wrapped_dek, kek_id = record

        plaintext_dek = self._kms.decrypt_data_key(kek_id, wrapped_dek)
        return self._aes.decrypt(plaintext_dek, payload.ciphertext, payload.iv, payload.tag)

    @staticmethod
    def _kek_id_for(tenant_id: str) -> str:
        """One KEK per tenant (V4 Ch8 §8.3 "Key separation"): ``tenant-<tenant_id>``.

        No ``/`` in the id — Vault's Transit engine routes ``transit/keys/:name``
        as a single path segment, and a slash-containing name breaks that
        routing ("unsupported path"), discovered running this against a
        real Vault Transit engine in Sprint-019 Phase 2.
        """
        return f"tenant-{tenant_id}"
