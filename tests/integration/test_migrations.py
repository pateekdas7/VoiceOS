"""Integration tests for Sprint-002 DB schema migrations.

These tests validate SQL DDL file structure (always) and execute the
migrations against a real PostgreSQL instance when POSTGRES_DSN is set.

To run against Postgres:
    POSTGRES_DSN=postgresql://user:pass@localhost:5432/voiceos_test pytest tests/integration/

Architecture: V6 Ch4 (Testing); Sprint-002 AC-5.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "scripts" / "db" / "migrations"
MONGODB_DIR = Path(__file__).parent.parent.parent / "scripts" / "db" / "mongodb"

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="POSTGRES_DSN not set — skipping live Postgres integration tests",
)

# Expected SQL migration files in order
EXPECTED_SQL_FILES = [
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
]

EXPECTED_MONGODB_FILES = [
    "call_lineage_indexes.json",
    "call_transcripts_indexes.json",
    "decision_envelopes_indexes.json",
    "response_plans_indexes.json",
]


# ---------------------------------------------------------------------------
# Static validation (always runs — no Postgres required)
# ---------------------------------------------------------------------------


class TestSQLFileStructure:
    def test_all_sql_files_exist(self) -> None:
        for fname in EXPECTED_SQL_FILES:
            path = MIGRATIONS_DIR / fname
            assert path.exists(), f"Missing migration file: {fname}"

    def test_sql_files_are_non_empty(self) -> None:
        for fname in EXPECTED_SQL_FILES:
            path = MIGRATIONS_DIR / fname
            content = path.read_text(encoding="utf-8").strip()
            assert content, f"Migration file is empty: {fname}"

    def test_sql_files_have_create_table_statement(self) -> None:
        for fname in EXPECTED_SQL_FILES:
            path = MIGRATIONS_DIR / fname
            content = path.read_text(encoding="utf-8")
            assert "CREATE TABLE" in content.upper(), f"Migration {fname} has no CREATE TABLE statement"

    def test_sql_files_use_if_not_exists(self) -> None:
        """All CREATE TABLE statements must be idempotent."""
        for fname in EXPECTED_SQL_FILES:
            path = MIGRATIONS_DIR / fname
            content = path.read_text(encoding="utf-8")
            # Count CREATE TABLE occurrences
            ct_count = len(re.findall(r"CREATE\s+TABLE\s+", content, re.IGNORECASE))
            # Count CREATE TABLE IF NOT EXISTS occurrences
            ctine_count = len(
                re.findall(
                    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+",
                    content,
                    re.IGNORECASE,
                )
            )
            assert ct_count == ctine_count, (
                f"Migration {fname}: not all CREATE TABLE statements use IF NOT EXISTS "
                f"(found {ct_count} CREATE TABLE, {ctine_count} with IF NOT EXISTS)"
            )

    def test_sql_files_have_indexes(self) -> None:
        for fname in EXPECTED_SQL_FILES:
            path = MIGRATIONS_DIR / fname
            content = path.read_text(encoding="utf-8")
            assert "CREATE INDEX" in content.upper(), f"Migration {fname} has no CREATE INDEX statement"

    def test_sql_files_numbered_sequentially(self) -> None:
        for i, fname in enumerate(EXPECTED_SQL_FILES, start=1):
            prefix = f"{i:03d}_"
            assert fname.startswith(prefix), f"Migration {fname} should start with {prefix}"


class TestMongoDBIndexFiles:
    def test_all_mongodb_files_exist(self) -> None:
        for fname in EXPECTED_MONGODB_FILES:
            path = MONGODB_DIR / fname
            assert path.exists(), f"Missing MongoDB index file: {fname}"

    def test_mongodb_files_are_valid_json(self) -> None:
        for fname in EXPECTED_MONGODB_FILES:
            path = MONGODB_DIR / fname
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                pytest.fail(f"MongoDB index file {fname} is not valid JSON: {exc}")
            assert isinstance(data, list), f"{fname}: top-level value must be a list"

    def test_mongodb_files_have_collection_field(self) -> None:
        for fname in EXPECTED_MONGODB_FILES:
            path = MONGODB_DIR / fname
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                assert "collection" in entry, f"{fname}: missing 'collection' field in entry"

    def test_mongodb_files_have_indexes_field(self) -> None:
        for fname in EXPECTED_MONGODB_FILES:
            path = MONGODB_DIR / fname
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                assert "indexes" in entry, f"{fname}: missing 'indexes' field in entry"
                assert isinstance(entry["indexes"], list), f"{fname}: 'indexes' must be a list"

    def test_mongodb_indexes_have_name_and_keys(self) -> None:
        for fname in EXPECTED_MONGODB_FILES:
            path = MONGODB_DIR / fname
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                for idx in entry["indexes"]:
                    assert "name" in idx, f"{fname}: index missing 'name' field"
                    assert "keys" in idx, f"{fname}: index {idx.get('name')} missing 'keys' field"


# ---------------------------------------------------------------------------
# Live Postgres integration tests (skipped without POSTGRES_DSN)
# ---------------------------------------------------------------------------


@requires_postgres
class TestLiveMigrations:
    """Runs all SQL migration files against a real Postgres instance.

    Requires POSTGRES_DSN environment variable, e.g.:
        POSTGRES_DSN=postgresql://voiceos:secret@localhost:5432/voiceos_test

    Each test run executes migrations idempotently (IF NOT EXISTS) so the
    tests can be repeated without dropping the schema between runs.
    """

    def _get_connection(self) -> Any:
        import psycopg2

        return psycopg2.connect(POSTGRES_DSN)

    def test_migrations_run_without_error(self) -> None:
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            for fname in EXPECTED_SQL_FILES:
                path = MIGRATIONS_DIR / fname
                sql = path.read_text(encoding="utf-8")
                try:
                    cursor.execute(sql)
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    pytest.fail(f"Migration {fname} failed: {exc}")
        finally:
            conn.close()

    def test_migrations_are_idempotent(self) -> None:
        """Running migrations twice must not raise an error."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            for fname in EXPECTED_SQL_FILES:
                path = MIGRATIONS_DIR / fname
                sql = path.read_text(encoding="utf-8")
                try:
                    cursor.execute(sql)
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    pytest.fail(f"Migration {fname} failed on second run (not idempotent): {exc}")
        finally:
            conn.close()

    def test_core_tables_exist_after_migration(self) -> None:
        expected_tables = [
            "customers",
            "customer_contacts",
            "customer_addresses",
            "loan_accounts",
            "emi_entries",
            "promises_to_pay",
            "settlements",
            "callback_requests",
            "consents",
            "consent_records",
            "idempotency_keys",
            "audit_log",
            "tenants",
            "organizations",
            "business_units",
            "branches",
            "roles",
            "users",
            "role_assignments",
            "campaigns",
            "billing_subscriptions",
            "usage_events",
            "invoices",
        ]
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                """,
            )
            existing = {row[0] for row in cursor.fetchall()}
            missing = set(expected_tables) - existing
            assert not missing, f"Tables not created after migrations: {missing}"
        finally:
            conn.close()
