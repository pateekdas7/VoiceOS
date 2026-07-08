"""IdempotencyRepository — generic idempotency key store (V3 Ch8).

Backs ``IdempotencyGuard.execute_once()`` (Sprint-015). The check-then-record
pattern is: caller calls ``check()``; if it returns ``None``, the caller
performs the effect and then calls ``record()`` with the outcome; if a
concurrent caller already recorded the same key, ``record()`` is a no-op
(``ON CONFLICT DO NOTHING``) and the original caller's ``check()`` on retry
returns the cached result instead of re-executing the effect.

Architecture: V3 Ch8 (Idempotency); Invariant EV-7.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ..contracts.primitives import TenantId
from .base import BaseRepository

_TABLE = "idempotency_keys"


class IdempotencyRepository(BaseRepository):
    """Tenant-scoped idempotency key check/record operations."""

    def check(self, tenant_id: TenantId, key: str) -> Any | None:
        """Return the cached result for ``key`` if it already exists, else ``None``.

        Does not insert — this is a pure read. Callers must follow the
        check-then-record pattern; this method never creates a key.
        """
        row = self._tenant_select_one(
            _TABLE,
            ("result",),
            tenant_id,
            extra_where="key = %s AND expires_at > NOW()",
            extra_params=(key,),
        )
        if row is None:
            return None
        (result_json,) = row
        if result_json is None:
            return None
        return json.loads(result_json) if isinstance(result_json, str) else result_json

    def claim(
        self,
        tenant_id: TenantId,
        key: str,
        resource_type: str,
        *,
        ttl_seconds: int = 86_400,
    ) -> bool:
        """Atomically claim ``key`` before executing its effect (Sprint-015).

        Inserts a placeholder row with ``result = NULL``. The unique ``key``
        primary key makes this atomic under concurrent callers: exactly one
        caller's INSERT succeeds (the "winner", who must now execute the
        effect and call ``complete()``); every other concurrent caller's
        INSERT is a no-op (the "losers", who must poll ``check()`` for the
        winner's result instead of re-executing the effect).

        Args:
            tenant_id: Tenant scope.
            key: The idempotency key.
            resource_type: Stable resource type code.
            ttl_seconds: How long the key remains valid (default 24h).

        Returns:
            ``True`` if this call claimed the key (execute the effect now),
            ``False`` if the key was already claimed by another caller.
        """
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        cur = self._execute(
            f"""
            INSERT INTO {_TABLE} (key, tenant_id, resource_type, created_at, expires_at)
            VALUES (%s, %s, %s, NOW(), %s)
            ON CONFLICT (key) DO NOTHING
            RETURNING key
            """,
            (key, tenant_id, resource_type, expires_at),
        )
        claimed = cur.fetchone() is not None
        self._commit()
        return claimed

    def complete(self, tenant_id: TenantId, key: str, result: Any) -> None:
        """Record the outcome of an effect for a previously claimed ``key``.

        Args:
            tenant_id: Tenant scope.
            key: The idempotency key that was claimed via ``claim()``.
            result: JSON-serializable outcome to cache for duplicate calls.
        """
        self._execute(
            f"UPDATE {_TABLE} SET result = %s::jsonb WHERE key = %s AND tenant_id = %s",
            (json.dumps(result), key, tenant_id),
        )
        self._commit()

    def record(
        self,
        tenant_id: TenantId,
        key: str,
        resource_type: str,
        result: Any,
        *,
        ttl_seconds: int = 86_400,
    ) -> bool:
        """Record the outcome of an effect under ``key``.

        Args:
            tenant_id: Tenant scope.
            key: The idempotency key (globally unique — typically
                ``{call_id}:{turn_id}:{effect_name}``).
            resource_type: Stable resource type code (e.g. ``'ptp'``, ``'sms'``).
            result: JSON-serializable outcome to cache for duplicate calls.
            ttl_seconds: How long the key remains valid (default 24h).

        Returns:
            ``True`` if this call recorded the key, ``False`` if a key with
            the same value already existed (no-op, matching the idempotent
            check-then-record contract).
        """
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        cur = self._execute(
            f"""
            INSERT INTO {_TABLE} (key, tenant_id, resource_type, result, created_at, expires_at)
            VALUES (%s, %s, %s, %s::jsonb, NOW(), %s)
            ON CONFLICT (key) DO NOTHING
            RETURNING key
            """,
            (key, tenant_id, resource_type, json.dumps(result), expires_at),
        )
        inserted = cur.fetchone() is not None
        self._commit()
        return inserted
