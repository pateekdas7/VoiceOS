"""Sprint-017: policies

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-04

New table (additive — no changes to existing schema):
  - ``policies``: which built-in Policy Engine rule_ids (V4 Ch4) are active at
    which scope (global / tenant / campaign). Rule *definitions* (conditions/
    effects) are Python code in ``src/services/policy_engine/packs/``; this
    table only stores rule *activation*, the Postgres fallback tier consulted
    by ``PolicyRepository`` on a Redis cache miss (V4 Ch4 §4.8 "Compiled
    policy cache").

``tenant_id``/``campaign_id`` are deliberately nullable (NULL denotes global
scope) and NOT a FK to ``tenants``/``campaigns`` — same convention as
``snapshots``/``recovery_log`` (migration 0014): the Policy Engine is a
Tier-1 reliability/compliance primitive (BUILD_ORDER.md) and must not
hard-depend on tenant management, which is not functionally complete until
Sprint-021.

The unique index uses ``COALESCE(..., <nil-uuid>)`` so a global-scope row
(``tenant_id``/``campaign_id`` both NULL) is still subject to a uniqueness
constraint on ``policy_id`` — Postgres treats NULLs as distinct under a plain
UNIQUE constraint, which would otherwise allow duplicate global rows.
``PolicyRepository.upsert_policy()``'s ``ON CONFLICT`` clause matches this
exact expression.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS policies (
            id              BIGSERIAL   PRIMARY KEY,
            policy_id       TEXT        NOT NULL,
            pack            TEXT        NOT NULL,
            scope           TEXT        NOT NULL,
            tenant_id       UUID        NULL,
            campaign_id     UUID        NULL,
            active          BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_policies_scope_identity
        ON policies (policy_id, scope, COALESCE(tenant_id, '{_NIL_UUID}'), COALESCE(campaign_id, '{_NIL_UUID}'));
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_policies_scope ON policies (scope, active);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_policies_tenant_id ON policies (tenant_id) WHERE tenant_id IS NOT NULL;")

    add_enum_check("policies", "ck_policies_scope_enum", "scope", ("global", "tenant", "campaign"))


def downgrade() -> None:
    drop_check("policies", "ck_policies_scope_enum")
    op.execute("DROP TABLE IF EXISTS policies CASCADE;")
