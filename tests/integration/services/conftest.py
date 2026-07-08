"""Shared fixtures for services-layer integration tests needing real Postgres.

Mirrors ``tests/integration/repositories/conftest.py``'s ``pg_conn`` fixture
(same ``alembic upgrade head`` + per-test connection pattern) — duplicated
here rather than imported because pytest fixture discovery is directory-scoped,
not import-scoped.
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
    """Real Postgres connection with the full schema (including migration 0018) applied."""
    import psycopg2

    conn = psycopg2.connect(POSTGRES_DSN)
    yield conn
    conn.close()
