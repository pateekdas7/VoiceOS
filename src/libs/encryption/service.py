"""EncryptionService — the single public entrypoint services inject (V4 Ch8 §8.7).

Wraps ``EnvelopeEncryption`` (encrypt/decrypt) and ``CryptoShredder``
(erasure) behind the interface named in the architecture doc, timing every
call for the Prometheus histograms in ``metrics.py``.

Architecture: V4 Ch8 §8.7 (``EncryptionService.encrypt/decrypt/crypto_shred``).
"""

from __future__ import annotations

import time

from src.libs.encryption.crypto_shred import CryptoShredder
from src.libs.encryption.envelope import EncryptedPayload, EnvelopeEncryption, KeyNotFoundError
from src.libs.encryption.metrics import record_decrypt, record_encrypt, record_latency_ms

__all__ = ["EncryptedPayload", "EncryptionService", "KeyNotFoundError"]


class EncryptionService:
    """Facade over envelope encryption + crypto-shredding for field-level PII."""

    def __init__(self, envelope: EnvelopeEncryption, crypto_shredder: CryptoShredder) -> None:
        self._envelope = envelope
        self._crypto_shredder = crypto_shredder

    def encrypt(self, plaintext: bytes, tenant_id: str) -> EncryptedPayload:
        start = time.monotonic()
        try:
            payload = self._envelope.encrypt(plaintext, tenant_id)
        except Exception:
            record_encrypt("error")
            raise
        record_encrypt("success")
        record_latency_ms("encrypt", (time.monotonic() - start) * 1000)
        return payload

    def decrypt(self, payload: EncryptedPayload, tenant_id: str) -> bytes:
        start = time.monotonic()
        try:
            plaintext = self._envelope.decrypt(payload, tenant_id)
        except KeyNotFoundError:
            record_decrypt("key_not_found")
            raise
        except Exception:
            record_decrypt("error")
            raise
        record_decrypt("success")
        record_latency_ms("decrypt", (time.monotonic() - start) * 1000)
        return plaintext

    def crypto_shred(self, tenant_id: str, record_id: str, actor_id: str = "system") -> None:
        self._crypto_shredder.shred(tenant_id, record_id, actor_id)
