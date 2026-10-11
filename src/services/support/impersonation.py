"""Customer impersonation with strict audit controls (W17)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Only one active session per operator at a time
_MAX_ACTIVE_PER_OPERATOR = 1


@dataclass
class ImpersonationSession:
    id: str
    operator_id: str
    operator_email: str
    tenant_id: str
    reason: str
    started_at: datetime
    ended_at: datetime | None
    is_active: bool
    ip_address: str | None
    actions_count: int


@dataclass
class ImpersonationAction:
    id: str
    session_id: str
    action_type: str
    resource: str
    details: dict[str, Any]
    performed_at: datetime


class ImpersonationService:
    """Manages operator impersonation sessions with full audit trail.

    Every session start/end and every action taken while impersonating
    is persisted to the DB. One operator can only hold one active session
    at a time to prevent unchecked access escalation.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def start_session(
        self,
        *,
        operator_id: str,
        operator_email: str,
        tenant_id: str,
        reason: str,
        ip_address: str | None = None,
    ) -> ImpersonationSession:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM impersonation_sessions "
            "WHERE operator_id=%s AND is_active=TRUE",
            (operator_id,),
        )
        if cur.fetchone()[0] >= _MAX_ACTIVE_PER_OPERATOR:
            cur.close()
            raise ValueError(
                f"{operator_email} already has an active impersonation session — "
                "end it before starting a new one."
            )
        cur.execute(
            "INSERT INTO impersonation_sessions "
            "(operator_id, operator_email, tenant_id, reason, ip_address) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id, started_at",
            (operator_id, operator_email, tenant_id, reason, ip_address),
        )
        row = cur.fetchone()
        self._conn.commit()
        cur.close()
        return ImpersonationSession(
            id=row[0], operator_id=operator_id, operator_email=operator_email,
            tenant_id=tenant_id, reason=reason, started_at=row[1],
            ended_at=None, is_active=True, ip_address=ip_address, actions_count=0,
        )

    def end_session(self, session_id: str, operator_id: str) -> ImpersonationSession | None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE impersonation_sessions "
            "SET is_active=FALSE, ended_at=NOW() "
            "WHERE id=%s AND operator_id=%s AND is_active=TRUE RETURNING id",
            (session_id, operator_id),
        )
        if not cur.fetchone():
            cur.close()
            return None
        self._conn.commit()
        cur.close()
        return self.get_session(session_id)

    def log_action(
        self,
        session_id: str,
        *,
        action_type: str,
        resource: str,
        details: dict[str, Any] | None = None,
    ) -> ImpersonationAction:
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO impersonation_actions "
            "(session_id, action_type, resource, details) "
            "VALUES (%s,%s,%s,%s) RETURNING id, performed_at",
            (session_id, action_type, resource, details or {}),
        )
        row = cur.fetchone()
        cur.execute(
            "UPDATE impersonation_sessions SET actions_count=actions_count+1 WHERE id=%s",
            (session_id,),
        )
        self._conn.commit()
        cur.close()
        return ImpersonationAction(
            id=row[0], session_id=session_id, action_type=action_type,
            resource=resource, details=details or {}, performed_at=row[1],
        )

    def get_session(self, session_id: str) -> ImpersonationSession | None:
        cur = self._conn.cursor()
        cur.execute(_SELECT_SESSION + " WHERE id=%s", (session_id,))
        row = cur.fetchone()
        cur.close()
        return _row_to_session(row) if row else None

    def list_sessions(
        self,
        *,
        tenant_id: str | None = None,
        operator_id: str | None = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> list[ImpersonationSession]:
        clauses, params = [], []
        if tenant_id:
            clauses.append("tenant_id=%s")
            params.append(tenant_id)
        if operator_id:
            clauses.append("operator_id=%s")
            params.append(operator_id)
        if active_only:
            clauses.append("is_active=TRUE")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        cur = self._conn.cursor()
        cur.execute(
            f"{_SELECT_SESSION} {where} ORDER BY started_at DESC LIMIT %s", params
        )
        rows = cur.fetchall()
        cur.close()
        return [_row_to_session(r) for r in rows]

    def get_actions(self, session_id: str) -> list[ImpersonationAction]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT id,session_id,action_type,resource,details,performed_at "
            "FROM impersonation_actions WHERE session_id=%s ORDER BY performed_at",
            (session_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            ImpersonationAction(
                id=r[0], session_id=r[1], action_type=r[2],
                resource=r[3], details=r[4], performed_at=r[5],
            )
            for r in rows
        ]


_SELECT_SESSION = (
    "SELECT id,operator_id,operator_email,tenant_id,reason,"
    "started_at,ended_at,is_active,ip_address,actions_count "
    "FROM impersonation_sessions"
)


def _row_to_session(row: tuple) -> ImpersonationSession:
    return ImpersonationSession(
        id=row[0], operator_id=row[1], operator_email=row[2],
        tenant_id=row[3], reason=row[4], started_at=row[5],
        ended_at=row[6], is_active=row[7],
        ip_address=row[8], actions_count=row[9],
    )
