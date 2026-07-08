"""AuditSearch — filtered queries over the audit trail for compliance review.

Architecture: V4 Ch11 (Audit Architecture) §11.7 (``query``), §11.11
(forensic reconstruction).
"""

from __future__ import annotations

from typing import Any

from .event import AuditEvent

_CHAIN_ROW_FIELDS = (
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
    "seq",
    "prev_hash",
    "hash",
)


class AuditSearch:
    """Read-only, tenant-scoped search over the audit trail."""

    def __init__(self, audit_repository: Any) -> None:
        self._repo = audit_repository

    def by_resource(self, tenant_id: Any, resource_type: str, resource_id: str) -> list[AuditEvent]:
        """The full audit trail for one resource (V4 Ch11 forensic reconstruction)."""
        rows = self._repo.find_by_resource(tenant_id, resource_type, resource_id)
        return [self._hydrate_plain(row) for row in rows]

    def in_range(self, tenant_id: Any, start: Any = None, end: Any = None) -> list[AuditEvent]:
        """All chain-covered events for ``tenant_id`` within ``[start, end]``, in order."""
        rows = self._repo.iter_chain(tenant_id, start, end)
        return [self._hydrate_chain(row) for row in rows]

    @staticmethod
    def _hydrate_plain(row: tuple[Any, ...]) -> AuditEvent:
        (
            audit_id,
            tenant_id,
            actor_id,
            action,
            resource_type,
            resource_id,
            outcome,
            ip_address,
            event_payload,
            recorded_at,
        ) = row
        return AuditEvent(
            audit_id=str(audit_id),
            tenant_id=str(tenant_id),
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            ip_address=ip_address,
            event_payload=event_payload,
            recorded_at=recorded_at,
        )

    @staticmethod
    def _hydrate_chain(row: tuple[Any, ...]) -> AuditEvent:
        values = dict(zip(_CHAIN_ROW_FIELDS, row, strict=True))
        return AuditEvent(
            audit_id=str(values["audit_id"]),
            tenant_id=str(values["tenant_id"]),
            actor_id=values["actor_id"],
            action=values["action"],
            resource_type=values["resource_type"],
            resource_id=values["resource_id"],
            outcome=values["outcome"],
            ip_address=values["ip_address"],
            event_payload=values["event_payload"],
            recorded_at=values["recorded_at"],
            seq=values["seq"],
            prev_hash=values["prev_hash"],
            hash=values["hash"],
        )
