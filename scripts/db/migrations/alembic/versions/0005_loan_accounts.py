"""Sprint-014: loan_accounts (002_loan_accounts_and_emi.sql, loan_accounts part)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import add_enum_check, drop_check

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS loan_accounts (
            loan_account_id         TEXT        PRIMARY KEY,
            tenant_id               UUID        NOT NULL,
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
            product_type            TEXT        NOT NULL,
            disbursed_amount_minor  BIGINT      NOT NULL CHECK (disbursed_amount_minor >= 0),
            currency                CHAR(3)     NOT NULL DEFAULT 'INR',
            interest_rate_bps       INT         NOT NULL CHECK (interest_rate_bps >= 0),
            tenure_months           INT         NOT NULL CHECK (tenure_months >= 1),
            disbursement_date       DATE        NOT NULL,
            maturity_date           DATE        NOT NULL,
            status                  TEXT        NOT NULL DEFAULT 'ACTIVE',
            dpd                     INT         NOT NULL DEFAULT 0 CHECK (dpd >= 0),
            outstanding_principal_minor BIGINT  DEFAULT 0 CHECK (outstanding_principal_minor >= 0),
            outstanding_interest_minor  BIGINT  DEFAULT 0 CHECK (outstanding_interest_minor >= 0),
            outstanding_penalty_minor   BIGINT  DEFAULT 0 CHECK (outstanding_penalty_minor >= 0),
            outstanding_total_minor     BIGINT  DEFAULT 0 CHECK (outstanding_total_minor >= 0),
            outstanding_as_of       TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_loan_tenant_id ON loan_accounts (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_loan_customer_id ON loan_accounts (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_loan_status ON loan_accounts (tenant_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_loan_dpd ON loan_accounts (tenant_id, dpd);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_loan_tenant_account_number "
        "ON loan_accounts (tenant_id, loan_account_id);"
    )

    add_enum_check(
        "loan_accounts",
        "ck_loan_accounts_status_enum",
        "status",
        ("ACTIVE", "DELINQUENT", "NPA", "SETTLED", "WRITTEN_OFF", "CLOSED"),
    )


def downgrade() -> None:
    drop_check("loan_accounts", "ck_loan_accounts_status_enum")
    op.execute("DROP TABLE IF EXISTS loan_accounts CASCADE;")
