"""Sprint-022: settlement authorization gate (CRM & Loan/Collections Management)

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-06

Sprint-022's Settlement workflow AC requires "offer -> accept -> authorize ->
disburse" transitions. The ``settlements`` table already exists (migration
0007, Sprint-014) with the frozen ``SettlementStatus`` enum
(``PROPOSED``/``ACCEPTED``/``REJECTED``/``EXPIRED``/``PAID`` — Sprint-002
contract, `src/libs/contracts/models/collections.py`); "authorize" is not a
new enum member (that would be a contract redesign outside Sprint-022's
scope) but a metadata gate recorded alongside ``ACCEPTED`` before
``SettlementService.disburse()`` is allowed to move an over-threshold
settlement (> 50,000 minor units, V5 Ch4 §5.13 ``approval_threshold``) to
``PAID``. This revision adds the two columns that gate records, additively.

No new tables — ``settlements``/``callback_requests``/``escalation_records``
were all created by migration 0007 (Sprint-014), ahead of the sprint that
actually builds their service layer.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE settlements ADD COLUMN IF NOT EXISTS approved_by TEXT;")
    op.execute("ALTER TABLE settlements ADD COLUMN IF NOT EXISTS authorized_at TIMESTAMPTZ;")


def downgrade() -> None:
    op.execute("ALTER TABLE settlements DROP COLUMN IF EXISTS authorized_at;")
    op.execute("ALTER TABLE settlements DROP COLUMN IF EXISTS approved_by;")
