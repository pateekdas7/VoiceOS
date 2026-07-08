"""Sprint-019: encrypted PII columns, DEK metadata, erasure certificates

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-05

New tables (additive):
  - ``data_encryption_keys``: per-record wrapped-DEK metadata (V4 Ch8
    §8.9 envelope encryption). The *plaintext* DEK never persists anywhere;
    only its KMS-wrapped form is stored here, keyed by ``dek_id``.
    Crypto-shredding a record (V4 Ch8 §8.12) means deleting its row here —
    see ``src/libs/encryption/dek_store.py::PostgresDEKStore``.
  - ``data_erasure_certificates``: immutable proof-of-erasure records
    (V4 Ch9 §9.6 ``ErasureResult``/``DataErasureCertificate``).

New columns (additive, expand-only — matches the Sprint-014/015/017
precedent of never dropping/renaming a column already in production):
  - ``customers.name_encrypted BYTEA`` — envelope-encrypted ``name``,
    serialized via ``EncryptedPayload.to_bytes()``.
  - ``customer_contacts.value_encrypted BYTEA`` — envelope-encrypted
    ``value`` (phone/email).
  - ``customer_addresses.address_encrypted BYTEA`` — envelope-encrypted
    JSON blob of ``{line1, line2, city, state, pincode}`` (the whole
    address is one DEK/ciphertext, not one per sub-field).

The original plaintext columns (``name``, ``value``, ``line1``/etc.) are
deliberately *not* dropped in this migration — Sprint-019.md's own rollback
procedure requires both forms to coexist during the transition ("each PII
column has both the original and `_encrypted` during transition; rollback
reads from original"). ``scripts/db/encrypt_pii_backfill.py`` populates the
new columns for already-existing rows; going forward, repositories write to
the encrypted column whenever an ``EncryptionService`` is wired in (Sprint-019
Phase 2) and fall back to the plaintext column when reading a row that
predates encryption. Dropping the plaintext columns entirely is intentionally
left to a follow-up sprint/ADR once every write path is confirmed
encryption-only — this migration only performs the additive "expand" half
of an expand-contract rollout (V6 Ch7 migration discipline).

Note: Sprint-019.md's illustrative PII field list ("phone, name, address,
UPI_ID") doesn't map 1:1 onto the actual Sprint-014 schema — there is no
``customers.phone``/``UPI_ID`` column; the real phone-equivalent field is
``customer_contacts.value``, and there is no UPI_ID column anywhere in this
schema today. This migration encrypts the fields that actually exist
(name, contact value, address), consistent with how prior sprints resolved
illustrative-prose-vs-actual-schema mismatches (see CPU_NODE_STATE.md §18).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_encryption_keys (
            dek_id       TEXT        PRIMARY KEY,
            tenant_id    UUID        NOT NULL,
            wrapped_dek  BYTEA       NOT NULL,
            kek_id       TEXT        NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_dek_tenant_id ON data_encryption_keys (tenant_id);")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_erasure_certificates (
            certificate_id  UUID        PRIMARY KEY,
            tenant_id       UUID        NOT NULL,
            customer_id     UUID        NOT NULL,
            scope           TEXT[]      NOT NULL,
            method          TEXT        NOT NULL,
            completed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_erasure_cert_customer ON data_erasure_certificates (tenant_id, customer_id);"
    )

    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS name_encrypted BYTEA;")
    op.execute("ALTER TABLE customer_contacts ADD COLUMN IF NOT EXISTS value_encrypted BYTEA;")
    op.execute("ALTER TABLE customer_addresses ADD COLUMN IF NOT EXISTS address_encrypted BYTEA;")


def downgrade() -> None:
    op.execute("ALTER TABLE customer_addresses DROP COLUMN IF EXISTS address_encrypted;")
    op.execute("ALTER TABLE customer_contacts DROP COLUMN IF EXISTS value_encrypted;")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS name_encrypted;")
    op.execute("DROP TABLE IF EXISTS data_erasure_certificates CASCADE;")
    op.execute("DROP TABLE IF EXISTS data_encryption_keys CASCADE;")
