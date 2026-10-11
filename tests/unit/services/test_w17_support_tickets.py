"""Unit tests for W17 support ticket system — runs locally, no Postgres/pydantic.

Run with:
    cd ~/VoiceOS-phase17
    python3 -m pytest tests/unit/services/test_w17_support_tickets.py --noconftest -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from src.services.support.tickets import (
    _SLA_HOURS,
    SupportTicketRepository,
    _row_to_ticket,
)


# ---------------------------------------------------------------------------
# Fake DB infrastructure
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Replays a pre-loaded script of results, one per execute() call."""

    def __init__(self, script: list[Any]) -> None:
        # Each entry is what the i-th execute call will return from fetch*.
        # - tuple  → fetchone() returns it, fetchall() returns [it]
        # - list   → fetchall() returns it, fetchone() returns first or None
        # - None   → fetchone() returns None, fetchall() returns []
        self._script = script
        self._idx = 0
        self._pending: Any = None
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = ()) -> None:
        self.executed.append((sql, params))
        self._pending = self._script[self._idx] if self._idx < len(self._script) else None
        self._idx += 1

    def fetchone(self) -> tuple | None:
        if isinstance(self._pending, list):
            return self._pending[0] if self._pending else None
        return self._pending  # tuple or None

    def fetchall(self) -> list[tuple]:
        if isinstance(self._pending, list):
            return self._pending
        return [self._pending] if self._pending is not None else []

    def close(self) -> None:
        pass


class _FakeConn:
    """Each positional arg to the constructor is the execute-script for one cursor() call.

    Methods like update_status() and assign() call self.get() internally, which
    creates a SECOND cursor.  Pass two scripts to support that:
        _FakeConn([script_for_cursor_1], [script_for_cursor_2])
    """

    def __init__(self, *cursor_scripts: list[Any]) -> None:
        self._scripts: list[list[Any]] = list(cursor_scripts) if cursor_scripts else [[]]
        self._idx = 0
        self.committed = False
        self._cursors: list[_FakeCursor] = []

    def cursor(self) -> _FakeCursor:
        script = self._scripts[self._idx] if self._idx < len(self._scripts) else []
        self._idx += 1
        cur = _FakeCursor(script)
        self._cursors.append(cur)
        return cur

    def commit(self) -> None:
        self.committed = True

    @property
    def last_cursor(self) -> _FakeCursor | None:
        return self._cursors[-1] if self._cursors else None


def _ts(offset_hours: float = 0.0) -> datetime:
    return datetime.now(UTC) + timedelta(hours=offset_hours)


def _make_ticket_row(
    ticket_id: str = "t1",
    tenant_id: str = "tenant-a",
    subject: str = "Login broken",
    description: str = "desc",
    status: str = "OPEN",
    priority: str = "HIGH",
    category: str = "auth",
    reporter_email: str = "rep@example.com",
    reporter_name: str = "Rep",
    assignee_email: str | None = None,
    resolution_note: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    resolved_at: datetime | None = None,
    sla_deadline: datetime | None = None,
) -> tuple:
    now = datetime.now(UTC)
    return (
        ticket_id, tenant_id, subject, description, status, priority,
        category, reporter_email, reporter_name, assignee_email,
        resolution_note,
        created_at or now,
        updated_at or now,
        resolved_at,
        sla_deadline or (now + timedelta(hours=8)),
    )


# ---------------------------------------------------------------------------
# SLA constants
# ---------------------------------------------------------------------------

def test_sla_hours_critical() -> None:
    assert _SLA_HOURS["CRITICAL"] == 4


def test_sla_hours_high() -> None:
    assert _SLA_HOURS["HIGH"] == 8


def test_sla_hours_medium() -> None:
    assert _SLA_HOURS["MEDIUM"] == 24


def test_sla_hours_low() -> None:
    assert _SLA_HOURS["LOW"] == 72


def test_all_standard_priorities_covered() -> None:
    assert set(_SLA_HOURS) == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


# ---------------------------------------------------------------------------
# Row-to-dataclass mapping
# ---------------------------------------------------------------------------

def test_row_to_ticket_full_mapping() -> None:
    now = datetime.now(UTC)
    row = _make_ticket_row(
        ticket_id="abc", tenant_id="t-xyz", subject="Test subject",
        description="Test desc", status="IN_PROGRESS", priority="CRITICAL",
        category="billing", reporter_email="a@b.com", reporter_name="Alice",
        assignee_email="ops@co.com", resolution_note=None,
        created_at=now, updated_at=now, resolved_at=None,
        sla_deadline=now + timedelta(hours=4),
    )
    t = _row_to_ticket(row)
    assert t.id == "abc"
    assert t.tenant_id == "t-xyz"
    assert t.subject == "Test subject"
    assert t.status == "IN_PROGRESS"
    assert t.priority == "CRITICAL"
    assert t.assignee_email == "ops@co.com"
    assert t.resolution_note is None
    assert t.resolved_at is None
    assert t.sla_deadline == now + timedelta(hours=4)


