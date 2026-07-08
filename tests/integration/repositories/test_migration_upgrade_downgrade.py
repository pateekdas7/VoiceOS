"""Integration test: Alembic upgrade/downgrade cycle (Sprint-014 AC).

Required named test: test_migrations_upgrade_downgrade.

IMPORTANT — runs against a disposable *scratch* database, never against the
shared POSTGRES_DSN database used by the other repository integration tests
(and never against the CPU node's production `voiceos` database in Phase 2).
`alembic downgrade base` drops every Sprint-014 table via CASCADE — doing
that against a database other services depend on would be destructive. This
mirrors Sprint-014.md's own Phase 1 placement of this check under "mock
validation" against a throwaway `TestPostgres`-style database, not production.

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import pytest

from tests.integration.conftest import requires_postgres

_POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")


def _scratch_dsn(base_dsn: str) -> str:
    """Derive a disposable-database DSN from POSTGRES_DSN by swapping the dbname."""
    parsed = urlparse(base_dsn)
    return urlunparse(parsed._replace(path="/voiceos_migrations_scratch"))


def _maintenance_dsn(base_dsn: str) -> str:
    """DSN pointing at the always-present `postgres` maintenance database."""
    parsed = urlparse(base_dsn)
    return urlunparse(parsed._replace(path="/postgres"))


def _ensure_scratch_database_exists(base_dsn: str) -> None:
    import psycopg2

    conn = psycopg2.connect(_maintenance_dsn(base_dsn))
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = 'voiceos_migrations_scratch'")
        if cur.fetchone() is None:
            cur.execute("CREATE DATABASE voiceos_migrations_scratch")
    finally:
        conn.close()


@requires_postgres
class TestMigrationUpgradeDowngrade:
    def test_migrations_upgrade_downgrade(self) -> None:
        """Upgrade to head from empty, downgrade to base, upgrade to head again."""
        from alembic import command
        from alembic.config import Config

        _ensure_scratch_database_exists(_POSTGRES_DSN)
        scratch_dsn = _scratch_dsn(_POSTGRES_DSN)

        os.environ["POSTGRES_DSN"] = scratch_dsn
        cfg = Config("alembic.ini")

        # Upgrade from empty database to head — all 20 revisions apply cleanly.
        command.upgrade(cfg, "head")
        assert _current_revision(scratch_dsn) == "0026"
        assert _table_exists(scratch_dsn, "tenants")
        assert _table_exists(scratch_dsn, "usage_events")
        assert _table_exists(scratch_dsn, "snapshots")
        assert _table_exists(scratch_dsn, "recovery_log")
        assert _table_exists(scratch_dsn, "policies")
        assert _table_exists(scratch_dsn, "data_encryption_keys")
        assert _table_exists(scratch_dsn, "data_erasure_certificates")
        assert _table_exists(scratch_dsn, "pii_tokens")
        assert _table_exists(scratch_dsn, "invitations")
        assert _table_exists(scratch_dsn, "sso_config")
        assert _table_exists(scratch_dsn, "campaign_audiences")
        assert _table_exists(scratch_dsn, "campaign_results")
        assert _table_exists(scratch_dsn, "hitl_queue")
        assert _table_exists(scratch_dsn, "hitl_decisions")
        assert _table_exists(scratch_dsn, "analytics_daily")
        assert _table_exists(scratch_dsn, "dim_tenant", schema="bi_facts")
        assert _table_exists(scratch_dsn, "fact_daily", schema="bi_facts")
        assert _table_exists(scratch_dsn, "prompt_versions")
        assert _table_exists(scratch_dsn, "campaign_prompt_pins")
        assert _table_exists(scratch_dsn, "model_configs")
        assert _table_exists(scratch_dsn, "webhook_registrations")
        assert _table_exists(scratch_dsn, "webhook_deliveries")
        assert _table_exists(scratch_dsn, "api_keys")
        assert _table_exists(scratch_dsn, "webhook_delivery_attempts")
        assert _table_exists(scratch_dsn, "webhook_dead_letter_queue")
        assert _table_exists(scratch_dsn, "api_key_usage")
        assert _table_exists(scratch_dsn, "api_rate_limits")
        assert _table_exists(scratch_dsn, "feature_flags")
        assert _table_exists(scratch_dsn, "tenant_rollout_rings")
        assert _table_exists(scratch_dsn, "fleet_versions")
        assert _table_exists(scratch_dsn, "tenant_migrations")

        # Downgrade all the way back to base — every revision's downgrade() runs
        # in reverse order without error.
        command.downgrade(cfg, "base")
        assert _current_revision(scratch_dsn) is None
        assert not _table_exists(scratch_dsn, "tenants")
        assert not _table_exists(scratch_dsn, "usage_events")
        assert not _table_exists(scratch_dsn, "snapshots")
        assert not _table_exists(scratch_dsn, "recovery_log")
        assert not _table_exists(scratch_dsn, "policies")
        assert not _table_exists(scratch_dsn, "data_encryption_keys")
        assert not _table_exists(scratch_dsn, "data_erasure_certificates")
        assert not _table_exists(scratch_dsn, "pii_tokens")
        assert not _table_exists(scratch_dsn, "invitations")
        assert not _table_exists(scratch_dsn, "sso_config")
        assert not _table_exists(scratch_dsn, "campaign_audiences")
        assert not _table_exists(scratch_dsn, "campaign_results")
        assert not _table_exists(scratch_dsn, "hitl_queue")
        assert not _table_exists(scratch_dsn, "hitl_decisions")
        assert not _table_exists(scratch_dsn, "analytics_daily")
        assert not _table_exists(scratch_dsn, "dim_tenant", schema="bi_facts")
        assert not _table_exists(scratch_dsn, "fact_daily", schema="bi_facts")
        assert not _table_exists(scratch_dsn, "prompt_versions")
        assert not _table_exists(scratch_dsn, "campaign_prompt_pins")
        assert not _table_exists(scratch_dsn, "model_configs")
        assert not _table_exists(scratch_dsn, "webhook_registrations")
        assert not _table_exists(scratch_dsn, "webhook_deliveries")
        assert not _table_exists(scratch_dsn, "api_keys")
        assert not _table_exists(scratch_dsn, "webhook_delivery_attempts")
        assert not _table_exists(scratch_dsn, "webhook_dead_letter_queue")
        assert not _table_exists(scratch_dsn, "api_key_usage")
        assert not _table_exists(scratch_dsn, "api_rate_limits")
        assert not _table_exists(scratch_dsn, "feature_flags")
        assert not _table_exists(scratch_dsn, "tenant_rollout_rings")
        assert not _table_exists(scratch_dsn, "fleet_versions")
        assert not _table_exists(scratch_dsn, "tenant_migrations")

        # Upgrade again successfully — revisions are re-runnable from empty.
        command.upgrade(cfg, "head")
        assert _current_revision(scratch_dsn) == "0026"
        assert _table_exists(scratch_dsn, "tenants")
        assert _table_exists(scratch_dsn, "customers")
        assert _table_exists(scratch_dsn, "loan_accounts")
        assert _table_exists(scratch_dsn, "promises_to_pay")
        assert _table_exists(scratch_dsn, "consents")
        assert _table_exists(scratch_dsn, "idempotency_keys")
        assert _table_exists(scratch_dsn, "audit_log")
        assert _table_exists(scratch_dsn, "campaigns")
        assert _table_exists(scratch_dsn, "billing_subscriptions")
        assert _table_exists(scratch_dsn, "snapshots")
        assert _table_exists(scratch_dsn, "recovery_log")
        assert _table_exists(scratch_dsn, "policies")
        assert _table_exists(scratch_dsn, "data_encryption_keys")
        assert _table_exists(scratch_dsn, "data_erasure_certificates")
        assert _table_exists(scratch_dsn, "pii_tokens")
        assert _table_exists(scratch_dsn, "invitations")
        assert _table_exists(scratch_dsn, "sso_config")
        assert _table_exists(scratch_dsn, "campaign_audiences")
        assert _table_exists(scratch_dsn, "campaign_results")
        assert _table_exists(scratch_dsn, "hitl_queue")
        assert _table_exists(scratch_dsn, "hitl_decisions")


def _current_revision(dsn: str) -> str | None:
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version'")
        if cur.fetchone() is None:
            return None
        cur.execute("SELECT version_num FROM alembic_version")
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _table_exists(dsn: str, table_name: str, schema: str = "public") -> bool:
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (schema, table_name),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
