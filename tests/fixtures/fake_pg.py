"""Lightweight psycopg2-compatible test double for repository unit tests.

Real behavioral verification against a live Postgres happens in
tests/integration/repositories/ (requires_postgres, run on the CPU node in
Phase 2). This fake exists only to unit-test the SQL/parameter-building logic
in src/libs/repositories/ locally without a database — recording every
executed statement so tests can assert tenant scoping and queuing canned
fetchone()/fetchall() results to drive control flow (e.g. idempotency
conflict paths).

Architecture: V6 Ch9 (Testing Standards).
"""

from __future__ import annotations

from typing import Any


class FakeCursor:
    """Records every execute() call; replays queued fetchone/fetchall results."""

    def __init__(
        self,
        fetchone_results: list[Any] | None = None,
        fetchall_results: list[list[tuple[Any, ...]]] | None = None,
        rowcount: int = 1,
    ) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._fetchone_queue: list[Any] = list(fetchone_results or [])
        self._fetchall_queue: list[list[tuple[Any, ...]]] = list(fetchall_results or [])
        self.rowcount = rowcount

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.executed.append((sql, tuple(params)))

    def fetchone(self) -> Any:
        return self._fetchone_queue.pop(0) if self._fetchone_queue else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._fetchall_queue.pop(0) if self._fetchall_queue else []


class FakeConnection:
    """A single-cursor fake connection — every cursor() call returns the same recorder."""

    def __init__(self, cursor: FakeCursor | None = None) -> None:
        self.cursor_obj = cursor if cursor is not None else FakeCursor()
        self.commit_count = 0

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commit_count += 1