def test_row_to_ticket_resolved_row() -> None:
    now = datetime.now(UTC)
    row = _make_ticket_row(
        status="RESOLVED", resolved_at=now, resolution_note="Fixed it"
    )
    t = _row_to_ticket(row)
    assert t.status == "RESOLVED"
    assert t.resolved_at == now
    assert t.resolution_note == "Fixed it"


# ---------------------------------------------------------------------------
# Repository.create — SLA deadline logic
# ---------------------------------------------------------------------------

def test_create_critical_sla_deadline() -> None:
    """CRITICAL ticket must get a deadline ~4 hours from now."""
    ticket_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    conn = _FakeConn(
        [(ticket_id, "OPEN", now, now)],  # RETURNING id, status, created_at, updated_at
    )
    repo = SupportTicketRepository(conn)
    before = datetime.now(UTC)
    ticket = repo.create(
        tenant_id="t1", subject="S", description="D",
        priority="CRITICAL", category="c",
        reporter_email="e@e.com", reporter_name="E",
    )
    after = datetime.now(UTC)
    assert ticket.sla_deadline is not None
    expected_low = before + timedelta(hours=4)
    expected_high = after + timedelta(hours=4)
    assert expected_low <= ticket.sla_deadline <= expected_high


def test_create_low_priority_sla_deadline() -> None:
    """LOW ticket must get a deadline ~72 hours from now."""
    now = datetime.now(UTC)
    conn = _FakeConn(
        [(str(uuid.uuid4()), "OPEN", now, now)],
    )
    repo = SupportTicketRepository(conn)
    before = datetime.now(UTC)
    ticket = repo.create(
        tenant_id="t1", subject="S", description="D",
        priority="LOW", category="c",
        reporter_email="e@e.com", reporter_name="E",
    )
    after = datetime.now(UTC)
    expected_low = before + timedelta(hours=72)
    expected_high = after + timedelta(hours=72)
    assert ticket.sla_deadline is not None
    assert expected_low <= ticket.sla_deadline <= expected_high


def test_create_unknown_priority_falls_back_to_24h() -> None:
    """Unknown priority falls back to 24h (default in _SLA_HOURS.get)."""
    now = datetime.now(UTC)
    conn = _FakeConn(
        [(str(uuid.uuid4()), "OPEN", now, now)],
    )
    repo = SupportTicketRepository(conn)
    before = datetime.now(UTC)
    ticket = repo.create(
        tenant_id="t1", subject="S", description="D",
        priority="SUPERCRITICAL",  # unknown
        category="c", reporter_email="e@e.com", reporter_name="E",
    )
    after = datetime.now(UTC)
    assert ticket.sla_deadline is not None
    expected_low = before + timedelta(hours=24)
    expected_high = after + timedelta(hours=24)
    assert expected_low <= ticket.sla_deadline <= expected_high


def test_create_commits_transaction() -> None:
    now = datetime.now(UTC)
    conn = _FakeConn([(str(uuid.uuid4()), "OPEN", now, now)],)
    repo = SupportTicketRepository(conn)
    repo.create(
        tenant_id="t1", subject="S", description="D",
        priority="MEDIUM", category="c",
        reporter_email="e@e.com", reporter_name="E",
    )
    assert conn.committed


def test_create_returns_correct_field_values() -> None:
    ticket_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    conn = _FakeConn([(ticket_id, "OPEN", now, now)],)
    repo = SupportTicketRepository(conn)
    ticket = repo.create(
        tenant_id="tenant-x", subject="My subject",
        description="My desc", priority="HIGH",
        category="billing", reporter_email="u@u.com",
        reporter_name="User",
    )
    assert ticket.id == ticket_id
    assert ticket.tenant_id == "tenant-x"
    assert ticket.subject == "My subject"
    assert ticket.description == "My desc"
    assert ticket.priority == "HIGH"
    assert ticket.category == "billing"
    assert ticket.reporter_email == "u@u.com"
    assert ticket.reporter_name == "User"
    assert ticket.status == "OPEN"
    assert ticket.assignee_email is None
    assert ticket.resolution_note is None
    assert ticket.resolved_at is None


# ---------------------------------------------------------------------------
# Repository.get
# ---------------------------------------------------------------------------

def test_get_returns_ticket_when_found() -> None:
    row = _make_ticket_row()
    conn = _FakeConn([row],)
    repo = SupportTicketRepository(conn)
    ticket = repo.get("t1")
    assert ticket is not None
    assert ticket.id == "t1"


def test_get_returns_none_when_not_found() -> None:
    conn = _FakeConn([None],)
    repo = SupportTicketRepository(conn)
    ticket = repo.get("missing-id")
    assert ticket is None


