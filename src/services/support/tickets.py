"""Support ticket system (W17 — Operational Support System)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# SLA resolution deadline by priority (hours)
_SLA_HOURS: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH": 8,
    "MEDIUM": 24,
    "LOW": 72,
}


@dataclass
class SupportTicket:
    id: str
    tenant_id: str
    subject: str
    description: str
    status: str
    priority: str
    category: str
    reporter_email: str
    reporter_name: str
    assignee_email: str | None
    resolution_note: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    sla_deadline: datetime | None
    comments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TicketComment:
    id: str
    ticket_id: str
    author: str
    body: str
    is_internal: bool
    created_at: datetime


class SupportTicketRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def create(
        self,
        *,
        tenant_id: str,
        subject: str,
        description: str,
        priority: str = "MEDIUM",
        category: str = "general",
        reporter_email: str,
        reporter_name: str = "",
    ) -> SupportTicket:
        sla_deadline = datetime.now(UTC) + timedelta(hours=_SLA_HOURS.get(priority, 24))
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO support_tickets
                (tenant_id, subject, description, priority, category,
                 reporter_email, reporter_name, sla_deadline)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, status, created_at, updated_at
            """,
            (tenant_id, subject, description, priority, category, reporter_email, reporter_name, sla_deadline),
        )
        row = cur.fetchone()
        self._conn.commit()
        cur.close()
        return SupportTicket(
            id=row[0],
            tenant_id=tenant_id,
            subject=subject,
            description=description,
            status=row[1],
            priority=priority,
            category=category,
            reporter_email=reporter_email,
            reporter_name=reporter_name,
            assignee_email=None,
            resolution_note=None,
            created_at=row[2],
            updated_at=row[3],
            resolved_at=None,
            sla_deadline=sla_deadline,
        )

    def get(self, ticket_id: str) -> SupportTicket | None:
        cur = self._conn.cursor()
        cur.execute(_SELECT + " WHERE id=%s", (ticket_id,))
        row = cur.fetchone()
        cur.close()
        return _row_to_ticket(row) if row else None

    def list_all(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        limit: int = 100,
    ) -> list[SupportTicket]:
        clauses, params = [], []
        if status:
            clauses.append("status=%s")
            params.append(status)
        if priority:
            clauses.append("priority=%s")
            params.append(priority)
        if assignee:
            clauses.append("assignee_email=%s")
            params.append(assignee)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        cur = self._conn.cursor()
        cur.execute(f"{_SELECT} {where} ORDER BY created_at DESC LIMIT %s", params)
        rows = cur.fetchall()
        cur.close()
        return [_row_to_ticket(r) for r in rows]

    def list_for_tenant(self, tenant_id: str, *, status: str | None = None, limit: int = 50) -> list[SupportTicket]:
        params: list[Any] = [tenant_id]
        extra = ""
        if status:
            extra = " AND status=%s"
            params.append(status)
        params.append(limit)
        cur = self._conn.cursor()
        cur.execute(
            f"{_SELECT} WHERE tenant_id=%s{extra} ORDER BY created_at DESC LIMIT %s",
            params,
        )
        rows = cur.fetchall()
        cur.close()
        return [_row_to_ticket(r) for r in rows]

    def update_status(
        self,
        ticket_id: str,
        status: str,
        *,
        resolution_note: str | None = None,
    ) -> SupportTicket | None:
        resolved_at = datetime.now(UTC) if status in ("RESOLVED", "CLOSED") else None
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE support_tickets "
            "SET status=%s, resolution_note=COALESCE(%s,resolution_note), "
            "resolved_at=COALESCE(%s,resolved_at), updated_at=NOW() "
            "WHERE id=%s RETURNING id",
            (status, resolution_note, resolved_at, ticket_id),
        )
        if not cur.fetchone():
            cur.close()
            return None
        self._conn.commit()
        cur.close()
        return self.get(ticket_id)

    def assign(self, ticket_id: str, assignee_email: str) -> SupportTicket | None:
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE support_tickets "
            "SET assignee_email=%s, status='IN_PROGRESS', updated_at=NOW() "
            "WHERE id=%s RETURNING id",
            (assignee_email, ticket_id),
        )
        if not cur.fetchone():
            cur.close()
            return None
        self._conn.commit()
        cur.close()
        return self.get(ticket_id)

    def add_comment(self, ticket_id: str, author: str, body: str, is_internal: bool = False) -> TicketComment:
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO support_ticket_comments (ticket_id, author, body, is_internal) "
            "VALUES (%s,%s,%s,%s) RETURNING id, created_at",
            (ticket_id, author, body, is_internal),
        )
        row = cur.fetchone()
        cur.execute("UPDATE support_tickets SET updated_at=NOW() WHERE id=%s", (ticket_id,))
        self._conn.commit()
        cur.close()
        return TicketComment(
            id=row[0],
            ticket_id=ticket_id,
            author=author,
            body=body,
            is_internal=is_internal,
            created_at=row[1],
        )

    def get_comments(self, ticket_id: str) -> list[TicketComment]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT id,ticket_id,author,body,is_internal,created_at "
            "FROM support_ticket_comments WHERE ticket_id=%s ORDER BY created_at",
            (ticket_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            TicketComment(id=r[0], ticket_id=r[1], author=r[2], body=r[3], is_internal=r[4], created_at=r[5])
            for r in rows
        ]

    def sla_stats(self) -> dict[str, Any]:
        cur = self._conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status NOT IN ('RESOLVED','CLOSED')) AS open_count,
                COUNT(*) FILTER (WHERE status IN ('RESOLVED','CLOSED'))     AS closed_count,
                COUNT(*) FILTER (
                    WHERE sla_deadline < NOW() AND status NOT IN ('RESOLVED','CLOSED')
                ) AS breached,
                COUNT(*) FILTER (
                    WHERE priority='CRITICAL' AND status NOT IN ('RESOLVED','CLOSED')
                ) AS critical_open,
                AVG(EXTRACT(EPOCH FROM (resolved_at - created_at))/3600)
                    FILTER (WHERE resolved_at IS NOT NULL) AS avg_resolution_hours
            FROM support_tickets
        """)
        row = cur.fetchone()
        cur.execute("""
            SELECT priority,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (
                       WHERE sla_deadline < NOW() AND status NOT IN ('RESOLVED','CLOSED')
                   ) AS breached
            FROM support_tickets
            WHERE status NOT IN ('RESOLVED','CLOSED')
            GROUP BY priority
        """)
        breakdown = {r[0]: {"total": r[1], "breached": r[2]} for r in cur.fetchall()}
        cur.close()
        return {
            "openCount": row[0] or 0,
            "closedCount": row[1] or 0,
            "slaBreachedCount": row[2] or 0,
            "criticalOpen": row[3] or 0,
            "avgResolutionHours": float(row[4]) if row[4] else None,
            "byPriority": breakdown,
        }


_SELECT = (
    "SELECT id,tenant_id,subject,description,status,priority,category,"
    "reporter_email,reporter_name,assignee_email,resolution_note,"
    "created_at,updated_at,resolved_at,sla_deadline FROM support_tickets"
)


def _row_to_ticket(row: tuple) -> SupportTicket:
    return SupportTicket(
        id=row[0],
        tenant_id=row[1],
        subject=row[2],
        description=row[3],
        status=row[4],
        priority=row[5],
        category=row[6],
        reporter_email=row[7],
        reporter_name=row[8],
        assignee_email=row[9],
        resolution_note=row[10],
        created_at=row[11],
        updated_at=row[12],
        resolved_at=row[13],
        sla_deadline=row[14],
    )
