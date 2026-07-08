"""Sprint-014: promises_to_pay, settlements, callback_requests, escalation_records (003_promises_to_pay.sql)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from scripts.db.migrations.alembic._ddl_helpers import (
    add_enum_check,
    add_unique_if_missing,
    drop_check,
    drop_constraint,
)

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS promises_to_pay (
            ptp_id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL,
            call_id                 UUID        NOT NULL,
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
            loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id),
            promised_amount_minor   BIGINT      NOT NULL CHECK (promised_amount_minor >= 0),
            currency                CHAR(3)     NOT NULL DEFAULT 'INR',
            promise_date            TIMESTAMPTZ NOT NULL,
            status                  TEXT        NOT NULL DEFAULT 'PENDING',
            notes                   TEXT        NOT NULL DEFAULT '',
            recorded_at             TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL
        );
        """
    )
    # Expand: add nullable `idempotency_key` column (V6 Ch7 expand-contract — additive only).
    # Issued as a standalone ALTER rather than inline in the CREATE TABLE body above because
    # promises_to_pay already exists on any database provisioned by Sprint-002 — the inline form
    # would be silently skipped by IF NOT EXISTS there and the column would never be added.
    op.execute("ALTER TABLE promises_to_pay ADD COLUMN IF NOT EXISTS idempotency_key TEXT;")

    op.execute("CREATE INDEX IF NOT EXISTS idx_ptp_tenant_id ON promises_to_pay (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ptp_customer_id ON promises_to_pay (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ptp_loan_account ON promises_to_pay (loan_account_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ptp_call_id ON promises_to_pay (call_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ptp_status ON promises_to_pay (tenant_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ptp_promise_date ON promises_to_pay (tenant_id, promise_date);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ptp_tenant_loan ON promises_to_pay (tenant_id, loan_account_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ptp_tenant_customer ON promises_to_pay (tenant_id, customer_id);")

    # Idempotency key (Sprint-014 AC: PTP.create_idempotent). Column is nullable (older rows
    # predating this sprint have none) so a plain UNIQUE constraint — which treats NULLs as
    # distinct — is correct and additive.
    add_unique_if_missing("promises_to_pay", "uq_ptp_idempotency_key", ("idempotency_key",))

    add_enum_check(
        "promises_to_pay",
        "ck_ptp_status_enum",
        "status",
        ("PENDING", "KEPT", "BROKEN", "PARTIAL", "CANCELLED"),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS settlements (
            settlement_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL,
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
            loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id),
            settlement_amount_minor BIGINT      NOT NULL CHECK (settlement_amount_minor >= 0),
            waiver_amount_minor     BIGINT      NOT NULL DEFAULT 0 CHECK (waiver_amount_minor >= 0),
            currency                CHAR(3)     NOT NULL DEFAULT 'INR',
            offer_expiry            TIMESTAMPTZ NOT NULL,
            status                  TEXT        NOT NULL DEFAULT 'PROPOSED',
            proposed_at             TIMESTAMPTZ NOT NULL,
            updated_at              TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_settlement_tenant_id ON settlements (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_settlement_customer_id ON settlements (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_settlement_status ON settlements (tenant_id, status);")

    add_enum_check(
        "settlements",
        "ck_settlements_status_enum",
        "status",
        ("PROPOSED", "ACCEPTED", "REJECTED", "EXPIRED", "PAID"),
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS callback_requests (
            callback_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL,
            call_id                 UUID        NOT NULL,
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
            loan_account_id         TEXT        NOT NULL REFERENCES loan_accounts (loan_account_id),
            preferred_time          TIMESTAMPTZ NOT NULL,
            timezone                TEXT        NOT NULL DEFAULT 'Asia/Kolkata',
            phone_number            TEXT        NOT NULL,
            is_fulfilled            BOOLEAN     NOT NULL DEFAULT FALSE,
            recorded_at             TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_callback_tenant_id ON callback_requests (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_callback_customer_id ON callback_requests (customer_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_callback_preferred_time ON callback_requests (tenant_id, preferred_time);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_callback_is_fulfilled ON callback_requests (tenant_id, is_fulfilled);"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS escalation_records (
            escalation_id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               UUID        NOT NULL,
            call_id                 UUID        NOT NULL,
            customer_id             UUID        NOT NULL REFERENCES customers (customer_id),
            reason                  TEXT        NOT NULL,
            escalated_to            TEXT        NOT NULL DEFAULT '',
            escalated_at            TIMESTAMPTZ NOT NULL,
            resolved_at             TIMESTAMPTZ,
            resolution_notes        TEXT        NOT NULL DEFAULT ''
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_escalation_tenant_id ON escalation_records (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_escalation_customer_id ON escalation_records (customer_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_escalation_call_id ON escalation_records (call_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_escalation_resolved_at ON escalation_records (tenant_id, resolved_at);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS escalation_records CASCADE;")
    op.execute("DROP TABLE IF EXISTS callback_requests CASCADE;")
    drop_check("settlements", "ck_settlements_status_enum")
    op.execute("DROP TABLE IF EXISTS settlements CASCADE;")
    drop_check("promises_to_pay", "ck_ptp_status_enum")
    drop_constraint("promises_to_pay", "uq_ptp_idempotency_key")
    op.execute("ALTER TABLE promises_to_pay DROP COLUMN IF EXISTS idempotency_key;")
    op.execute("DROP TABLE IF EXISTS promises_to_pay CASCADE;")