# ---------------------------------------------------------------------------
# Repository.update_status
# ---------------------------------------------------------------------------

def test_update_status_resolved_sets_resolved_at() -> None:
    row = _make_ticket_row(status="RESOLVED", resolved_at=datetime.now(UTC))
    conn = _FakeConn(
        [("t1",)],  # cursor 1: UPDATE RETURNING id
        [row],      # cursor 2: SELECT in self.get()
    )
    repo = SupportTicketRepository(conn)
    result = repo.update_status("t1", "RESOLVED", resolution_note="All good")
    assert result is not None
    assert result.status == "RESOLVED"


def test_update_status_not_found_returns_none() -> None:
    conn = _FakeConn([None],)  # cursor 1: UPDATE RETURNING returns nothing → early return
    repo = SupportTicketRepository(conn)
    result = repo.update_status("nonexistent", "RESOLVED")
    assert result is None


def test_update_status_non_resolved_does_not_set_resolved_at() -> None:
    """IN_PROGRESS transition must not set resolved_at."""
    row = _make_ticket_row(status="IN_PROGRESS", resolved_at=None)
    conn = _FakeConn(
        [("t1",)],  # cursor 1: UPDATE RETURNING id
        [row],      # cursor 2: SELECT in self.get()
    )
    repo = SupportTicketRepository(conn)
    result = repo.update_status("t1", "IN_PROGRESS")
    assert result is not None
    assert result.resolved_at is None


# ---------------------------------------------------------------------------
# Repository.assign
# ---------------------------------------------------------------------------

def test_assign_returns_updated_ticket() -> None:
    row = _make_ticket_row(assignee_email="ops@ops.com", status="IN_PROGRESS")
    conn = _FakeConn(
        [("t1",)],  # cursor 1: UPDATE RETURNING id
        [row],      # cursor 2: SELECT in self.get()
    )
    repo = SupportTicketRepository(conn)
    result = repo.assign("t1", "ops@ops.com")
    assert result is not None
    assert result.assignee_email == "ops@ops.com"


def test_assign_not_found_returns_none() -> None:
    conn = _FakeConn([None],)
    repo = SupportTicketRepository(conn)
    result = repo.assign("bad-id", "ops@ops.com")
    assert result is None


# ---------------------------------------------------------------------------
# Repository.add_comment
# ---------------------------------------------------------------------------

def test_add_comment_returns_comment_object() -> None:
    comment_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    # add_comment uses ONE cursor for both INSERT and UPDATE
    conn = _FakeConn(
        [(comment_id, now), None],  # cursor 1: INSERT fetchone, then UPDATE (no RETURNING)
    )
    repo = SupportTicketRepository(conn)
    comment = repo.add_comment("t1", "admin@co.com", "We are looking into this.")
    assert comment.id == comment_id
    assert comment.ticket_id == "t1"
    assert comment.author == "admin@co.com"
    assert comment.body == "We are looking into this."
    assert comment.is_internal is False
    assert comment.created_at == now


def test_add_internal_comment() -> None:
    comment_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    conn = _FakeConn([(comment_id, now), None],)
    repo = SupportTicketRepository(conn)
    comment = repo.add_comment("t1", "ops@co.com", "Internal note", is_internal=True)
    assert comment.is_internal is True


def test_add_comment_commits_transaction() -> None:
    now = datetime.now(UTC)
    conn = _FakeConn([(str(uuid.uuid4()), now), None],)
    repo = SupportTicketRepository(conn)
    repo.add_comment("t1", "a@b.com", "Hello")
    assert conn.committed


# ---------------------------------------------------------------------------
# Repository.list_all — filter SQL construction
# ---------------------------------------------------------------------------

def test_list_all_no_filters_no_where_clause() -> None:
    conn = _FakeConn([[]],)  # fetchall returns empty list
    repo = SupportTicketRepository(conn)
    repo.list_all()
    sql = conn.last_cursor.executed[0][0]
    assert "WHERE" not in sql


def test_list_all_status_filter_includes_where() -> None:
    conn = _FakeConn([[]],)
    repo = SupportTicketRepository(conn)
    repo.list_all(status="OPEN")
    sql = conn.last_cursor.executed[0][0]
    assert "WHERE" in sql
    assert "status=%s" in sql


def test_list_all_priority_filter_included() -> None:
    conn = _FakeConn([[]],)
    repo = SupportTicketRepository(conn)
    repo.list_all(priority="CRITICAL")
    sql = conn.last_cursor.executed[0][0]
    assert "priority=%s" in sql


def test_list_all_multiple_filters_uses_and() -> None:
    conn = _FakeConn([[]],)
    repo = SupportTicketRepository(conn)
    repo.list_all(status="OPEN", priority="HIGH")
    sql = conn.last_cursor.executed[0][0]
    assert "AND" in sql
