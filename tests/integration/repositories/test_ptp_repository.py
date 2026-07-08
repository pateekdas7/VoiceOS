"""Integration test: PromiseToPayRepository against real Postgres (V5 Ch4.3, EV-7).

Required named test: test_ptp_create_returns_existing_on_duplicate_key.

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest

from src.libs.contracts.models.collections import PromiseToPay
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.models.loan import LoanAccount
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.repositories.customer import CustomerRepository
from src.libs.repositories.loan_account import LoanAccountRepository
from src.libs.repositories.promise_to_pay import PromiseToPayRepository
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 4, tzinfo=UTC)


@pytest.fixture
def loan_fixture(pg_conn: Any) -> Iterator[tuple[TenantId, CustomerId, str]]:
    """A real customer + loan account for PTP FK constraints, cleaned up after the test."""
    tenant_id = TenantId(str(uuid.uuid4()))
    customer_id = CustomerId(str(uuid.uuid4()))
    loan_account_id = f"loan-{uuid.uuid4()}"

    CustomerRepository(pg_conn).create(
        Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            crm_id=f"crm-{uuid.uuid4()}",
            name="PTP Test Customer",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    LoanAccountRepository(pg_conn).create(
        LoanAccount(
            loan_account_id=loan_account_id,
            tenant_id=tenant_id,
            customer_id=customer_id,
            product_type="PERSONAL_LOAN",
            disbursed_amount_minor=100_000_00,
            currency="INR",
            interest_rate_bps=1200,
            tenure_months=12,
            disbursement_date=date(2026, 1, 1),
            maturity_date=date(2027, 1, 1),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )

    yield tenant_id, customer_id, loan_account_id

    cur = pg_conn.cursor()
    cur.execute("DELETE FROM promises_to_pay WHERE customer_id = %s", (customer_id,))
    cur.execute("DELETE FROM loan_accounts WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
    pg_conn.commit()


@requires_postgres
class TestPromiseToPayRepository:
    def test_ptp_create_returns_existing_on_duplicate_key(
        self, pg_conn: Any, loan_fixture: tuple[TenantId, CustomerId, str]
    ) -> None:
        """Sprint-014 required test: idempotent PTP creation."""
        tenant_id, customer_id, loan_account_id = loan_fixture
        repo = PromiseToPayRepository(pg_conn)
        idempotency_key = f"call-1:turn-1:{uuid.uuid4()}"

        def _new_ptp(amount_minor: int) -> PromiseToPay:
            return PromiseToPay(
                ptp_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                call_id=CallId(str(uuid.uuid4())),
                customer_id=customer_id,
                loan_account_id=loan_account_id,
                promised_amount_minor=amount_minor,
                currency="INR",
                promise_date=_NOW,
                recorded_at=_NOW,
                updated_at=_NOW,
            )

        first_ptp, first_created = repo.create_idempotent(_new_ptp(50_000_00), idempotency_key=idempotency_key)
        assert first_created is True

        # A retry (e.g. barge-in re-submission) with the same idempotency_key but a
        # different amount must return the ORIGINAL record, not create a second one.
        second_ptp, second_created = repo.create_idempotent(_new_ptp(99_999_00), idempotency_key=idempotency_key)
        assert second_created is False
        assert second_ptp.ptp_id == first_ptp.ptp_id
        assert second_ptp.promised_amount_minor == 50_000_00

        all_for_loan = repo.find_by_loan(tenant_id, loan_account_id)
        assert len(all_for_loan) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
