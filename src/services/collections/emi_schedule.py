"""EMIScheduleService — EMI schedule management and payment posting (V5 Ch4.2).

Architecture: V5 Ch4.2 (EMI Schedule); Invariant RI-5 (Law of Authority) —
DPD and outstanding values are always computed real-time from this schedule,
never fabricated or cached beyond the denormalised ``loan_accounts`` columns
that other reads use for fast listing (V5 Ch4.3).
"""

from __future__ import annotations

from datetime import date

from src.libs.contracts.models.loan import EMIEntry, EMISchedule, EMIStatus
from src.libs.contracts.primitives import TenantId
from src.libs.repositories.emi_schedule import EMIScheduleRepository


class EMIScheduleService:
    """Schedule management + real-time DPD/next-due calculation for a loan account."""

    def __init__(self, repository: EMIScheduleRepository) -> None:
        self._repo = repository

    def create_schedule(self, tenant_id: TenantId, loan_account_id: str, instalments: tuple[EMIEntry, ...]) -> None:
        """Persist a full EMI schedule (used at loan origination / import time)."""
        for entry in instalments:
            self._repo.create_entry(tenant_id, loan_account_id, entry)

    def get_schedule(self, tenant_id: TenantId, loan_account_id: str, currency: str = "INR") -> EMISchedule:
        return self._repo.get_schedule(tenant_id, loan_account_id, currency)

    def post_payment(
        self,
        tenant_id: TenantId,
        loan_account_id: str,
        instalment_number: int,
        paid_minor: int,
        instalment_total_minor: int,
    ) -> EMIStatus:
        """Post a payment against a single instalment; returns its resulting status."""
        status = EMIStatus.PAID if paid_minor >= instalment_total_minor else EMIStatus.PARTIALLY_PAID
        self._repo.update_payment(tenant_id, loan_account_id, instalment_number, paid_minor, status)
        return status

    def calculate_dpd(self, tenant_id: TenantId, loan_account_id: str, *, as_of: date | None = None) -> int:
        """Real-time Days-Past-Due: calendar days since the earliest unpaid EMI's due date.

        Not stored — always derived fresh from ``emi_entries`` (V5 Ch4.3,
        Sprint-022 AC: "3 unpaid EMIs, oldest due 30 days ago -> DPD = 30").
        Returns 0 when there is no unpaid instalment, or when the earliest
        unpaid instalment is not yet overdue.
        """
        today = as_of or date.today()
        unpaid = self._repo.find_unpaid(tenant_id, loan_account_id)
        if not unpaid:
            return 0
        oldest_due_date = unpaid[0].due_date  # find_unpaid orders by due_date ASC
        dpd = (today - oldest_due_date).days
        return max(dpd, 0)

    def next_unpaid_emi(self, tenant_id: TenantId, loan_account_id: str) -> EMIEntry | None:
        """The earliest unpaid instalment — the source of the "minimum PTP amount" rule."""
        unpaid = self._repo.find_unpaid(tenant_id, loan_account_id)
        return unpaid[0] if unpaid else None

    def total_overdue_minor(self, tenant_id: TenantId, loan_account_id: str, *, as_of: date | None = None) -> int:
        """Sum of unpaid balances across every instalment currently past due."""
        today = as_of or date.today()
        unpaid = self._repo.find_unpaid(tenant_id, loan_account_id)
        return sum(entry.total_minor - entry.paid_minor for entry in unpaid if entry.due_date <= today)
