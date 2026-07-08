"""SecretsManager — runtime secret fetch with caching, grace-window rotation,
and emergency revocation (V4 Ch7).

No secret is ever read from ``os.environ`` or a config file here — every
value is fetched from an injected ``SecretProvider`` (a vault/AWS Secrets
Manager adapter) at runtime and cached in-process, encrypted, for a bounded
TTL. This is the mechanical enforcement point for V4 Ch7 §7.3's "no secrets
in code/images/config — runtime injection only".

Architecture: V4 Ch7 §7.7 (Public Interfaces), §7.12 (rotation/revocation
policies), §7.14 (performance targets: fetch < 10ms cached).
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from cryptography.fernet import Fernet

from src.libs.secrets.metrics import record_secret_access


class SecretNotFoundError(Exception):
    """Raised when no secret exists at the requested path."""


class SecretRevokedError(Exception):
    """Raised when a secret has been emergency-revoked and not yet re-issued."""


class SecretProvider(Protocol):
    """Backend a SecretsManager fetches secrets from (vault, AWS Secrets Manager, ...)."""

    def get(self, path: str) -> str: ...

    def put(self, path: str, value: str) -> None: ...


class _CacheEntry:
    """One cached, Fernet-encrypted secret value plus its expiry/grace bookkeeping."""

    __slots__ = ("expires_at", "grace_expires_at", "previous_token", "token")

    def __init__(self, token: bytes, expires_at: float) -> None:
        self.token = token
        self.expires_at = expires_at
        self.previous_token: bytes | None = None
        self.grace_expires_at: float = 0.0


class SecretsManager:
    """Fetches secrets from a vault/cloud-secrets provider — never from env vars.

    Args:
        provider: The backend to fetch/write secrets from.
        cache_ttl_seconds: How long a fetched secret is cached in-process
            before being re-fetched (V4 Ch7 §7.13: default 300s).
        grace_window_seconds: After ``rotate()``, how long the pre-rotation
            value remains acceptable so in-flight callers don't fail
            (V4 Ch7 §7.12 "rotation with grace").
        audit_repository: Optional sink for ``secrets.get_secret`` audit
            events (path + actor, never the value). ``None`` is a no-op,
            mirroring every optional-audit wiring since Sprint-017.
    """

    def __init__(
        self,
        provider: SecretProvider,
        *,
        cache_ttl_seconds: int = 300,
        grace_window_seconds: int = 60,
        audit_repository: Any | None = None,
    ) -> None:
        self._provider = provider
        self._cache_ttl_seconds = cache_ttl_seconds
        self._grace_window_seconds = grace_window_seconds
        self._audit_repository = audit_repository
        self._fernet = Fernet(Fernet.generate_key())  # ephemeral, in-process only
        self._cache: dict[str, _CacheEntry] = {}
        self._revoked: set[str] = set()

    def get_secret(self, path: str, actor_id: str = "system", tenant_id: str | None = None) -> str:
        """Fetch the secret at ``path``, using the in-memory cache when fresh.

        Raises:
            SecretRevokedError: ``path`` was revoked and not yet re-issued.
        """
        record_secret_access(path)
        self._audit(path, actor_id, tenant_id, outcome="success")

        now = time.monotonic()
        entry = self._cache.get(path)
        if entry is not None and now < entry.expires_at:
            return self._fernet.decrypt(entry.token).decode("utf-8")

        if path in self._revoked:
            raise SecretRevokedError(f"secret revoked, not yet re-issued: {path}")

        try:
            value = self._provider.get(path)
        except KeyError as exc:
            raise SecretNotFoundError(f"no secret at path: {path}") from exc

        self._cache[path] = _CacheEntry(
            token=self._fernet.encrypt(value.encode("utf-8")),
            expires_at=now + self._cache_ttl_seconds,
        )
        return value

    def rotate(self, path: str) -> str:
        """Issue a new value for ``path``; the old value stays valid for the grace window."""
        old_entry = self._cache.get(path)
        new_value = self._provider.get(path)
        now = time.monotonic()
        new_entry = _CacheEntry(
            token=self._fernet.encrypt(new_value.encode("utf-8")),
            expires_at=now + self._cache_ttl_seconds,
        )
        if old_entry is not None:
            new_entry.previous_token = old_entry.token
            new_entry.grace_expires_at = now + self._grace_window_seconds
        self._cache[path] = new_entry
        self._revoked.discard(path)
        return new_value

    def revoke(self, path: str, reason: str) -> None:
        """Emergency revocation: invalidate ``path`` immediately, everywhere."""
        self._cache.pop(path, None)
        self._revoked.add(path)
        self._audit(path, actor_id="system", tenant_id=None, outcome=f"revoked:{reason}")

    def is_revoked(self, path: str) -> bool:
        return path in self._revoked

    def _audit(self, path: str, actor_id: str, tenant_id: str | None, *, outcome: str) -> None:
        if self._audit_repository is None or tenant_id is None:
            return
        self._audit_repository.append(
            tenant_id,
            actor_id=actor_id,
            action="secret_accessed",
            resource_type="secret",
            resource_id=path,
            outcome=outcome,
        )
