"""Integration test: concurrent PromiseToPayService.create() (V3 Ch8, EV-7).

Exercises the IdempotencyGuard claim/poll race path against real Postgres by
opening N independent connections and firing N ``service.create()`` calls
with the *same* (tenant, call, loan, promise_date) key concurrently.

Acceptance criterion (V3 Ch8): "10 concurrent calls -> 1 effect". A pass here
proves that under real database contention the guard admits exactly one
writer, and the losers return the winner's cached PTP rather than either
duplicating the row or raising.

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

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
from tests.fixtures.db import POSTGRES_DSN
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 4, tzinfo=UTC)

CONCURRENT_CALLERS = 10


def _future_promise_date() -> datetime:
    return datetime.now(UTC) + timedelta(days=7)


@pytest.fixture
def seeded_loan(pg_conn: Any) -> Iterator[tuple[TenantId, CustomerId, str]]:
    """Seed customer/loan/EMI rows on the shared conn; separate connections
    used by the concurrent workers will see this data because the setup
    commits before the test body runs."""
    tenant_id = TenantId(str(uuid.uuid4()))
    customer_id = CustomerId(str(uuid.uuid4()))
    loan_account_id = f"loan-{uuid.uuid4()}"

    CustomerRepository(pg_conn).create(
        Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            crm_id=f"crm-{uuid.uuid4()}",
            name="Concurrent PTP Test Customer",
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
    pg_conn.commit()

    yield tenant_id, customer_id, loan_account_id

    cur = pg_conn.cursor()
    cur.execute("DELETE FROM promises_to_pay WHERE customer_id = %s", (customer_id,))
    cur.execute("DELETE FROM idempotency_keys WHERE tenant_id = %s", (tenant_id,))
    cur.execute("DELETE FROM emi_entries WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM loan_accounts WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
    pg_conn.commit()


def _build_service_on_conn(conn: Any) -> PromiseToPayService:
    return PromiseToPayService(
        repository=PromiseToPayRepository(conn),
        emi_schedule_service=EMIScheduleService(EMIScheduleRepository(conn)),
        idempotency_guard=IdempotencyGuard(IdempotencyRepository(conn)),
    )


@requires_postgres
class TestPromiseToPayServiceConcurrency:
    @pytest.mark.asyncio
    async def test_n_concurrent_creates_produce_exactly_one_row(
        self,
        pg_conn: Any,
        seeded_loan: tuple[TenantId, CustomerId, str],
    ) -> None:
        """N concurrent create() calls with the same idempotency key must
        return the SAME PTP and leave exactly one row in ``promises_to_pay``.
        A regression that lost the claim atomicity would surface here as
        either N rows or divergent ptp_ids across returned PTPs."""
        import psycopg2

        tenant_id, customer_id, loan_account_id = seeded_loan
        call_id = CallId(str(uuid.uuid4()))
        promise_date = _future_promise_date()

        conns: list[Any] = [psycopg2.connect(POSTGRES_DSN) for _ in range(CONCURRENT_CALLERS)]
        try:
            services = [_build_service_on_conn(c) for c in conns]

            async def _fire(svc: PromiseToPayService) -> Any:
                return await svc.create(
                    tenant_id=tenant_id,
                    call_id=call_id,
                    customer_id=customer_id,
                    loan_account_id=loan_account_id,
                    promised_amount_minor=9_000_00,
                    currency="INR",
                    promise_date=promise_date,
                )

            results = await asyncio.gather(*[_fire(s) for s in services])

            first_id = results[0].ptp_id
            assert all(r.ptp_id == first_id for r in results), (
                f"Expected all {CONCURRENT_CALLERS} concurrent create() calls to return "
                f"the same ptp_id ({first_id}); got {[r.ptp_id for r in results]}"
            )
        finally:
            for c in conns:
                c.close()

        cur = pg_conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM promises_to_pay WHERE loan_account_id = %s",
            (loan_account_id,),
        )
        assert cur.fetchone()[0] == 1

        cur.execute(
            "SELECT COUNT(*) FROM idempotency_keys WHERE tenant_id = %s AND resource_type = 'ptp'",
            (str(tenant_id),),
        )
        assert cur.fetchone()[0] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
