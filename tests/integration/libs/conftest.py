"""Shared fixtures for tests/integration/libs/ (Sprint-015).

Mirrors ``tests/integration/repositories/conftest.py``'s pattern: ensures the
full Alembic schema (through the Sprint-015 ``snapshots``/``recovery_log``
revision) exists on POSTGRES_DSN before any Postgres-backed test in this
directory runs, then hands out a real psycopg2 connection per test.

Skipped entirely when POSTGRES_DSN is unset (see tests.integration.conftest).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.fixtures.db import POSTGRES_DSN


@pytest.fixture(scope="session")
def _migrated_schema() -> None:
    """Run `alembic upgrade head` once per test session against POSTGRES_DSN."""
    import os

    from alembic import command
    from alembic.config import Config

    os.environ["POSTGRES_DSN"] = POSTGRES_DSN
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@pytest.fixture
def pg_conn(_migrated_schema: None) -> Iterator[Any]:
    """Real Postgres connection with the full schema (incl. Sprint-015) applied."""
    import psycopg2

    conn = psycopg2.connect(POSTGRES_DSN)
    yield conn
    conn.close()
