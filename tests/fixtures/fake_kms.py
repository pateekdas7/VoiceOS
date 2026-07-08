"""In-memory KMS test double for envelope-encryption unit tests.

Simulates a KMS key-encryption-key (KEK) hierarchy without a real KMS/Vault
Transit backend: ``generate_data_key`` returns a fresh random 256-bit DEK
plus a "wrapped" form (XOR'd against a per-KEK pad — reversible only via
``decrypt_data_key``, never the real AES-KW/RSA-OAEP a production KMS would
use, since this fake only needs to prove the envelope-encryption *protocol*,
not real key-wrapping cryptography).

Architecture: V4 Ch8 (Encryption Architecture); V6 Ch9 (Testing Standards).
"""

from __future__ import annotations

import os


class FakeKMSClient:
    """Deterministic-enough fake KEK store — one random pad per kek_id."""

    def __init__(self) -> None:
        self._keks: dict[str, bytes] = {}
        self.generate_calls: list[str] = []
        self.decrypt_calls: list[str] = []

    def _kek(self, kek_id: str) -> bytes:
        if kek_id not in self._keks:
            self._keks[kek_id] = os.urandom(32)
        return self._keks[kek_id]

    def generate_data_key(self, kek_id: str) -> tuple[bytes, bytes]:
        """Return (plaintext_dek, wrapped_dek)."""
        self.generate_calls.append(kek_id)
        plaintext_dek = os.urandom(32)
        wrapped_dek = _xor(plaintext_dek, self._kek(kek_id))
        return plaintext_dek, wrapped_dek

    def decrypt_data_key(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        self.decrypt_calls.append(kek_id)
        return _xor(wrapped_dek, self._kek(kek_id))

    def destroy_kek(self, kek_id: str) -> None:
        """Tenant-level crypto-shred: destroy the KEK so all its wrapped DEKs die with it."""
        self._keks.pop(kek_id, None)

    def ensure_kek(self, kek_id: str) -> None:
        self._kek(kek_id)


def _xor(data: bytes, pad: bytes) -> bytes:
    return bytes(b ^ pad[i % len(pad)] for i, b in enumerate(data))
