"""Unit tests for W17 customer impersonation service — no Postgres/pydantic.

Run with:
    cd ~/VoiceOS-phase17
    python3 -m pytest tests/unit/services/test_w17_impersonation.py --noconftest -v
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from src.services.support.impersonation import (
    _MAX_ACTIVE_PER_OPERATOR,
    ImpersonationService,
    _row_to_session,
)

# ---------------------------------------------------------------------------
# Fake DB infrastructure
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, script: list[Any]) -> None:
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
        return self._pending

    def fetchall(self) -> list[tuple]:
        if isinstance(self._pending, list):
            return self._pending
        return [self._pending] if self._pending is not None else []

    def close(self) -> None:
        pass


class _FakeConn:
    """Each positional arg is the execute-script for one cursor() call."""

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


def _make_session_row(
    session_id: str = "s1",
    operator_id: str = "op-1",
    operator_email: str = "ops@co.com",
    tenant_id: str = "tenant-a",
    reason: str = "Customer requested debug",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    is_active: bool = True,
    ip_address: str | None = "10.0.0.1",
    actions_count: int = 0,
) -> tuple:
    return (
        session_id,
        operator_id,
        operator_email,
        tenant_id,
        reason,
        started_at or datetime.now(UTC),
        ended_at,
        is_active,
        ip_address,
        actions_count,
    )


# ---------------------------------------------------------------------------
# Max-active-per-operator constant
# ---------------------------------------------------------------------------


def test_max_active_per_operator_is_one() -> None:
    assert _MAX_ACTIVE_PER_OPERATOR == 1


# ---------------------------------------------------------------------------
# Row-to-dataclass mapping
# ---------------------------------------------------------------------------


def test_row_to_session_active() -> None:
    row = _make_session_row(is_active=True, actions_count=3)
    s = _row_to_session(row)
    assert s.id == "s1"
    assert s.operator_id == "op-1"
    assert s.operator_email == "ops@co.com"
    assert s.tenant_id == "tenant-a"
    assert s.reason == "Customer requested debug"
    assert s.is_active is True
    assert s.actions_count == 3
    assert s.ended_at is None


def test_row_to_session_ended() -> None:
    now = datetime.now(UTC)
    row = _make_session_row(ended_at=now, is_active=False, actions_count=5)
    s = _row_to_session(row)
    assert s.is_active is False
    assert s.ended_at == now
    assert s.actions_count == 5


# ---------------------------------------------------------------------------
# ImpersonationService.start_session
# ---------------------------------------------------------------------------


def test_start_session_succeeds_when_no_active_session() -> None:
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    # start_session uses ONE cursor for both COUNT and INSERT
    conn = _FakeConn(
        [(0,), (session_id, now)],  # cursor 1: COUNT fetchone, INSERT fetchone
    )
    svc = ImpersonationService(conn)
    session = svc.start_session(
        operator_id="op-1",
        operator_email="ops@co.com",
        tenant_id="tenant-a",
        reason="Debug request",
    )
    assert session.id == session_id
    assert session.operator_id == "op-1"
    assert session.operator_email == "ops@co.com"
    assert session.tenant_id == "tenant-a"
    assert session.reason == "Debug request"
    assert session.is_active is True
    assert session.actions_count == 0
    assert session.ended_at is None


def test_start_session_blocked_when_operator_has_active_session() -> None:
    """Second session start must raise ValueError."""
    conn = _FakeConn(
        [(1,)],  # cursor 1: COUNT(*) = 1 — already has active session
    )
    svc = ImpersonationService(conn)
    with pytest.raises(ValueError, match="already has an active impersonation session"):
        svc.start_session(
            operator_id="op-1",
            operator_email="ops@co.com",
            tenant_id="tenant-b",
            reason="Another reason",
        )


def test_start_session_error_message_includes_operator_email() -> None:
    conn = _FakeConn(
        [(1,)],
    )
    svc = ImpersonationService(conn)
    with pytest.raises(ValueError, match=r"ops@company\.com"):
        svc.start_session(
            operator_id="op-2",
            operator_email="ops@company.com",
            tenant_id="t",
            reason="r",
        )


def test_start_session_with_ip_address() -> None:
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    conn = _FakeConn(
        [(0,), (session_id, now)],
    )
    svc = ImpersonationService(conn)
    session = svc.start_session(
        operator_id="op-1",
        operator_email="ops@co.com",
        tenant_id="t",
        reason="r",
        ip_address="192.168.1.100",
    )
    assert session.ip_address == "192.168.1.100"


def test_start_session_commits_transaction() -> None:
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    conn = _FakeConn(
        [(0,), (session_id, now)],
    )
    svc = ImpersonationService(conn)
    svc.start_session(
        operator_id="op-1",
        operator_email="o@o.com",
        tenant_id="t",
        reason="r",
    )
    assert conn.committed


# ---------------------------------------------------------------------------
# ImpersonationService.end_session
# ---------------------------------------------------------------------------


def test_end_session_returns_updated_session() -> None:
    now = datetime.now(UTC)
    ended_row = _make_session_row(ended_at=now, is_active=False)
    conn = _FakeConn(
        [("s1",)],  # cursor 1: UPDATE RETURNING id
        [ended_row],  # cursor 2: SELECT in get_session()
    )
    svc = ImpersonationService(conn)
    result = svc.end_session("s1", "op-1")
    assert result is not None
    assert result.is_active is False


def test_end_session_wrong_operator_returns_none() -> None:
    """UPDATE WHERE id=%s AND operator_id=%s returns nothing → None."""
    conn = _FakeConn(
        [None],
    )
    svc = ImpersonationService(conn)
    result = svc.end_session("s1", "wrong-operator")
    assert result is None


def test_end_session_commits() -> None:
    ended_row = _make_session_row(ended_at=datetime.now(UTC), is_active=False)
    conn = _FakeConn(
        [("s1",)],  # cursor 1: UPDATE RETURNING id
        [ended_row],  # cursor 2: SELECT in get_session()
    )
    svc = ImpersonationService(conn)
    svc.end_session("s1", "op-1")
    assert conn.committed


# ---------------------------------------------------------------------------
# ImpersonationService.log_action
# ---------------------------------------------------------------------------


def test_log_action_returns_action_object() -> None:
    action_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    # log_action uses ONE cursor for INSERT and UPDATE
    conn = _FakeConn(
        [(action_id, now), None],  # cursor 1: INSERT fetchone, then UPDATE
    )
    svc = ImpersonationService(conn)
    action = svc.log_action(
        "s1",
        action_type="VIEW_CAMPAIGN",
        resource="campaigns/abc",
        details={"campaign_id": "abc"},
    )
    assert action.id == action_id
    assert action.session_id == "s1"
    assert action.action_type == "VIEW_CAMPAIGN"
    assert action.resource == "campaigns/abc"
    assert action.details == {"campaign_id": "abc"}
    assert action.performed_at == now


def test_log_action_without_details_defaults_to_empty_dict() -> None:
    now = datetime.now(UTC)
    conn = _FakeConn(
        [(str(uuid.uuid4()), now), None],
    )
    svc = ImpersonationService(conn)
    action = svc.log_action("s1", action_type="VIEW_LEADS", resource="leads/")
    assert action.details == {}


def test_log_action_commits() -> None:
    now = datetime.now(UTC)
    conn = _FakeConn(
        [(str(uuid.uuid4()), now), None],
    )
    svc = ImpersonationService(conn)
    svc.log_action("s1", action_type="X", resource="r")
    assert conn.committed


# ---------------------------------------------------------------------------
# ImpersonationService.get_session
# ---------------------------------------------------------------------------


def test_get_session_returns_session() -> None:
    row = _make_session_row(session_id="s-xyz")
    conn = _FakeConn(
        [row],
    )
    svc = ImpersonationService(conn)
    s = svc.get_session("s-xyz")
    assert s is not None
    assert s.id == "s-xyz"


def test_get_session_not_found_returns_none() -> None:
    conn = _FakeConn(
        [None],
    )
    svc = ImpersonationService(conn)
    s = svc.get_session("nonexistent")
    assert s is None


# ---------------------------------------------------------------------------
# ImpersonationService.list_sessions — filter SQL
# ---------------------------------------------------------------------------


def test_list_sessions_no_filter_no_where() -> None:
    conn = _FakeConn(
        [[]],
    )
    svc = ImpersonationService(conn)
    svc.list_sessions()
    sql = conn.last_cursor.executed[0][0]
    assert "WHERE" not in sql


def test_list_sessions_active_only_adds_is_active_clause() -> None:
    conn = _FakeConn(
        [[]],
    )
    svc = ImpersonationService(conn)
    svc.list_sessions(active_only=True)
    sql = conn.last_cursor.executed[0][0]
    assert "is_active=TRUE" in sql


def test_list_sessions_tenant_filter_adds_tenant_id_clause() -> None:
    conn = _FakeConn(
        [[]],
    )
    svc = ImpersonationService(conn)
    svc.list_sessions(tenant_id="tenant-x")
    sql = conn.last_cursor.executed[0][0]
    assert "tenant_id=%s" in sql


def test_list_sessions_operator_filter() -> None:
    conn = _FakeConn(
        [[]],
    )
    svc = ImpersonationService(conn)
    svc.list_sessions(operator_id="op-99")
    sql = conn.last_cursor.executed[0][0]
    assert "operator_id=%s" in sql


def test_list_sessions_combined_filters_use_and() -> None:
    conn = _FakeConn(
        [[]],
    )
    svc = ImpersonationService(conn)
    svc.list_sessions(tenant_id="t", active_only=True)
    sql = conn.last_cursor.executed[0][0]
    assert "AND" in sql


# ---------------------------------------------------------------------------
# ImpersonationService.get_actions
# ---------------------------------------------------------------------------


def test_get_actions_returns_action_list() -> None:
    now = datetime.now(UTC)
    conn = _FakeConn(
        [
            [
                ("a1", "s1", "VIEW_CAMPAIGN", "campaigns/c1", {}, now),
                ("a2", "s1", "VIEW_LEADS", "leads/", {}, now),
            ],
        ],
    )
    svc = ImpersonationService(conn)
    actions = svc.get_actions("s1")
    assert len(actions) == 2
    assert actions[0].id == "a1"
    assert actions[0].action_type == "VIEW_CAMPAIGN"
    assert actions[1].action_type == "VIEW_LEADS"


def test_get_actions_empty_session() -> None:
    conn = _FakeConn(
        [[]],
    )
    svc = ImpersonationService(conn)
    actions = svc.get_actions("s-empty")
    assert actions == []
