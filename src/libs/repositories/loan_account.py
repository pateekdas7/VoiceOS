"""LoanAccountRepository — authoritative collections loan account records (V5 Ch4).

Architecture: V5 Ch4 (Loan & Collections); Invariant RI-5 (Law of Authority).
"""

from __future__ import annotations

from typing import Any

from ..contracts.models.loan import LoanAccount, LoanOutstanding, LoanStatus
from ..contracts.primitives import CustomerId, TenantId
from .base import BaseRepository

_TABLE = "loan_accounts"

_LOAN_COLUMNS = (
    "loan_account_id",
    "tenant_id",
    "customer_id",
    "product_type",
    "disbursed_amount_minor",
    "currency",
    "interest_rate_bps",
    "tenure_months",
    "disbursement_date",
    "maturity_date",
    "status",
    "dpd",
    "outstanding_principal_minor",
    "outstanding_interest_minor",
    "outstanding_penalty_minor",
    "outstanding_total_minor",
    "outstanding_as_of",
    "created_at",
    "updated_at",
)


class LoanAccountRepository(BaseRepository):
    """Tenant-scoped CRUD + lookup queries for the ``loan_accounts`` domain."""

    def create(self, loan: LoanAccount) -> LoanAccount:
        """Insert a new loan account record."""
        outstanding = loan.outstanding
        self._execute(
            f"""
            INSERT INTO {_TABLE} (
                loan_account_id, tenant_id, customer_id, product_type,
                disbursed_amount_minor, currency, interest_rate_bps, tenure_months,
                disbursement_date, maturity_date, status, dpd,
                outstanding_principal_minor, outstanding_interest_minor,
                outstanding_penalty_minor, outstanding_total_minor, outstanding_as_of,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                loan.loan_account_id,
                loan.tenant_id,
                loan.customer_id,
                loan.product_type,
                loan.disbursed_amount_minor,
                loan.currency,
                loan.interest_rate_bps,
                loan.tenure_months,
                loan.disbursement_date,
                loan.maturity_date,
                loan.status.value,
                loan.dpd,
                outstanding.principal_minor if outstanding else None,
                outstanding.interest_minor if outstanding else None,
                outstanding.penalty_minor if outstanding else None,
                outstanding.total_minor if outstanding else None,
                outstanding.as_of if outstanding else None,
                loan.created_at,
                loan.updated_at,
            ),
        )
        self._commit()
        return loan

    def get(self, tenant_id: TenantId, loan_account_id: str) -> LoanAccount | None:
        """Fetch a loan account by ID, scoped to ``tenant_id``."""
        row = self._tenant_select_one(
            _TABLE,
            _LOAN_COLUMNS,
            tenant_id,
            extra_where="loan_account_id = %s",
            extra_params=(loan_account_id,),
        )
        return self._hydrate(row) if row is not None else None

    def find_by_customer(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[LoanAccount, ...]:
        """Find all loan accounts for a customer, scoped to ``tenant_id``."""
        rows = self._tenant_select(
            _TABLE,
            _LOAN_COLUMNS,
            tenant_id,
            extra_where="customer_id = %s",
            extra_params=(customer_id,),
            order_by="created_at DESC",
        )
        return tuple(self._hydrate(row) for row in rows)

    def outstanding_balance(self, tenant_id: TenantId, loan_account_id: str) -> LoanOutstanding | None:
        """Fetch the denormalised current outstanding balance for a loan account."""
        row = self._tenant_select_one(
            _TABLE,
            (
                "outstanding_principal_minor",
                "outstanding_interest_minor",
                "outstanding_penalty_minor",
                "outstanding_total_minor",
                "outstanding_as_of",
                "currency",
            ),
            tenant_id,
            extra_where="loan_account_id = %s",
            extra_params=(loan_account_id,),
        )
        if row is None or row[4] is None:
            return None
        principal, interest, penalty, total, as_of, currency = row
        return LoanOutstanding(
            loan_account_id=loan_account_id,
            principal_minor=principal or 0,
            interest_minor=interest or 0,
            penalty_minor=penalty or 0,
            total_minor=total or 0,
            currency=currency,
            as_of=as_of,
        )

    def select_cohort(
        self,
        tenant_id: TenantId,
        min_dpd: int,
        max_dpd: int | None,
        min_outstanding_minor: int,
        product_types: tuple[str, ...] = (),
    ) -> tuple[tuple[str, str, int], ...]:
        """Campaign audience cohort selection (V5 Ch6): ``(customer_id, loan_account_id, dpd)``
        tuples for accounts matching the DPD/outstanding/product filters, scoped to ``tenant_id``."""
        extra_where = "dpd >= %s AND outstanding_total_minor >= %s"
        extra_params: list[Any] = [min_dpd, min_outstanding_minor]
        if max_dpd is not None:
            extra_where += " AND dpd <= %s"
            extra_params.append(max_dpd)
        if product_types:
            placeholders = ", ".join(["%s"] * len(product_types))
            extra_where += f" AND product_type IN ({placeholders})"
            extra_params.extend(product_types)
        rows = self._tenant_select(
            _TABLE,
            ("customer_id", "loan_account_id", "dpd"),
            tenant_id,
            extra_where=extra_where,
            extra_params=extra_params,
        )
        return tuple((str(customer_id), str(loan_account_id), dpd) for customer_id, loan_account_id, dpd in rows)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _hydrate(self, row: tuple[Any, ...]) -> LoanAccount:
        (
            loan_account_id,
            tenant_id,
            customer_id,
            product_type,
            disbursed_amount_minor,
            currency,
            interest_rate_bps,
            tenure_months,
            disbursement_date,
            maturity_date,
            status,
            dpd,
            outstanding_principal_minor,
            outstanding_interest_minor,
            outstanding_penalty_minor,
            outstanding_total_minor,
            outstanding_as_of,
            created_at,
            updated_at,
        ) = row

        outstanding = None
        if outstanding_as_of is not None:
            outstanding = LoanOutstanding(
                loan_account_id=loan_account_id,
                principal_minor=outstanding_principal_minor or 0,
                interest_minor=outstanding_interest_minor or 0,
                penalty_minor=outstanding_penalty_minor or 0,
                total_minor=outstanding_total_minor or 0,
                currency=currency,
                as_of=outstanding_as_of,
            )

        return LoanAccount(
            loan_account_id=loan_account_id,
            tenant_id=TenantId(tenant_id),
            customer_id=CustomerId(customer_id),
            product_type=product_type,
            disbursed_amount_minor=disbursed_amount_minor,
            currency=currency,
            interest_rate_bps=interest_rate_bps,
            tenure_months=tenure_months,
            disbursement_date=disbursement_date,
            maturity_date=maturity_date,
            status=LoanStatus(status),
            dpd=dpd,
            outstanding=outstanding,
            emi_schedule=None,
            created_at=created_at,
            updated_at=updated_at,
        )
