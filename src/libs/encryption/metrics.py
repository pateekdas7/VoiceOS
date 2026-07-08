"""Prometheus metrics for the encryption layer (V4 Ch8 §8.17 Observability).

Architecture: V4 Ch8 §8.17 ("Encryption coverage... key ages/rotation
status, KMS health, decrypt authz denials").
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

ENCRYPT_OPERATIONS_TOTAL: Counter = Counter(
    "voiceos_encrypt_operations_total",
    "Total EncryptionService.encrypt() calls, by outcome.",
    labelnames=["outcome"],
)
"""Counter: 'success' | 'error', per encrypt() call."""

DECRYPT_OPERATIONS_TOTAL: Counter = Counter(
    "voiceos_decrypt_operations_total",
    "Total EncryptionService.decrypt() calls, by outcome.",
    labelnames=["outcome"],
)
"""Counter: 'success' | 'key_not_found' | 'error', per decrypt() call."""

CRYPTO_SHRED_TOTAL: Counter = Counter(
    "voiceos_crypto_shred_total",
    "Total CryptoShredder.shred() calls (right-to-erasure DEK deletions).",
)
"""Counter: one increment per shredded record."""

ENCRYPTION_LATENCY_MS: Histogram = Histogram(
    "voiceos_encryption_latency_ms",
    "encrypt()/decrypt() wall-clock duration in milliseconds, by operation.",
    labelnames=["operation"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 25, 50),
)
"""Histogram: target < 1ms typical per V4 Ch8 §8.14 (AES-NI, not counting KMS round trip)."""


def record_encrypt(outcome: str) -> None:
    ENCRYPT_OPERATIONS_TOTAL.labels(outcome=outcome).inc()


def record_decrypt(outcome: str) -> None:
    DECRYPT_OPERATIONS_TOTAL.labels(outcome=outcome).inc()


def record_crypto_shred() -> None:
    CRYPTO_SHRED_TOTAL.inc()


def record_latency_ms(operation: str, duration_ms: float) -> None:
    ENCRYPTION_LATENCY_MS.labels(operation=operation).observe(duration_ms)
