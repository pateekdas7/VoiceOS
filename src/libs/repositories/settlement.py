"""SettlementRepository — settlement offer lifecycle persistence (V5 Ch4.4).

Architecture: V5 Ch4.4 (Settlement Workflow); Invariant RI-5 (Law of Authority).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.collections import Settlement, SettlementStatus
from ..contracts.primitives import CustomerId, TenantId
from .base import BaseRepository

_TABLE = "settlements"

_SETTLEMENT_COLUMNS = (
    "settlement_id",
    "tenant_id",
    "customer_id",
    "loan_account_id",
    "settlement_amount_minor",
    "waiver_amount_minor",
    "currency",
    "offer_expiry",
    "status",
    "approved_by",
    "authorized_at",
    "proposed_at",
    "updated_at",
)


class SettlementRepository(BaseRepository):
    """Tenant-scoped CRUD + lookup queries for the ``settlements`` domain."""

    def create(self, settlement: Settlement) -> Settlement:
        """Insert a new settlement offer (status always starts ``PROPOSED``)."""
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                settlement_id, tenant_id, customer_id, loan_account_id,
                settlement_amount_minor, waiver_amount_minor, currency,
                offer_expiry, status, proposed_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                settlement.settlement_id,
                settlement.tenant_id,
                settlement.customer_id,
                settlement.loan_account_id,
                settlement.settlement_amount_minor,
                settlement.waiver_amount_minor,
                settlement.currency,
                settlement.offer_expiry,
                settlement.status.value,
                settlement.proposed_at,
                settlement.updated_at,
            ),
        )
        self._commit()
        return settlement

    def get(self, tenant_id: TenantId, settlement_id: str) -> Settlement | None:
        row = self._tenant_select_one(
            _TABLE,
            _SETTLEMENT_COLUMNS,
            tenant_id,
            extra_where="settlement_id = %s",
            extra_params=(settlement_id,),
        )
        return self._hydrate(row) if row is not None else None

    def find_by_customer(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[Settlement, ...]:
        rows = self._tenant_select(
            _TABLE,
            _SETTLEMENT_COLUMNS,
            tenant_id,
            extra_where="customer_id = %s",
            extra_params=(customer_id,),
            order_by="proposed_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def update_status(self, tenant_id: TenantId, settlement_id: str, status: SettlementStatus) -> None:
        self._tenant_update(
            _TABLE,
            ("status",),
            (status.value,),
            tenant_id,
            extra_where="settlement_id = %s",
            extra_params=(settlement_id,),
        )

    def get_approved_by(self, tenant_id: TenantId, settlement_id: str) -> str | None:
        """Return the recorded human approver, if this settlement has been authorized."""
        row = self._tenant_select_one(
            _TABLE,
            ("approved_by",),
            tenant_id,
            extra_where="settlement_id = %s",
            extra_params=(settlement_id,),
        )
        return row[0] if row is not None else None

    def record_authorization(
        self, tenant_id: TenantId, settlement_id: str, approved_by: str, authorized_at: Any
    ) -> None:
        """Record the human approver for a settlement over the approval threshold (V5 §5.13).

        Does not change ``status`` — authorization is a metadata gate that
        must be satisfied before ``disburse()`` (status -> PAID) is allowed,
        not a state of the frozen ``SettlementStatus`` enum (Sprint-002).
        """
        self._tenant_update(
            _TABLE,
            ("approved_by", "authorized_at"),
            (approved_by, authorized_at),
            tenant_id,
            extra_where="settlement_id = %s",
            extra_params=(settlement_id,),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...]) -> Settlement:
        (
            settlement_id,
            tenant_id,
            customer_id,
            loan_account_id,
            settlement_amount_minor,
            waiver_amount_minor,
            currency,
            offer_expiry,
            status,
            _approved_by,
            _authorized_at,
            proposed_at,
            updated_at,
        ) = row
        return Settlement(
            settlement_id=str(settlement_id),
            tenant_id=TenantId(tenant_id),
            customer_id=CustomerId(customer_id),
            loan_account_id=loan_account_id,
            settlement_amount_minor=settlement_amount_minor,
            waiver_amount_minor=waiver_amount_minor,
            currency=currency,
            offer_expiry=offer_expiry,
            status=SettlementStatus(status),
            proposed_at=proposed_at,
            updated_at=updated_at,
        )
