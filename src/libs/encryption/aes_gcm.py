"""AES-256-GCM field-level encryption primitive (V4 Ch8 §8.12 "At rest").

Architecture: V4 Ch8 §8.12 (AES-256-GCM authenticated encryption for all
sensitive stores), §8.14 (< 1ms typical overhead via AES-NI).
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_SIZE_BYTES = 32  # 256-bit
NONCE_SIZE_BYTES = 12  # 96-bit, NIST-recommended for GCM
TAG_SIZE_BYTES = 16  # 128-bit authentication tag


class AESGCMEncryptor:
    """Stateless AES-256-GCM encrypt/decrypt — one fresh random nonce per call."""

    @staticmethod
    def generate_key() -> bytes:
        """Generate a fresh random 256-bit key (a plaintext DEK)."""
        key: bytes = AESGCM.generate_key(bit_length=256)
        return key

    def encrypt(self, key: bytes, plaintext: bytes, associated_data: bytes | None = None) -> tuple[bytes, bytes, bytes]:
        """Encrypt ``plaintext`` under ``key``.

        Returns:
            ``(ciphertext, nonce, tag)`` — the tag is returned separately
            (split from AESGCM's combined output) to match the
            ``EncryptedPayload(ciphertext, encrypted_dek, iv, tag)`` shape
            specified in Sprint-019.md.
        """
        nonce = os.urandom(NONCE_SIZE_BYTES)
        combined = AESGCM(key).encrypt(nonce, plaintext, associated_data)
        ciphertext, tag = combined[:-TAG_SIZE_BYTES], combined[-TAG_SIZE_BYTES:]
        return ciphertext, nonce, tag

    def decrypt(
        self, key: bytes, ciphertext: bytes, nonce: bytes, tag: bytes, associated_data: bytes | None = None
    ) -> bytes:
        """Decrypt ``ciphertext``+``tag`` under ``key``/``nonce``.

        Raises:
            cryptography.exceptions.InvalidTag: authentication failed (wrong
                key, tampered ciphertext, or mismatched associated_data).
        """
        plaintext: bytes = AESGCM(key).decrypt(nonce, ciphertext + tag, associated_data)
        return plaintext
