"""Sprint-014: emi_entries, dpd_records (002_loan_accounts_and_emi.sql remainder)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS emi_entries (
            emi_id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id) ON DELETE CASCADE,
            tenant_id               UUID        NOT NULL,
            instalment_number       INT         NOT NULL CHECK (instalment_number >= 1),
            due_date                DATE        NOT NULL,
            principal_minor         BIGINT      NOT NULL CHECK (principal_minor >= 0),
            interest_minor          BIGINT      NOT NULL CHECK (interest_minor >= 0),
            total_minor             BIGINT      NOT NULL CHECK (total_minor >= 0),
            paid_minor              BIGINT      NOT NULL DEFAULT 0 CHECK (paid_minor >= 0),
            status                  TEXT        NOT NULL DEFAULT 'PENDING',
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT uq_emi_instalment UNIQUE (loan_account_id, instalment_number)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_emi_loan_account ON emi_entries (loan_account_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_emi_due_date ON emi_entries (tenant_id, due_date);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_emi_status ON emi_entries (tenant_id, status);")

    add_enum_check(
        "emi_entries",
        "ck_emi_entries_status_enum",
        "status",
        ("PENDING", "PAID", "PARTIALLY_PAID", "OVERDUE", "WAIVED"),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dpd_records (
            dpd_record_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id) ON DELETE CASCADE,
            tenant_id               UUID        NOT NULL,
            dpd                     INT         NOT NULL CHECK (dpd >= 0),
            as_of_date              DATE        NOT NULL,
            overdue_minor           BIGINT      NOT NULL CHECK (overdue_minor >= 0),
            currency                CHAR(3)     NOT NULL DEFAULT 'INR',
            recorded_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_dpd_loan_account ON dpd_records (loan_account_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_dpd_as_of_date ON dpd_records (tenant_id, as_of_date DESC);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dpd_records CASCADE;")
    drop_check("emi_entries", "ck_emi_entries_status_enum")
    op.execute("DROP TABLE IF EXISTS emi_entries CASCADE;")
