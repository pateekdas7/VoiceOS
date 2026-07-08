"""Sprint-020: audit_log hash chain + pii_tokens persistent token vault

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-05

Additive, expand-only (matches the Sprint-014/015/016/017/019 precedent —
never drop/rename a column already in production):

  - ``audit_log``: adds ``seq`` (monotonic per-row ordering, independent of
    ``recorded_at`` clock precision), ``prev_hash``/``hash`` (SHA-256 hash
    chain, V4 Ch11 §11.6/§11.12 "each event carries prev_hash forming a hash
    chain"). Existing pre-Sprint-020 rows (if any) get a ``seq`` value via
    the new column's identity/serial default but keep ``prev_hash``/``hash``
    NULL — the hash chain begins at the first row appended by the new
    ``AuditRepository.append()`` after this migration, per tenant; it is
    intentionally not retrofitted onto historical rows (there is no prior
    hash to chain from). ``AuditVerifier.verify_chain()`` treats the first
    non-NULL-hash row per tenant as that tenant's chain genesis.
  - ``pii_tokens``: the persistent half of ``PIITokenizer`` (V4 Ch10 §10.9
    "the raw value lives in a token vault"); Redis provides the transient,
    session-TTL half (``src/libs/pii/tokenizer.py``).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS seq BIGSERIAL;")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS prev_hash TEXT;")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS hash TEXT;")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant_seq ON audit_log (tenant_id, seq);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pii_tokens (
            token           TEXT        PRIMARY KEY,
            tenant_id       UUID        NOT NULL,
            entity_type     TEXT        NOT NULL,
            value           TEXT        NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_pii_tokens_tenant ON pii_tokens (tenant_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pii_tokens CASCADE;")
    op.execute("DROP INDEX IF EXISTS idx_audit_tenant_seq;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS hash;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS prev_hash;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS seq;")
