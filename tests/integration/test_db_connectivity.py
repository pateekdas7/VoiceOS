"""Integration tests: PostgreSQL connectivity, migrations, and round-trip queries.

Requires POSTGRES_DSN environment variable. Skipped automatically when absent.

Run:
    POSTGRES_DSN=postgresql://voiceos:voiceos_dev_pw@localhost:5432/voiceos_dev \\
    pytest tests/integration/test_db_connectivity.py -v

Architecture: V6 Ch9; DocSuite-08; Sprint-003 AC.
"""

from __future__ import annotations

import uuid

from tests.fixtures.db import MIGRATION_FILES, MIGRATIONS_DIR, TestPostgres
from tests.integration.conftest import requires_postgres


@requires_postgres
class TestPostgresConnectivity:
    """PostgreSQL connection, migration, and basic query integration tests."""

    def test_connect_and_execute_select_one(self) -> None:
        """Can open a connection and execute a trivial query."""
        with TestPostgres() as db:
            cur = db.cursor()
            cur.execute("SELECT 1 AS result")
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1

    def test_connect_and_execute_timestamp(self) -> None:
        """Database clock is accessible and returns a non-null timestamp."""
        with TestPostgres() as db:
            cur = db.cursor()
            cur.execute("SELECT NOW()")
            row = cur.fetchone()
            assert row is not None
            assert row[0] is not None

    def test_apply_migrations_without_error(self) -> None:
        """All Sprint-002 migrations apply without raising an exception."""
        with TestPostgres() as db:
            db.apply_migrations()

    def test_migrations_are_idempotent(self) -> None:
        """Running all migrations twice does not raise errors."""
        with TestPostgres() as db:
            db.apply_migrations()
            db.apply_migrations()

    def test_core_tables_exist_after_migration(self) -> None:
        """Expected tables are created by the Sprint-002 migrations."""
        expected = {
            "customers",
            "loan_accounts",
            "promises_to_pay",
            "consents",
            "idempotency_keys",
            "audit_log",
            "tenants",
            "users",
            "campaigns",
            "billing_subscriptions",
        }
        with TestPostgres() as db:
            db.apply_migrations()
            cur = db.cursor()
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            existing = {row[0] for row in cur.fetchall()}
            missing = expected - existing
            assert not missing, f"Tables missing after migrations: {missing}"

    def test_round_trip_temp_table(self) -> None:
        """Can insert a row into a temporary table and read it back."""
        with TestPostgres() as db:
            cur = db.cursor()
            cur.execute("CREATE TEMP TABLE IF NOT EXISTS _test_rtt (id UUID PRIMARY KEY, val TEXT NOT NULL)")
            test_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO _test_rtt (id, val) VALUES (%s, %s)",
                (test_id, "voiceos-sprint-003"),
            )
            db.commit()
            cur.execute("SELECT val FROM _test_rtt WHERE id = %s", (test_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "voiceos-sprint-003"

    def test_migration_files_are_all_present(self) -> None:
        """Every migration file referenced by TestPostgres exists on disk."""
        for fname in MIGRATION_FILES:
            path = MIGRATIONS_DIR / fname
            assert path.exists(), f"Migration file missing: {fname}"

    def test_disconnect_cleans_up(self) -> None:
        """Connection is cleaned up after context manager exits."""
        db = TestPostgres()
        db.connect()
        db.close()
        # second close must be a no-op (not raise)
        db.close()
