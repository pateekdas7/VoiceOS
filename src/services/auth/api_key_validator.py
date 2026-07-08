"""APIKeyValidator — validates API keys and resolves tenant_id + scopes
(V4 Ch5 §5.6 "API Keys").

Keys are never stored or compared in plaintext — only their SHA-256 digest
is held in the key store, so a leaked store dump does not expose usable
credentials.

Architecture: V4 Ch5 (Authentication — API Keys).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from .models import AuthContext, AuthenticationError, AuthMethod

if TYPE_CHECKING:
    from src.libs.contracts.models.integration import APIKeyRecord as PersistedAPIKeyRecord


@dataclass(frozen=True)
class APIKeyRecord:
    """The tenant/role/scope grant a validated API key resolves to."""

    tenant_id: str
    role: str = ""
    scopes: tuple[str, ...] = ()
    api_key_id: str = ""
    """Populated only when resolved via the Postgres-backed repository (Sprint-025 Part-3)."""


class APIKeyRepositoryPort(Protocol):
    def find_by_hash(self, key_hash: str) -> PersistedAPIKeyRecord | None: ...


class APIKeyValidator:
    """Validates ``X-API-Key`` header values against a hashed key store.

    Args:
        key_store: Injected backing store (an in-memory dict, sufficient
            for tests and Phase 1). Keyed by the SHA-256 hex digest of the
            raw API key.
        repository: Optional Postgres-backed fallback (Sprint-025,
            ``src.libs.repositories.integration.APIKeyRepository``) --
            consulted only when ``key_store`` misses, and only if the
            persisted record isn't revoked. ``None`` (default) preserves
            every pre-Sprint-025 in-memory-only caller unchanged.
    """

    def __init__(
        self,
        key_store: dict[str, APIKeyRecord] | None = None,
        repository: APIKeyRepositoryPort | None = None,
    ) -> None:
        self._key_store: dict[str, APIKeyRecord] = key_store if key_store is not None else {}
        self._repository = repository

    @staticmethod
    def hash_key(raw_key: str) -> str:
        """The SHA-256 hex digest used as this key's lookup identity."""
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def register_key(
        self,
        raw_key: str,
        tenant_id: str,
        role: str = "",
        scopes: tuple[str, ...] = (),
    ) -> None:
        """Register a new API key (test/seeding helper — production issuance
        is a future sprint's concern)."""
        self._key_store[self.hash_key(raw_key)] = APIKeyRecord(tenant_id=tenant_id, role=role, scopes=scopes)

    def validate(self, raw_key: str | None) -> AuthContext:
        """Resolve a raw API key to an :class:`AuthContext`.

        Raises:
            AuthenticationError: When the header is missing or the key does
                not resolve to a registered record.
        """
        if not raw_key:
            raise AuthenticationError("Missing X-API-Key header")

        digest = self.hash_key(raw_key)
        record = self._key_store.get(digest)
        if record is None and self._repository is not None:
            persisted = self._repository.find_by_hash(digest)
            if persisted is not None and not persisted.is_revoked:
                if persisted.expires_at is not None and persisted.expires_at < datetime.now(UTC):
                    raise AuthenticationError("API key has expired")
                record = APIKeyRecord(
                    tenant_id=str(persisted.tenant_id),
                    role=persisted.role,
                    scopes=persisted.scopes,
                    api_key_id=persisted.api_key_id,
                )
        if record is None:
            raise AuthenticationError("Invalid API key")

        return AuthContext(
            subject=f"api-key:{digest[:8]}",
            tenant_id=record.tenant_id,
            role=record.role,
            scopes=record.scopes,
            auth_method=AuthMethod.API_KEY,
            api_key_id=record.api_key_id,
        )
