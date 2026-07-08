"""AuditRepository — immutable, append-only, hash-chained audit trail (V4 Ch11).

Immutability is enforced twice, in depth:
  1. Here (Python): ``update()``/``delete()`` raise before any SQL executes.
  2. In Postgres (Sprint-014 migration 0010): a ``BEFORE UPDATE OR DELETE``
     trigger on ``audit_log`` rejects the statement even for direct SQL access.

Tamper-evidence (Sprint-020, migration 0017) is computed here — the single
INSERT path — rather than in a parallel writer, so every call site (past and
future) is automatically hash-chained: each row's ``hash`` covers its own
fields plus the tenant's previous row's ``hash`` (``prev_hash``), forming a
per-tenant chain (V4 Ch11 §11.12, §11.19 "hash-chaining is per-stream").
``AuditLogger`` (Sprint-020, ``src/libs/audit/logger.py``) is the
PII-redacting, required-event-aware façade that calls ``append()``.

Architecture: V4 Ch11 (Audit Trail); V4 Ch3 (AI Governance).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, NoReturn

from ..contracts.primitives import TenantId
from .base import BaseRepository

_TABLE = "audit_log"

_AUDIT_COLUMNS = (
    "audit_id",
    "tenant_id",
    "actor_id",
    "action",
    "resource_type",
    "resource_id",
    "outcome",
    "ip_address",
    "event_payload",
    "recorded_at",
)

_CHAIN_COLUMNS = (*_AUDIT_COLUMNS, "seq", "prev_hash", "hash")

GENESIS_HASH = "0" * 64
"""The ``prev_hash`` used for the first hash-chained event of a tenant.

Public (not underscore-prefixed): :class:`~src.libs.audit.verifier.AuditVerifier`
recomputes the same chain independently and needs this same starting value.
"""


class ImmutableAuditLogError(RuntimeError):
    """Raised whenever code attempts to modify or delete an audit_log row."""


def compute_audit_hash(
    prev_hash: str,
    tenant_id: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: str,
    event_payload: dict[str, Any] | None,
) -> str:
    """SHA-256 of ``prev_hash`` + this row's canonical fields (V4 Ch11 §11.12)."""
    canonical = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "actor_id": actor_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "outcome": outcome,
            "event_payload": event_payload,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(f"{prev_hash}{canonical}".encode()).hexdigest()


class AuditRepository(BaseRepository):
    """Tenant-scoped, append-only, hash-chained audit log queries."""

    def append(
        self,
        tenant_id: TenantId,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        *,
        event_payload: dict[str, Any] | None = None,
        ip_address: str = "",
    ) -> str:
        """Append a new audit record and return its chain ``hash``.

        This is the only write path — there is no update/delete. The
        ``SELECT ... FOR UPDATE`` on the tenant's latest row serializes
        concurrent appends for the same tenant so two writers can never
        chain off the same ``prev_hash`` (the one race window this doesn't
        close is the very first event ever appended for a brand-new tenant,
        where there is no existing row to lock).
        """
        cur = self._execute(
            f"SELECT hash FROM {_TABLE} WHERE tenant_id = %s ORDER BY seq DESC LIMIT 1 FOR UPDATE",
            (tenant_id,),
        )
        row = cur.fetchone()
        prev_hash = row[0] if row and row[0] is not None else GENESIS_HASH

        new_hash = compute_audit_hash(
            prev_hash, tenant_id, actor_id, action, resource_type, resource_id, outcome, event_payload
        )

        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                tenant_id, actor_id, action, resource_type, resource_id, outcome,
                ip_address, event_payload, prev_hash, hash, recorded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW())
            """,
            (
                tenant_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                outcome,
                ip_address,
                json.dumps(event_payload) if event_payload is not None else None,
                prev_hash,
                new_hash,
            ),
        )
        self._commit()
        return new_hash

    def iter_chain(
        self,
        tenant_id: TenantId,
        start: Any = None,
        end: Any = None,
    ) -> tuple[Any, ...]:
        """Read the tenant's audit chain in ``seq`` order for verification.

        Only rows with a non-NULL ``hash`` are chain members (pre-Sprint-020
        rows, if any, predate the hash chain — see migration 0017's
        docstring) and are excluded.
        """
        extra_where = "hash IS NOT NULL"
        extra_params: list[Any] = []
        if start is not None:
            extra_where += " AND recorded_at >= %s"
            extra_params.append(start)
        if end is not None:
            extra_where += " AND recorded_at <= %s"
            extra_params.append(end)
        return tuple(
            self._tenant_select(
                _TABLE,
                _CHAIN_COLUMNS,
                tenant_id,
                extra_where=extra_where,
                extra_params=extra_params,
                order_by="seq ASC",
            )
        )

    def find_by_resource(self, tenant_id: TenantId, resource_type: str, resource_id: str) -> tuple[Any, ...]:
        """Read the audit trail for a specific resource, scoped to ``tenant_id``."""
        return tuple(
            self._tenant_select(
                _TABLE,
                _AUDIT_COLUMNS,
                tenant_id,
                extra_where="resource_type = %s AND resource_id = %s",
                extra_params=(resource_type, resource_id),
                order_by="recorded_at DESC",
            )
        )

    def update(self, *_args: object, **_kwargs: object) -> NoReturn:
        """Always raises — audit_log is append-only (V4 Ch11)."""
        raise ImmutableAuditLogError("audit_log is append-only: UPDATE is not permitted")

    def delete(self, *_args: object, **_kwargs: object) -> NoReturn:
        """Always raises — audit_log is append-only (V4 Ch11)."""
        raise ImmutableAuditLogError("audit_log is append-only: DELETE is not permitted")
