"""SystemXAuditRepository — append-only audit trail persistence."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..models import AuditEntry


def _dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val.replace(tzinfo=UTC) if val.tzinfo is None else val
    return datetime.fromisoformat(str(val)).replace(tzinfo=UTC)


def _row_to_entry(row: tuple) -> AuditEntry:
    entry_id, incident_id, recorded_at, actor, action, result, rollback_status, verification_outcome, metadata = row
    return AuditEntry(
        entry_id=entry_id,
        incident_id=incident_id,
        recorded_at=_dt(recorded_at),
        actor=actor,
        action=action,
        result=result,
        rollback_status=rollback_status,
        verification_outcome=verification_outcome,
        metadata=metadata or {},
    )


class SystemXAuditRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def append(self, entry: AuditEntry) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO system_x_audit_trail
                       (entry_id, incident_id, recorded_at, actor, action, result,
                        rollback_status, verification_outcome, metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    entry.entry_id, entry.incident_id, entry.recorded_at,
                    entry.actor, entry.action, entry.result,
                    entry.rollback_status, entry.verification_outcome,
                    json.dumps(entry.metadata),
                ),
            )
        self._conn.commit()

    def list_for_incident(self, incident_id: str) -> list[AuditEntry]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT entry_id, incident_id, recorded_at, actor, action, result,
                          rollback_status, verification_outcome, metadata
                   FROM system_x_audit_trail WHERE incident_id = %s ORDER BY recorded_at""",
                (incident_id,),
            )
            rows = cur.fetchall()
        return [_row_to_entry(r) for r in rows]

    def list_recent(self, limit: int = 100) -> list[AuditEntry]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT entry_id, incident_id, recorded_at, actor, action, result,
                          rollback_status, verification_outcome, metadata
                   FROM system_x_audit_trail ORDER BY recorded_at DESC LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
        return [_row_to_entry(r) for r in rows]


__all__ = ["SystemXAuditRepository"]
