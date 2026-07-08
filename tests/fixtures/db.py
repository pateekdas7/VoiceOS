"""PostgreSQL test fixtures for VoiceOS.

TestPostgres connects to a real Postgres instance identified by the POSTGRES_DSN
environment variable, applies Sprint-002 migrations idempotently, and exposes
a thin connection wrapper for integration tests.

When POSTGRES_DSN is not set all callers raise or are skipped via the
requires_postgres marker defined in tests/integration/conftest.py.

Architecture: V6 Ch9 (Testing Standards); DocSuite-08.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Environment config
# ---------------------------------------------------------------------------

POSTGRES_DSN: str = os.environ.get("POSTGRES_DSN", "")

MIGRATIONS_DIR: Path = Path(__file__).parent.parent.parent / "scripts" / "db" / "migrations"

MIGRATION_FILES: list[str] = [
    "001_customers_and_parties.sql",
    "002_loan_accounts_and_emi.sql",
    "003_promises_to_pay.sql",
    "004_consents.sql",
    "005_idempotency_keys.sql",
    "006_audit_log.sql",
    "007_tenants_and_orgs.sql",
    "008_users_and_roles.sql",
    "009_campaigns.sql",
    "010_billing_and_usage.sql",
    "011_relationship_memory.sql",  # Sprint-010: RelationshipMemoryStore backing table
]


# ---------------------------------------------------------------------------
# TestPostgres
# ---------------------------------------------------------------------------


class TestPostgres:
    """Postgres test helper that manages a connection lifecycle.

    Connects to POSTGRES_DSN, applies Sprint-002 migrations idempotently,
    and provides cursor access for integration assertions.

    Usage (context manager — preferred):
        with TestPostgres() as db:
            db.apply_migrations()
            cur = db.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)

    Usage (manual):
        db = TestPostgres()
        db.connect()
        db.apply_migrations()
        db.close()
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn: str = dsn if dsn is not None else POSTGRES_DSN
        self._conn: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open a connection to the database.

        Raises:
            ImportError: If psycopg2 is not installed.
            psycopg2.OperationalError: If the connection fails.
        """
        import psycopg2

        self._conn = psycopg2.connect(self._dsn)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> TestPostgres:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def apply_migrations(self) -> None:
        """Apply all Sprint-002 DDL migration files idempotently.

        Raises:
            RuntimeError: If connect() has not been called.
            RuntimeError: If any migration fails.
        """
        if self._conn is None:
            raise RuntimeError("Not connected — call connect() or use as a context manager.")
        cursor = self._conn.cursor()
        for fname in MIGRATION_FILES:
            path = MIGRATIONS_DIR / fname
            sql = path.read_text(encoding="utf-8")
            try:
                cursor.execute(sql)
                self._conn.commit()
            except Exception as exc:
                self._conn.rollback()
                raise RuntimeError(f"Migration '{fname}' failed: {exc}") from exc

    def cursor(self) -> Any:
        """Return a database cursor.

        Raises:
            RuntimeError: If connect() has not been called.
        """
        if self._conn is None:
            raise RuntimeError("Not connected — call connect() or use as a context manager.")
        return self._conn.cursor()

    def commit(self) -> None:
        """Commit the current transaction."""
        if self._conn is not None:
            self._conn.commit()

    def rollback(self) -> None:
        """Roll back the current transaction."""
        if self._conn is not None:
            self._conn.rollback()
