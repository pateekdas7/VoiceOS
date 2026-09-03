"""Integration test: PromiseToPayService against real Postgres (V5 Ch4.3, EV-7).

Exercises the full service path — validation → IdempotencyGuard.execute_once
→ PromiseToPayRepository.create_idempotent → Postgres — to prove that a
committed PTP row actually lands in the settlements-adjacent
``promises_to_pay`` table and that a same-key retry returns the original row
without producing a duplicate.

The repository layer alone is covered by test_ptp_repository.py; this test
also engages IdempotencyGuard (Sprint-015) so a regression in either layer
is caught.

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from src.libs.contracts.models.collections import PTPStatus
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.models.loan import EMIEntry, EMIStatus, LoanAccount
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.repositories.customer import CustomerRepository
from src.libs.repositories.emi_schedule import EMIScheduleRepository
from src.libs.repositories.idempotency import IdempotencyRepository
from src.libs.repositories.loan_account import LoanAccountRepository
from src.libs.repositories.promise_to_pay import PromiseToPayRepository
from src.services.collections.emi_schedule import EMIScheduleService
from src.services.collections.promise_to_pay import PromiseToPayService
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 4, tzinfo=UTC)


def _future_promise_date() -> datetime:
    """A date strictly in the future — computed at call time so the test
    stays valid regardless of when it runs (fixed literals would silently
    expire and trip the ``promise_date must be strictly in the future``
    validator)."""
    return datetime.now(UTC) + timedelta(days=7)


@pytest.fixture
def loan_fixture(pg_conn: Any) -> Iterator[tuple[TenantId, CustomerId, str]]:
    tenant_id = TenantId(str(uuid.uuid4()))
    customer_id = CustomerId(str(uuid.uuid4()))
    loan_account_id = f"loan-{uuid.uuid4()}"

    CustomerRepository(pg_conn).create(
        Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            crm_id=f"crm-{uuid.uuid4()}",
            name="PTP Service Test Customer",
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
    EMIScheduleRepository(pg_conn).create_entry(
        tenant_id,
        loan_account_id,
        EMIEntry(
            instalment_number=1,
            due_date=date(2026, 6, 1),
            principal_minor=8_000_00,
            interest_minor=1_000_00,
            total_minor=9_000_00,
            paid_minor=0,
            status=EMIStatus.PENDING,
        ),
    )

    yield tenant_id, customer_id, loan_account_id

    cur = pg_conn.cursor()
    cur.execute("DELETE FROM promises_to_pay WHERE customer_id = %s", (customer_id,))
    cur.execute("DELETE FROM idempotency_keys WHERE tenant_id = %s", (tenant_id,))
    cur.execute("DELETE FROM emi_entries WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM loan_accounts WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
    pg_conn.commit()


def _build_service(pg_conn: Any) -> PromiseToPayService:
    return PromiseToPayService(
        repository=PromiseToPayRepository(pg_conn),
        emi_schedule_service=EMIScheduleService(EMIScheduleRepository(pg_conn)),
        idempotency_guard=IdempotencyGuard(IdempotencyRepository(pg_conn)),
    )


@requires_postgres
class TestPromiseToPayServiceIntegration:
    @pytest.mark.asyncio
    async def test_create_persists_row_to_postgres(
        self, pg_conn: Any, loan_fixture: tuple[TenantId, CustomerId, str]
    ) -> None:
        tenant_id, customer_id, loan_account_id = loan_fixture
        service = _build_service(pg_conn)
        call_id = CallId(str(uuid.uuid4()))

        ptp = await service.create(
            tenant_id=tenant_id,
            call_id=call_id,
            customer_id=customer_id,
            loan_account_id=loan_account_id,
            promised_amount_minor=9_000_00,
            currency="INR",
            promise_date=_future_promise_date(),
        )

        assert ptp.status == PTPStatus.PENDING
        assert ptp.promised_amount_minor == 9_000_00

        cur = pg_conn.cursor()
        cur.execute(
            "SELECT ptp_id, promised_amount_minor, status FROM promises_to_pay WHERE ptp_id = %s",
            (ptp.ptp_id,),
        )
        row = cur.fetchone()
        assert row is not None, "PTP row was not committed to promises_to_pay"
        assert row[1] == 9_000_00
        assert row[2] == PTPStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_duplicate_create_via_service_is_idempotent(
        self, pg_conn: Any, loan_fixture: tuple[TenantId, CustomerId, str]
    ) -> None:
        """Second create() with the same (tenant, call, loan, date) returns the first row."""
        tenant_id, customer_id, loan_account_id = loan_fixture
        service = _build_service(pg_conn)
        call_id = CallId(str(uuid.uuid4()))

        first = await service.create(
            tenant_id=tenant_id,
            call_id=call_id,
            customer_id=customer_id,
            loan_account_id=loan_account_id,
            promised_amount_minor=9_000_00,
            currency="INR",
            promise_date=_future_promise_date(),
        )
        second = await service.create(
            tenant_id=tenant_id,
            call_id=call_id,
            customer_id=customer_id,
            loan_account_id=loan_account_id,
            promised_amount_minor=25_000_00,
            currency="INR",
            promise_date=_future_promise_date(),
        )

        assert second.ptp_id == first.ptp_id
        assert second.promised_amount_minor == 9_000_00

        cur = pg_conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM promises_to_pay WHERE loan_account_id = %s",
            (loan_account_id,),
        )
        assert cur.fetchone()[0] == 1

    @pytest.mark.asyncio
    async def test_update_status_broken_persists(
        self, pg_conn: Any, loan_fixture: tuple[TenantId, CustomerId, str]
    ) -> None:
        tenant_id, customer_id, loan_account_id = loan_fixture
        service = _build_service(pg_conn)
        call_id = CallId(str(uuid.uuid4()))

        ptp = await service.create(
            tenant_id=tenant_id,
            call_id=call_id,
            customer_id=customer_id,
            loan_account_id=loan_account_id,
            promised_amount_minor=9_000_00,
            currency="INR",
            promise_date=_future_promise_date(),
        )
        service.update_status(tenant_id, ptp.ptp_id, PTPStatus.BROKEN)

        cur = pg_conn.cursor()
        cur.execute("SELECT status FROM promises_to_pay WHERE ptp_id = %s", (ptp.ptp_id,))
        assert cur.fetchone()[0] == PTPStatus.BROKEN.value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
