"""Encryption architecture — AES-256-GCM, envelope encryption, crypto-shredding, TLS 1.3 (V4 Ch8).

Architecture: V4 Ch8 (Encryption Architecture).
"""

from __future__ import annotations

from src.libs.encryption.aes_gcm import AESGCMEncryptor
from src.libs.encryption.crypto_shred import CryptoShredder
from src.libs.encryption.dek_store import DEKStoreProtocol, InMemoryDEKStore, PostgresDEKStore
from src.libs.encryption.envelope import EncryptedPayload, EnvelopeEncryption, KeyNotFoundError
from src.libs.encryption.kms_client import AWSKMSAdapter, KMSClientProtocol, VaultTransitKMSClient
from src.libs.encryption.service import EncryptionService
from src.libs.encryption.tls_config import TLSConfig

__all__ = [
    "AESGCMEncryptor",
    "AWSKMSAdapter",
    "CryptoShredder",
    "DEKStoreProtocol",
    "EncryptedPayload",
    "EncryptionService",
    "EnvelopeEncryption",
    "InMemoryDEKStore",
    "KMSClientProtocol",
    "KeyNotFoundError",
    "PostgresDEKStore",
    "TLSConfig",
    "VaultTransitKMSClient",
]
