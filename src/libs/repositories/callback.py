"""CallbackRepository — customer callback scheduling persistence (V5 Ch4.5).

Architecture: V5 Ch4.5 (Callback Scheduling).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.collections import CallbackRequest
from ..contracts.primitives import CustomerId, TenantId
from .base import BaseRepository

_TABLE = "callback_requests"

_CALLBACK_COLUMNS = (
    "callback_id",
    "tenant_id",
    "call_id",
    "customer_id",
    "loan_account_id",
    "preferred_time",
    "timezone",
    "phone_number",
    "is_fulfilled",
    "recorded_at",
)


class CallbackRepository(BaseRepository):
    """Tenant-scoped CRUD + lookup queries for the ``callback_requests`` domain."""

    def create(self, callback: CallbackRequest) -> CallbackRequest:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                callback_id, tenant_id, call_id, customer_id, loan_account_id,
                preferred_time, timezone, phone_number, is_fulfilled, recorded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                callback.callback_id,
                callback.tenant_id,
                callback.call_id,
                callback.customer_id,
                callback.loan_account_id,
                callback.preferred_time,
                callback.timezone,
                callback.phone_number,
                callback.is_fulfilled,
                callback.recorded_at,
            ),
        )
        self._commit()
        return callback

    def find_pending(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[CallbackRequest, ...]:
        rows = self._tenant_select(
            _TABLE,
            _CALLBACK_COLUMNS,
            tenant_id,
            extra_where="customer_id = %s AND is_fulfilled = FALSE",
            extra_params=(customer_id,),
            order_by="preferred_time ASC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def mark_fulfilled(self, tenant_id: TenantId, callback_id: str) -> None:
        self._tenant_update(
            _TABLE,
            ("is_fulfilled",),
            (True,),
            tenant_id,
            extra_where="callback_id = %s",
            extra_params=(callback_id,),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...]) -> CallbackRequest:
        (
            callback_id,
            tenant_id,
            call_id,
            customer_id,
            loan_account_id,
            preferred_time,
            timezone,
            phone_number,
            is_fulfilled,
            recorded_at,
        ) = row
        return CallbackRequest(
            callback_id=str(callback_id),
            tenant_id=TenantId(tenant_id),
            call_id=call_id,
            customer_id=CustomerId(customer_id),
            loan_account_id=loan_account_id,
            preferred_time=preferred_time,
            timezone=timezone,
            phone_number=phone_number,
            recorded_at=recorded_at,
            is_fulfilled=is_fulfilled,
        )
