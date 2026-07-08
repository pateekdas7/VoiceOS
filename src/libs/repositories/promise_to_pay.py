"""PromiseToPayRepository — idempotent PTP recording (V5 Ch4.3, V3 Ch8).

Architecture: V5 Ch4.3 (Promise-To-Pay); V3 Ch8 (Idempotency); Invariant EV-7.
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.collections import PromiseToPay, PTPStatus
from ..contracts.primitives import CustomerId, TenantId
from .base import BaseRepository

_TABLE = "promises_to_pay"

_PTP_COLUMNS = (
    "ptp_id",
    "tenant_id",
    "call_id",
    "customer_id",
    "loan_account_id",
    "promised_amount_minor",
    "currency",
    "promise_date",
    "status",
    "notes",
    "recorded_at",
    "updated_at",
)


class PromiseToPayRepository(BaseRepository):
    """Tenant-scoped, idempotency-protected queries for the ``promises_to_pay`` domain."""

    def create_idempotent(self, ptp: PromiseToPay, idempotency_key: str | None = None) -> tuple[PromiseToPay, bool]:
        """Insert a PTP, or return the existing record if ``idempotency_key`` already exists.

        Uses ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`` against
        the ``uq_ptp_idempotency_key`` constraint (Sprint-014 migration 0007).
        A ``None`` key never conflicts (Postgres treats NULLs as distinct in
        a UNIQUE constraint), so callers that don't supply one always insert.

        Args:
            ptp: The PTP to persist.
            idempotency_key: Deduplication key, typically ``call_id:turn_id``.

        Returns:
            A tuple of (the resulting record, whether it was newly created).
            When ``created`` is ``False``, the returned record is the
            pre-existing row for that idempotency key, not ``ptp``.
        """
        cur = self._execute(
            f"""
            INSERT INTO {_TABLE} (
                ptp_id, tenant_id, call_id, customer_id, loan_account_id,
                promised_amount_minor, currency, promise_date, status, notes,
                idempotency_key, recorded_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING ptp_id
            """,
            (
                ptp.ptp_id,
                ptp.tenant_id,
                ptp.call_id,
                ptp.customer_id,
                ptp.loan_account_id,
                ptp.promised_amount_minor,
                ptp.currency,
                ptp.promise_date,
                ptp.status.value,
                ptp.notes,
                idempotency_key,
                ptp.recorded_at,
                ptp.updated_at,
            ),
        )
        inserted_row = cur.fetchone()
        self._commit()

        if inserted_row is not None:
            return ptp, True

        # Conflict: another call with the same idempotency_key already exists.
        existing_row = self._tenant_select_one(
            _TABLE,
            _PTP_COLUMNS,
            ptp.tenant_id,
            extra_where="idempotency_key = %s",
            extra_params=(idempotency_key,),
        )
        if existing_row is None:
            # idempotency_key was None (no real conflict possible) — the insert
            # must have failed for another reason, which _execute would already
            # have raised. Unreachable in practice; kept for type-safety.
            raise RuntimeError("create_idempotent: insert conflicted but no existing row found")
        return self._hydrate(existing_row), False

    def update_status(self, tenant_id: TenantId, ptp_id: str, status: PTPStatus) -> None:
        """Update the lifecycle status of a PTP, scoped to ``tenant_id``."""
        self._tenant_update(
            _TABLE,
            ("status",),
            (status.value,),
            tenant_id,
            extra_where="ptp_id = %s",
            extra_params=(ptp_id,),
        )

    def find_by_loan(self, tenant_id: TenantId, loan_account_id: str) -> tuple[PromiseToPay, ...]:
        """Find all PTPs recorded against a loan account, scoped to ``tenant_id``."""
        rows = self._tenant_select(
            _TABLE,
            _PTP_COLUMNS,
            tenant_id,
            extra_where="loan_account_id = %s",
            extra_params=(loan_account_id,),
            order_by="recorded_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...]) -> PromiseToPay:
        (
            ptp_id,
            tenant_id,
            call_id,
            customer_id,
            loan_account_id,
            promised_amount_minor,
            currency,
            promise_date,
            status,
            notes,
            recorded_at,
            updated_at,
        ) = row
        return PromiseToPay(
            ptp_id=str(ptp_id),
            tenant_id=TenantId(tenant_id),
            call_id=call_id,
            customer_id=CustomerId(customer_id),
            loan_account_id=loan_account_id,
            promised_amount_minor=promised_amount_minor,
            currency=currency,
            promise_date=promise_date,
            status=PTPStatus(status),
            recorded_at=recorded_at,
            updated_at=updated_at,
            notes=notes or "",
        )
