"""BaseRepository — tenant-scoped Postgres query helpers.

Every concrete repository composes these helpers instead of writing raw
``cursor.execute`` calls directly, so the tenant isolation invariant (AR-8:
every resource access is scoped to tenant_id) is enforced in exactly one
place: ``tenant_id`` is always the first WHERE condition, never optional.

The connection object must expose ``cursor()`` and ``commit()`` — matching
both a real psycopg2 connection and test doubles (mirrors the
RelationshipMemoryStore precedent from Sprint-010).

Architecture: V6 Ch7 (Data Modeling Standards); AR-8 (tenant isolation).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.libs.circuit_breaker.breaker import CircuitBreaker


class BaseRepository:
    """Shared tenant-scoped query helpers for all domain repositories."""

    def __init__(self, conn: Any, breaker: CircuitBreaker | None = None) -> None:
        """
        Args:
            conn: A psycopg2 connection (or compatible test double) exposing
                ``cursor()`` and ``commit()``.
            breaker: Optional CircuitBreaker guarding Postgres queries
                (Sprint-016, V3 Ch14 §14.2). None (default) preserves
                pre-Sprint-016 behavior.
        """
        self._conn = conn
        self._breaker = breaker

    # ------------------------------------------------------------------
    # Low-level primitives
    # ------------------------------------------------------------------

    def _cursor(self) -> Any:
        return self._conn.cursor()

    def _commit(self) -> None:
        self._conn.commit()

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Execute a raw statement and return the cursor (caller commits).

        Routed through the optional CircuitBreaker (Sprint-016) — every
        concrete repository funnels through this one primitive, so wiring
        the breaker here covers every domain repository's Postgres calls.

        Rolls back on any failure: Postgres aborts the entire transaction
        on the first error a connection hits, and refuses every further
        statement (``InFailedSqlTransaction``) until a rollback clears it.
        Every repository shares one long-lived connection per process (no
        pool, no per-request scope — see WebSessionMiddleware), so without
        this, one bad query here poisons every other request the process
        serves until it's restarted — not a hypothetical: this is exactly
        what happened live in dev (a duplicate-key error on invitation
        acceptance left the connection stuck, and the next unrelated
        request failed with the same aborted-transaction error).
        """

        def _do_execute() -> Any:
            cur = self._cursor()
            try:
                cur.execute(sql, tuple(params))
            except Exception:
                self._conn.rollback()
                raise
            return cur

        if self._breaker is not None:
            return self._breaker.call_sync(_do_execute)
        return _do_execute()

    # ------------------------------------------------------------------
    # Tenant-scoped query builders
    # ------------------------------------------------------------------

    def _tenant_select(
        self,
        table: str,
        columns: Sequence[str],
        tenant_id: str,
        *,
        extra_where: str = "",
        extra_params: Sequence[Any] = (),
        order_by: str = "",
        limit: int | None = None,
    ) -> list[tuple[Any, ...]]:
        """SELECT rows from ``table`` scoped to ``tenant_id``.

        ``tenant_id = %s`` is always the first (and mandatory) WHERE
        condition — this is the mechanical tenant-isolation enforcement
        point every repository method funnels through.

        Args:
            table: Table name (repository-controlled constant, never
                user input).
            columns: Column names to select.
            tenant_id: Tenant scope — always applied.
            extra_where: Additional SQL condition (ANDed after tenant_id),
                using ``%s`` placeholders.
            extra_params: Parameters for ``extra_where``, in order.
            order_by: Optional ORDER BY clause body (no ``ORDER BY`` prefix).
            limit: Optional row limit.

        Returns:
            Raw row tuples as returned by the cursor.
        """
        cols = ", ".join(columns)
        sql = f"SELECT {cols} FROM {table} WHERE tenant_id = %s"
        params: list[Any] = [tenant_id]
        if extra_where:
            sql += f" AND {extra_where}"
            params.extend(extra_params)
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        cur = self._execute(sql, params)
        result: list[tuple[Any, ...]] = cur.fetchall()
        return result

    def _tenant_select_one(
        self,
        table: str,
        columns: Sequence[str],
        tenant_id: str,
        *,
        extra_where: str = "",
        extra_params: Sequence[Any] = (),
    ) -> tuple[Any, ...] | None:
        """SELECT a single row scoped to ``tenant_id``, or ``None`` if absent."""
        rows = self._tenant_select(
            table,
            columns,
            tenant_id,
            extra_where=extra_where,
            extra_params=extra_params,
            limit=1,
        )
        return rows[0] if rows else None

    def _tenant_update(
        self,
        table: str,
        set_columns: Sequence[str],
        set_values: Sequence[Any],
        tenant_id: str,
        *,
        extra_where: str,
        extra_params: Sequence[Any],
    ) -> int:
        """UPDATE rows scoped to ``tenant_id``; returns the affected row count.

        ``extra_where`` is required (never blank) — an unconditional
        tenant-wide UPDATE is never a valid repository operation.
        """
        if not extra_where:
            raise ValueError("extra_where is required for _tenant_update — refusing an unscoped bulk update")
        set_clause = ", ".join(f"{col} = %s" for col in set_columns)
        sql = f"UPDATE {table} SET {set_clause} WHERE tenant_id = %s AND {extra_where}"
        params = [*set_values, tenant_id, *extra_params]
        cur = self._execute(sql, params)
        self._commit()
        rowcount: int = cur.rowcount
        return rowcount
