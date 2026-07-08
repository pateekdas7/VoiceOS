"""Shared fixtures for repository integration tests (Sprint-014).

Ensures the complete Sprint-014 schema (all 13 Alembic revisions, including
the additive columns/constraints/trigger layered onto the Sprint-002 tables)
exists on POSTGRES_DSN before any repository integration test runs, then
hands out a real psycopg2 connection per test.

``tenant_id``, ``customer_id``, etc. are UUID-typed columns in Postgres, so
tests generate real ``uuid.uuid4()`` values (not string prefixes) and clean
up their own rows by exact UUID match in a test-local teardown — a LIKE-based
prefix convention (as used for the TEXT-typed relationship_memory.customer_id
in Sprint-010's integration tests) does not apply to UUID columns.

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
    """Real Postgres connection with the full Sprint-014 schema applied.

    No automatic row cleanup here — each test generates its own UUID-scoped
    tenant/resource IDs and deletes exactly those rows in its own teardown.
    """
    import psycopg2

    conn = psycopg2.connect(POSTGRES_DSN)
    yield conn
    conn.close()
