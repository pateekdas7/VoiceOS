"""EMIScheduleRepository — EMI instalment schedule persistence (V5 Ch4.2).

Architecture: V5 Ch4.2 (EMI Schedule); Invariant RI-5 (Law of Authority).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..contracts.models.loan import EMIEntry, EMISchedule, EMIStatus
from ..contracts.primitives import TenantId
from .base import BaseRepository

_TABLE = "emi_entries"

_EMI_COLUMNS = (
    "instalment_number",
    "due_date",
    "principal_minor",
    "interest_minor",
    "total_minor",
    "paid_minor",
    "status",
)


class EMIScheduleRepository(BaseRepository):
    """Tenant-scoped CRUD + lookup queries for the ``emi_entries`` domain."""

    def create_entry(self, tenant_id: TenantId, loan_account_id: str, entry: EMIEntry) -> EMIEntry:
        """Insert a single EMI instalment row for ``loan_account_id``."""
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                loan_account_id, tenant_id, instalment_number, due_date,
                principal_minor, interest_minor, total_minor, paid_minor, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                loan_account_id,
                tenant_id,
                entry.instalment_number,
                entry.due_date,
                entry.principal_minor,
                entry.interest_minor,
                entry.total_minor,
                entry.paid_minor,
                entry.status.value,
            ),
        )
        self._commit()
        return entry

    def get_schedule(self, tenant_id: TenantId, loan_account_id: str, currency: str = "INR") -> EMISchedule:
        """Fetch the full EMI schedule for a loan account, ordered by instalment number."""
        rows = self._tenant_select(
            _TABLE,
            _EMI_COLUMNS,
            tenant_id,
            extra_where="loan_account_id = %s",
            extra_params=(loan_account_id,),
            order_by="instalment_number ASC",
        )
        return EMISchedule(
            loan_account_id=loan_account_id,
            currency=currency,
            instalments=tuple(self._hydrate(row) for row in rows),
        )

    def find_unpaid(self, tenant_id: TenantId, loan_account_id: str) -> tuple[EMIEntry, ...]:
        """Fetch unpaid instalments (PENDING/PARTIALLY_PAID/OVERDUE) ordered by due date.

        This is the real-time source for DPD calculation (V5 Ch4.3) — the
        oldest unpaid instalment's ``due_date`` determines DPD; nothing here
        is cached or derived from the denormalised ``loan_accounts.dpd`` column.
        """
        rows = self._tenant_select(
            _TABLE,
            _EMI_COLUMNS,
            tenant_id,
            extra_where=("loan_account_id = %s AND status IN ('PENDING', 'PARTIALLY_PAID', 'OVERDUE')"),
            extra_params=(loan_account_id,),
            order_by="due_date ASC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def update_payment(
        self, tenant_id: TenantId, loan_account_id: str, instalment_number: int, paid_minor: int, status: EMIStatus
    ) -> None:
        """Post a payment against a single instalment, updating its status."""
        self._tenant_update(
            _TABLE,
            ("paid_minor", "status", "updated_at"),
            (paid_minor, status.value, datetime.now(UTC)),
            tenant_id,
            extra_where="loan_account_id = %s AND instalment_number = %s",
            extra_params=(loan_account_id, instalment_number),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...]) -> EMIEntry:
        (
            instalment_number,
            due_date,
            principal_minor,
            interest_minor,
            total_minor,
            paid_minor,
            status,
        ) = row
        return EMIEntry(
            instalment_number=instalment_number,
            due_date=due_date,
            principal_minor=principal_minor,
            interest_minor=interest_minor,
            total_minor=total_minor,
            paid_minor=paid_minor,
            status=EMIStatus(status),
        )
