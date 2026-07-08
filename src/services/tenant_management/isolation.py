"""IsolationProfileManager — provisions the tenant's data isolation tier (V5 Ch2.3).

Three profiles (``src.libs.contracts.models.tenant.IsolationProfile``):

- ``SHARED``: shared schema, ``tenant_id`` column on every table (default;
  already the AR-8 convention every repository enforces via
  ``BaseRepository._tenant_select`` — no DDL needed at provisioning time).
- ``DEDICATED_SCHEMA``: a real per-tenant Postgres schema is created.
- ``DEDICATED_CLUSTER``: a real per-tenant Postgres database is created
  (requires a ``CREATEDB``-privileged connection in ``autocommit`` mode —
  ``CREATE DATABASE`` cannot run inside a transaction block).

Isolation level is fixed at provisioning time (Sprint-021.md: "cannot
change without migration") — this manager only ever provisions forward,
never re-classifies an existing tenant.

Architecture: V5 Ch2 (Multi-Tenant Architecture) §2.3 (Isolation Profiles).
"""

from __future__ import annotations

import re
from typing import Any

from src.libs.contracts.models.tenant import IsolationProfile

_SAFE_TENANT_ID = re.compile(r"^[0-9a-fA-F-]{1,64}$")


class UnsafeTenantIdentifierError(Exception):
    """Raised when a tenant_id cannot be safely embedded in a DDL identifier."""


def _schema_name(tenant_id: str) -> str:
    return f"tenant_{tenant_id.replace('-', '_')}"


def _database_name(tenant_id: str) -> str:
    return f"voiceos_tenant_{tenant_id.replace('-', '_')}"


class IsolationProfileManager:
    """Provisions the Postgres-level isolation boundary for a tenant."""

    def provision(self, tenant_id: str, profile: IsolationProfile, conn: Any) -> str:
        """Provision ``profile`` for ``tenant_id``. Returns the schema/database name (or ``""`` for SHARED).

        Args:
            conn: A cursor-yielding connection. For ``DEDICATED_CLUSTER`` this
                must be an autocommit connection with ``CREATEDB`` privilege —
                ``CREATE DATABASE`` cannot execute inside a transaction block.
        """
        if not _SAFE_TENANT_ID.match(tenant_id):
            raise UnsafeTenantIdentifierError(f"tenant_id is not a safe DDL identifier: {tenant_id!r}")

        if profile is IsolationProfile.SHARED:
            return ""
        if profile is IsolationProfile.DEDICATED_SCHEMA:
            name = _schema_name(tenant_id)
            cur = conn.cursor()
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {name}")
            conn.commit()
            return name
        # DEDICATED_CLUSTER
        name = _database_name(tenant_id)
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM pg_database WHERE datname = '{name}'")
        if cur.fetchone() is None:
            cur.execute(f"CREATE DATABASE {name}")
        return name
