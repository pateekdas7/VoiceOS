"""CallDispositionRepository — per-call terminal outcomes (Sprint-024, V5 Ch11).

Backs ``CallAnalytics``'s per-call outcome/duration aggregation. The
``call_dispositions`` table was created by migration 0011 (Sprint-014) but
had no repository until this sprint.

Architecture: V5 Ch4.5 (call disposition vocabulary); V5 Ch11 (Analytics Platform).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.analytics import CallDisposition
from ..contracts.primitives import CallId, CustomerId, TenantId
from .base import BaseRepository

_TABLE = "call_dispositions"

_COLUMNS = (
    "disposition_id",
    "tenant_id",
    "call_id",
    "customer_id",
    "loan_account_id",
    "outcome_code",
    "duration_ms",
    "dispositioned_at",
)


class CallDispositionRepository(BaseRepository):
    """Tenant-scoped CRUD + aggregation queries for call dispositions."""

    def record(self, disposition: CallDisposition) -> CallDisposition:
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                disposition_id, tenant_id, call_id, customer_id, loan_account_id,
                outcome_code, duration_ms, dispositioned_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                disposition.disposition_id,
                disposition.tenant_id,
                disposition.call_id,
                disposition.customer_id,
                disposition.loan_account_id,
                disposition.outcome_code,
                disposition.duration_ms,
                disposition.dispositioned_at,
            ),
        )
        self._commit()
        return disposition

    def get_by_call_id(self, tenant_id: TenantId, call_id: str) -> CallDisposition | None:
        """Fetch a single disposition by call_id (Sprint-025, ``GET /v1/calls/{id}``)."""
        row = self._tenant_select_one(_TABLE, _COLUMNS, tenant_id, extra_where="call_id = %s", extra_params=(call_id,))
        return self._hydrate(row) if row is not None else None

    def find_between(self, tenant_id: TenantId, start: Any, end: Any) -> tuple[CallDisposition, ...]:
        """Find all dispositions in ``[start, end)`` for a tenant (day-rollup source)."""
        rows = self._tenant_select(
            _TABLE,
            _COLUMNS,
            tenant_id,
            extra_where="dispositioned_at >= %s AND dispositioned_at < %s",
            extra_params=(start, end),
            order_by="dispositioned_at",
        )
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: tuple[Any, ...]) -> CallDisposition:
        (
            disposition_id,
            tenant_id,
            call_id,
            customer_id,
            loan_account_id,
            outcome_code,
            duration_ms,
            dispositioned_at,
        ) = row
        return CallDisposition(
            disposition_id=str(disposition_id),
            tenant_id=TenantId(str(tenant_id)),
            call_id=CallId(str(call_id)),
            customer_id=CustomerId(str(customer_id)),
            loan_account_id=str(loan_account_id),
            outcome_code=outcome_code,
            duration_ms=duration_ms,
            dispositioned_at=dispositioned_at,
        )
