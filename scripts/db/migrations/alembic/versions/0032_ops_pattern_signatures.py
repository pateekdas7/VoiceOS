"""ADR-006 Sec 3.5/7: ops_pattern_signatures (recurring-issue fingerprints)

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-25

Plain-code hash/counter table backing ADR-006 Sec 3.5's "historical
learning" design: a deterministic fingerprint (hash of category +
affected_components + root_cause_summary) with a first_seen/last_seen/
occurrence_count. This is NOT model state or a learned representation --
no ML/retraining is involved, it is the concrete mechanism for "recurring
issue detection" the founder asked be supported without any autonomous
learning (ADR-006 Sec 3.5).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_pattern_signatures (
            signature_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID        REFERENCES tenants (tenant_id) ON DELETE CASCADE,
            fingerprint_hash    TEXT        NOT NULL,
            category            TEXT        NOT NULL,
            affected_components JSONB       NOT NULL DEFAULT '[]',
            root_cause_summary  TEXT        NOT NULL,
            first_seen          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            occurrence_count    INT         NOT NULL DEFAULT 1 CHECK (occurrence_count >= 1)
        );
        """
    )
    # A fingerprint is unique per tenant scope: two different tenants
    # experiencing a "similar" issue never merge into one cross-tenant
    # pattern record (ADR-006 Sec 10.2). NULL tenant_id (platform-wide
    # infra patterns) is likewise kept distinct per Postgres's
    # NULL-not-equal-NULL semantics under a plain UNIQUE index, so two
    # platform-wide fingerprints with the same hash still correctly collide
    # (both NULL) while tenant-scoped ones only collide within their tenant.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ops_pattern_signatures_fingerprint "
        "ON ops_pattern_signatures (fingerprint_hash, COALESCE(tenant_id, '00000000-0000-0000-0000-000000000000'));"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_ops_pattern_signatures_tenant_last_seen ON ops_pattern_signatures (tenant_id, last_seen DESC);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops_pattern_signatures CASCADE;")
