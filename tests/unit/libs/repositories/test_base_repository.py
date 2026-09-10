"""Unit tests for BaseRepository — tenant-scoping query builders (AR-8).

Architecture: V6 Ch7; AR-8 (tenant isolation).
"""

from __future__ import annotations

import pytest

from src.libs.circuit_breaker.breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError, CircuitState
from src.libs.repositories.base import BaseRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor


class TestTenantSelect:
    def test_tenant_id_is_first_where_condition(self) -> None:
        cursor = FakeCursor(fetchall_results=[[("row",)]])
        repo = BaseRepository(FakeConnection(cursor))

        repo._tenant_select("customers", ("customer_id",), "tenant-a")

        sql, params = cursor.executed[0]
        assert "WHERE tenant_id = %s" in sql
        assert params[0] == "tenant-a"

    def test_extra_where_is_anded_after_tenant_scope(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = BaseRepository(FakeConnection(cursor))

        repo._tenant_select(
            "customers",
            ("customer_id",),
            "tenant-a",
            extra_where="crm_id = %s",
            extra_params=("crm-123",),
        )

        sql, params = cursor.executed[0]
        assert "WHERE tenant_id = %s AND crm_id = %s" in sql
        assert params == ("tenant-a", "crm-123")

    def test_order_by_and_limit_appended(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = BaseRepository(FakeConnection(cursor))

        repo._tenant_select("customers", ("customer_id",), "tenant-a", order_by="created_at DESC", limit=5)

        sql, params = cursor.executed[0]
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT %s" in sql
        assert params[-1] == 5

    def test_returns_rows_from_cursor(self) -> None:
        cursor = FakeCursor(fetchall_results=[[("c1",), ("c2",)]])
        repo = BaseRepository(FakeConnection(cursor))

        rows = repo._tenant_select("customers", ("customer_id",), "tenant-a")

        assert rows == [("c1",), ("c2",)]


class TestTenantSelectOne:
    def test_returns_none_when_no_rows(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = BaseRepository(FakeConnection(cursor))

        assert repo._tenant_select_one("customers", ("customer_id",), "tenant-a") is None

    def test_returns_first_row(self) -> None:
        cursor = FakeCursor(fetchall_results=[[("c1",)]])
        repo = BaseRepository(FakeConnection(cursor))

        assert repo._tenant_select_one("customers", ("customer_id",), "tenant-a") == ("c1",)


class TestTenantUpdate:
    def test_refuses_unscoped_update(self) -> None:
        repo = BaseRepository(FakeConnection())

        with pytest.raises(ValueError, match="extra_where is required"):
            repo._tenant_update("customers", ("name",), ("New Name",), "tenant-a", extra_where="", extra_params=())

    def test_tenant_id_scopes_update(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = BaseRepository(conn)

        repo._tenant_update(
            "customers",
            ("name",),
            ("New Name",),
            "tenant-a",
            extra_where="customer_id = %s",
            extra_params=("cust-1",),
        )

        sql, params = cursor.executed[0]
        assert "SET name = %s WHERE tenant_id = %s AND customer_id = %s" in sql
        assert params == ("New Name", "tenant-a", "cust-1")
        assert conn.commit_count == 1

    def test_returns_rowcount(self) -> None:
        cursor = FakeCursor(rowcount=3)
        repo = BaseRepository(FakeConnection(cursor))

        affected = repo._tenant_update("customers", ("name",), ("x",), "tenant-a", extra_where="1=1", extra_params=())

        assert affected == 3


class TestBaseRepositoryCircuitBreaker:
    """Sprint-016: BaseRepository's optional breaker guards Postgres queries (V3 Ch14 §14.2)."""

    def test_execute_succeeds_without_breaker(self) -> None:
        cursor = FakeCursor(fetchall_results=[[("row",)]])
        repo = BaseRepository(FakeConnection(cursor))

        rows = repo._tenant_select("customers", ("customer_id",), "tenant-a")

        assert rows == [("row",)]

    def test_execute_opens_breaker_on_repeated_failure(self) -> None:
        class BrokenConnection:
            def cursor(self) -> FakeCursor:
                raise ConnectionError("postgres down")

            def commit(self) -> None:
                pass

        breaker = CircuitBreaker("postgres", CircuitBreakerConfig(failure_threshold=1))
        repo = BaseRepository(BrokenConnection(), breaker=breaker)

        with pytest.raises(ConnectionError):
            repo._tenant_select("customers", ("customer_id",), "tenant-a")

        assert breaker.state == CircuitState.OPEN

        with pytest.raises(CircuitOpenError):
            repo._tenant_select("customers", ("customer_id",), "tenant-a")


class TestExecuteRollsBackOnFailure:
    """A failed query must not poison every later request on the same
    connection -- live in dev, a duplicate-key error during invitation
    acceptance left the shared long-lived connection in Postgres's aborted-
    transaction state, and the next unrelated request failed with
    ``InFailedSqlTransaction`` even though it had nothing to do with the
    original error. _execute() must roll back before propagating."""

    def test_rolls_back_the_connection_on_execute_failure(self) -> None:
        cursor = FakeCursor(raises_on_execute=ValueError("duplicate key value"))
        conn = FakeConnection(cursor)
        repo = BaseRepository(conn)

        with pytest.raises(ValueError, match="duplicate key value"):
            repo._execute("INSERT INTO users (email) VALUES (%s)", ("a@b.com",))

        assert conn.rollback_count == 1

    def test_connection_is_usable_again_after_a_failed_query(self) -> None:
        cursor = FakeCursor(raises_on_execute=ValueError("duplicate key value"), fetchall_results=[[("row",)]])
        conn = FakeConnection(cursor)
        repo = BaseRepository(conn)

        with pytest.raises(ValueError):
            repo._execute("INSERT INTO users (email) VALUES (%s)", ("a@b.com",))

        # A completely unrelated later request on the same connection must
        # still succeed -- this is the exact failure mode that shipped.
        rows = repo._tenant_select("customers", ("customer_id",), "tenant-a")
        assert rows == [("row",)]
