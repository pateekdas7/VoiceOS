"""PIITokenizer — reversible tokenization with access-controlled lookup.

Replaces sensitive values with opaque tokens (V4 Ch10 §10.12 "Tokenization:
... the raw value lives in a token vault, detokenizable only under authz
(Ch 6) + audit (Ch 11)"). Reversal requires the AUDITOR role — enforced
structurally (a ``role`` attribute) rather than by importing
``src.services.auth``/``src.services.authz`` directly, since ``src/libs/``
must not depend on ``src/services/`` (V6 Ch2 layering; mirrors the
``PolicyLookupPort`` structural-typing precedent in
``src.engines.dialogue_policy``).

Storage is pluggable and additive, matching every Sprint-019 backend
pattern (``SecretsManager``/``EncryptionService``): an in-memory dict always
backs lookups (works with zero infra in Phase 1); an optional Redis client
adds a session-TTL cache (Sprint-013 ``TTLGuard`` discipline); an optional
Postgres-backed repository adds persistent storage (Phase 2).

Architecture: V4 Ch10 (PII Protection) §10.7, §10.9, §10.11, §10.12.
"""

from __future__ import annotations

import secrets
from typing import Any, Protocol, runtime_checkable

from src.libs.redis_client.ttl_guard import TTLGuard

from .entities import PIIEntity

_SESSION_TTL_SECONDS = 4 * 60 * 60
"""Session-scoped TTL for the Redis-backed transient token cache (V4 Ch10 §10.9)."""

_AUDITOR_ROLE = "AUDITOR"


class PIITokenAccessDeniedError(Exception):
    """Raised when a non-AUDITOR actor attempts :meth:`PIITokenizer.detokenize`."""


class PIITokenNotFoundError(KeyError):
    """Raised when a token has no known mapping (expired, never existed, or wrong tenant)."""


@runtime_checkable
class _Authorized(Protocol):
    """Structural stand-in for ``AuthContext`` — a ``role`` string attribute."""

    role: str


class PIITokenizer:
    """Reversible tokenization for storage/reference (V4 Ch10 §10.7)."""

    def __init__(
        self,
        redis: Any | None = None,
        token_repository: Any | None = None,
    ) -> None:
        """
        Args:
            redis: Optional Redis-compatible client for the transient
                (session-TTL) token cache.
            token_repository: Optional Postgres-backed repository exposing
                ``put(token, tenant_id, entity_type, value) -> None`` and
                ``get(token, tenant_id) -> str | None`` for persistent storage.
        """
        self._store: dict[str, tuple[str, PIIEntity, str]] = {}
        self._redis = redis
        self._ttl_guard = TTLGuard(redis) if redis is not None else None
        self._token_repository = token_repository

    def tokenize(self, value: str, entity_type: PIIEntity, tenant_id: str = "") -> str:
        """Replace ``value`` with an opaque token, e.g. ``PHONE_7f3a2b``."""
        token = f"{entity_type.value}_{secrets.token_hex(3)}"
        self._store[token] = (tenant_id, entity_type, value)

        if self._ttl_guard is not None:
            self._ttl_guard.set(f"voiceos:pii_token:{token}", value, ex=_SESSION_TTL_SECONDS)

        if self._token_repository is not None:
            self._token_repository.put(token, tenant_id, entity_type.value, value)

        return token

    def detokenize(self, token: str, actor: _Authorized, tenant_id: str = "") -> str:
        """Reverse a token back to its raw value. Requires the AUDITOR role.

        Every successful and denied detokenization should be recorded by the
        caller via ``AuditLogger`` (V4 Ch10 §10.18 "every raw-PII access is
        audited") — this method itself only enforces the authz gate and
        performs the lookup, so it stays a plain library primitive.

        Raises:
            PIITokenAccessDeniedError: ``actor.role`` is not ``AUDITOR``.
            PIITokenNotFoundError: no mapping exists for ``token``.
        """
        if actor.role != _AUDITOR_ROLE:
            raise PIITokenAccessDeniedError(f"detokenize requires AUDITOR role, got {actor.role!r}")

        cached = self._store.get(token)
        if cached is not None:
            return cached[2]

        if self._redis is not None:
            raw = self._redis.get(f"voiceos:pii_token:{token}")
            if raw is not None:
                return raw.decode() if isinstance(raw, bytes) else str(raw)

        if self._token_repository is not None:
            value = self._token_repository.get(token, tenant_id)
            if value is not None:
                return str(value)

        raise PIITokenNotFoundError(token)
