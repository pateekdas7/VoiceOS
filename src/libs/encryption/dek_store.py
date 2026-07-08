"""DEK metadata store — tracks each record's wrapped DEK + owning KEK.

A DEK's *plaintext* form never persists anywhere (generated fresh on
encrypt, reconstructed via ``KMSClientProtocol.decrypt_data_key`` on
decrypt) — only its *wrapped* (KMS-encrypted) form is stored here, keyed by
``dek_id``. Crypto-shredding (V4 Ch8 §8.12) a single record means deleting
its row: with the wrapped DEK gone, the corresponding ciphertext is
permanently unrecoverable even though the KEK itself remains intact for
every other record.

Architecture: V4 Ch8 §8.9 (envelope encryption data flow), §8.12 (crypto-shredding).
"""

from __future__ import annotations

from typing import Any, Protocol


class DEKStoreProtocol(Protocol):
    """Storage for wrapped per-record DEKs, scoped by tenant."""

    def put(self, tenant_id: str, dek_id: str, wrapped_dek: bytes, kek_id: str) -> None: ...

    def get(self, tenant_id: str, dek_id: str) -> tuple[bytes, str] | None:
        """Return ``(wrapped_dek, kek_id)``, or ``None`` if absent/shredded."""
        ...

    def delete(self, tenant_id: str, dek_id: str) -> None: ...


class InMemoryDEKStore:
    """Phase 1 / unit-test DEK store — process-local dict, no persistence."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], tuple[bytes, str]] = {}

    def put(self, tenant_id: str, dek_id: str, wrapped_dek: bytes, kek_id: str) -> None:
        self._store[(tenant_id, dek_id)] = (wrapped_dek, kek_id)

    def get(self, tenant_id: str, dek_id: str) -> tuple[bytes, str] | None:
        return self._store.get((tenant_id, dek_id))

    def delete(self, tenant_id: str, dek_id: str) -> None:
        self._store.pop((tenant_id, dek_id), None)


_TABLE = "data_encryption_keys"


class PostgresDEKStore:
    """Real DEK store — the ``data_encryption_keys`` Postgres table (migration 0016).

    Takes a raw psycopg2-compatible connection directly (not a
    ``BaseRepository`` subclass) — this library must not depend on
    ``src.libs.repositories`` (Tier 1 must not depend on higher tiers;
    BUILD_ORDER.md), mirroring how ``BaseRepository`` itself only requires
    ``cursor()``/``commit()``.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def put(self, tenant_id: str, dek_id: str, wrapped_dek: bytes, kek_id: str) -> None:
        cur = self._conn.cursor()
        cur.execute(
            f"""
            INSERT INTO {_TABLE} (dek_id, tenant_id, wrapped_dek, kek_id, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            (dek_id, tenant_id, wrapped_dek, kek_id),
        )
        self._conn.commit()

    def get(self, tenant_id: str, dek_id: str) -> tuple[bytes, str] | None:
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT wrapped_dek, kek_id FROM {_TABLE} WHERE tenant_id = %s AND dek_id = %s",
            (tenant_id, dek_id),
        )
        row = cur.fetchone()
        return (bytes(row[0]), row[1]) if row is not None else None

    def delete(self, tenant_id: str, dek_id: str) -> None:
        cur = self._conn.cursor()
        cur.execute(f"DELETE FROM {_TABLE} WHERE tenant_id = %s AND dek_id = %s", (tenant_id, dek_id))
        self._conn.commit()
