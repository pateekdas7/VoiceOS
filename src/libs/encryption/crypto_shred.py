"""CryptoShredder — right-to-erasure via DEK deletion (V4 Ch8 §8.12, V4 Ch9 §9.12).

Deleting a record's wrapped DEK from the DEK store makes its ciphertext
permanently unrecoverable, even in immutable backups where physical
deletion of the ciphertext itself is impractical (V4 Ch9 §9.12) — this is
the mechanism ``DataErasureJob`` (src.libs.privacy.erasure) calls as one
step of the broader right-to-erasure workflow.

Architecture: V4 Ch8 §8.7 (``crypto_shred``), §8.12 ("destroy the
per-tenant/record key so ciphertext is unrecoverable").
"""

from __future__ import annotations

from typing import Any

from src.libs.encryption.dek_store import DEKStoreProtocol
from src.libs.encryption.metrics import record_crypto_shred


class CryptoShredder:
    """Deletes a record's DEK, cryptographically erasing its ciphertext."""

    def __init__(self, dek_store: DEKStoreProtocol, audit_repository: Any | None = None) -> None:
        self._dek_store = dek_store
        self._audit_repository = audit_repository

    def shred(self, tenant_id: str, record_id: str, actor_id: str = "system") -> None:
        """Delete the DEK for ``record_id`` (a ``dek_id`` from an ``EncryptedPayload``).

        Idempotent — shredding an already-shredded (or never-existing)
        record_id is a no-op, not an error, so retrying a failed erasure
        workflow (V4 Ch9 §9.16) is always safe.
        """
        self._dek_store.delete(tenant_id, record_id)
        record_crypto_shred()
        if self._audit_repository is not None:
            self._audit_repository.append(
                tenant_id,
                actor_id=actor_id,
                action="crypto_shred",
                resource_type="dek",
                resource_id=record_id,
                outcome="success",
            )
