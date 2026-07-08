"""LoanAccountService — loan account CRUD + real-time DPD (V5 Ch4).

Architecture: V5 Ch4 (Loan & Collections Management); Invariant RI-5.
"""

from __future__ import annotations

from src.libs.contracts.models.loan import LoanAccount, LoanOutstanding
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.repositories.loan_account import LoanAccountRepository

from .emi_schedule import EMIScheduleService


class LoanAccountService:
    """CRUD + DPD calculation over the ``loan_accounts`` domain (V5 Ch4)."""

    def __init__(self, repository: LoanAccountRepository, emi_schedule_service: EMIScheduleService) -> None:
        self._repo = repository
        self._emi = emi_schedule_service

    def create(self, loan: LoanAccount) -> LoanAccount:
        return self._repo.create(loan)

    def get(self, tenant_id: TenantId, loan_account_id: str) -> LoanAccount | None:
        return self._repo.get(tenant_id, loan_account_id)

    def find_by_customer(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[LoanAccount, ...]:
        return self._repo.find_by_customer(tenant_id, customer_id)

    def outstanding_balance(self, tenant_id: TenantId, loan_account_id: str) -> LoanOutstanding | None:
        return self._repo.outstanding_balance(tenant_id, loan_account_id)

    def calculate_dpd(self, tenant_id: TenantId, loan_account_id: str) -> int:
        """Real-time DPD for a single loan account — delegates to EMIScheduleService.

        Never reads the denormalised ``loan_accounts.dpd`` column for this
        purpose (that column exists only for fast list-view queries); the
        authoritative value placed in ``CustomerContext`` always comes from
        this method (V5 Ch4.3, Invariant RI-5).
        """
        return self._emi.calculate_dpd(tenant_id, loan_account_id)
