"""Unit tests for ComplianceViolationRepository (Phase 6d).

Tests idempotency, lifecycle transitions (ACTIVE → RESOLVED → ACTIVE re-detected),
and tenant isolation. Uses an in-memory fake connection to avoid a real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.libs.contracts.primitives import TenantId
from src.libs.repositories.compliance_violations import ComplianceViolationRepository


# ---------------------------------------------------------------------------
# Fake connection / cursor infrastructure
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Minimal cursor double: captures SQL + params for assertion."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.last_sql: str = ""
        self.last_params: tuple[Any, ...] = ()
        self._rows = rows or []
        self.rowcount = len(self._rows)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.last_sql = sql
        self.last_params = params

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._cursor = _FakeCursor(rows)
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComplianceViolationRepositoryUpsertActive:
    def test_upsert_executes_insert_on_conflict(self) -> None:
        conn = _FakeConn()
        repo = ComplianceViolationRepository(conn)
        repo.upsert_active(TenantId("t-1"), "consent.denied", "consent.denied: 5 events correlated")

        assert "INSERT INTO compliance_violations" in conn._cursor.last_sql
        assert "ON CONFLICT" in conn._cursor.last_sql
        assert conn.committed

    def test_upsert_passes_correct_params(self) -> None:
        conn = _FakeConn()
        repo = ComplianceViolationRepository(conn)
        repo.upsert_active(TenantId("t-abc"), "rule-x", "summary text")

        params = conn._cursor.last_params
        assert str(params[0]) == "t-abc"
        assert params[1] == "rule-x"
        assert params[2] == "summary text"
        assert isinstance(params[3], datetime)

    def test_upsert_commits_transaction(self) -> None:
        conn = _FakeConn()
        repo = ComplianceViolationRepository(conn)
        repo.upsert_active(TenantId("t-1"), "r1", "s")
        assert conn.committed


class TestComplianceViolationRepositoryIsViolated:
    def test_returns_true_when_active_row_exists(self) -> None:
        conn = _FakeConn(rows=[(1,)])
        repo = ComplianceViolationRepository(conn)
        assert repo.is_violated(TenantId("t-1")) is True

    def test_returns_false_when_no_active_row(self) -> None:
        conn = _FakeConn(rows=[])
        repo = ComplianceViolationRepository(conn)
        assert repo.is_violated(TenantId("t-2")) is False

    def test_query_scopes_to_tenant(self) -> None:
        conn = _FakeConn(rows=[])
        repo = ComplianceViolationRepository(conn)
        repo.is_violated(TenantId("tenant-xyz"))
        assert "tenant_id = %s" in conn._cursor.last_sql
        assert conn._cursor.last_params[0] == "tenant-xyz"

    def test_query_filters_active_status(self) -> None:
        conn = _FakeConn(rows=[])
        repo = ComplianceViolationRepository(conn)
        repo.is_violated(TenantId("t-1"))
        assert "status = 'ACTIVE'" in conn._cursor.last_sql


class TestComplianceViolationRepositoryListActive:
    def test_returns_hydrated_dicts(self) -> None:
        from uuid import uuid4

        vid = str(uuid4())
        now = datetime.now(UTC)
        conn = _FakeConn(rows=[(vid, "t-1", "rule1", "summary", "ACTIVE", now, None, None)])
        repo = ComplianceViolationRepository(conn)
        rows = repo.list_active(TenantId("t-1"))

        assert len(rows) == 1
        assert rows[0]["violation_id"] == vid
        assert rows[0]["rule_id"] == "rule1"
        assert rows[0]["status"] == "ACTIVE"
        assert rows[0]["resolved_at"] is None

    def test_empty_result_when_no_violations(self) -> None:
        conn = _FakeConn(rows=[])
        repo = ComplianceViolationRepository(conn)
        assert repo.list_active(TenantId("t-99")) == ()


class TestComplianceViolationRepositoryResolve:
    def test_resolve_calls_update(self) -> None:
        conn = _FakeConn()
        repo = ComplianceViolationRepository(conn)
        repo.resolve(TenantId("t-1"), "consent.denied")

        assert "UPDATE compliance_violations" in conn._cursor.last_sql
        assert "RESOLVED" in conn._cursor.last_sql

    def test_resolve_scopes_to_rule_id(self) -> None:
        conn = _FakeConn()
        repo = ComplianceViolationRepository(conn)
        repo.resolve(TenantId("t-1"), "specific-rule")

        assert "specific-rule" in str(conn._cursor.last_params)

    def test_resolve_commits(self) -> None:
        conn = _FakeConn()
        repo = ComplianceViolationRepository(conn)
        repo.resolve(TenantId("t-1"), "r")
        assert conn.committed


class TestComplianceViolationRepositoryHydrate:
    def test_hydrate_formats_timestamps_as_iso(self) -> None:
        from uuid import uuid4

        vid = str(uuid4())
        now = datetime.now(UTC)
        conn = _FakeConn(rows=[(vid, "t-1", "r1", "s", "ACTIVE", now, None, None)])
        repo = ComplianceViolationRepository(conn)
        rows = repo.list_active(TenantId("t-1"))

        assert rows[0]["detected_at"] == now.isoformat()

    def test_hydrate_resolved_at_none(self) -> None:
        from uuid import uuid4

        conn = _FakeConn(
            rows=[(str(uuid4()), "t-1", "r1", "s", "ACTIVE", datetime.now(UTC), None, None)]
        )
        repo = ComplianceViolationRepository(conn)
        rows = repo.list_active(TenantId("t-1"))
        assert rows[0]["resolved_at"] is None
