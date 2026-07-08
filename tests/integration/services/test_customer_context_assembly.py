"""Integration tests: CustomerContext assembly + PTP idempotency (Sprint-022, V5 Ch4).

Required named tests: ``test_customer_context_assembly``, ``test_ptp_create_idempotent``,
``test_ptp_policy_check_called``.

Runs against real Postgres (migration 0019 applied via the session-scoped
``pg_conn`` fixture, `tests/integration/services/conftest.py`). PolicyEngine
uses a Phase 1 fake (no real PolicyEngineService/Redis dependency needed to
prove the collections-domain policy check fires).

Skipped when POSTGRES_DSN is not set.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from src.libs.contracts.models.customer import Customer
from src.libs.contracts.models.loan import EMIEntry, EMIStatus, LoanAccount, LoanOutstanding
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.repositories.customer import CustomerRepository
from src.libs.repositories.emi_schedule import EMIScheduleRepository
from src.libs.repositories.idempotency import IdempotencyRepository
from src.libs.repositories.loan_account import LoanAccountRepository
from src.libs.repositories.party import PartyRepository
from src.libs.repositories.promise_to_pay import PromiseToPayRepository
from src.services.collections.emi_schedule import EMIScheduleService
from src.services.collections.loan_account import LoanAccountService
from src.services.collections.promise_to_pay import PromiseToPayService
from src.services.crm.context_assembler import CustomerContextAssembler
from src.services.crm.party import PartyService
from src.services.crm.repository import CRMRepositories
from src.services.crm.service import CustomerService
from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome
from src.services.policy_engine.rule import PolicyRequest
from tests.fixtures.db import POSTGRES_DSN
from tests.integration.conftest import requires_postgres

_NOW = datetime(2026, 7, 6, tzinfo=UTC)


class _FakePolicyEngineService:
    """Records every PolicyRequest it's asked to evaluate; always PERMITs."""

    def __init__(self) -> None:
        self.requests: list[PolicyRequest] = []

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        return PolicyDecision(outcome=PolicyOutcome.PERMIT, reason="test-permit")


@pytest.fixture
def customer_and_loan(pg_conn: Any) -> Iterator[tuple[TenantId, CustomerId, str]]:
    """A real customer + loan account + one overdue EMI, cleaned up after the test."""
    tenant_id = TenantId(str(uuid.uuid4()))
    customer_id = CustomerId(str(uuid.uuid4()))
    loan_account_id = f"loan-{uuid.uuid4()}"

    CustomerRepository(pg_conn).create(
        Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            crm_id=f"crm-{uuid.uuid4()}",
            name="Context Assembly Test Customer",
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
            disbursed_amount_minor=500_000_00,
            currency="INR",
            interest_rate_bps=1200,
            tenure_months=24,
            disbursement_date=date(2025, 1, 1),
            maturity_date=date(2027, 1, 1),
            outstanding=LoanOutstanding(
                loan_account_id=loan_account_id,
                principal_minor=400_000_00,
                interest_minor=10_000_00,
                total_minor=410_000_00,
                currency="INR",
                as_of=_NOW,
            ),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    EMIScheduleRepository(pg_conn).create_entry(
        tenant_id,
        loan_account_id,
        EMIEntry(
            instalment_number=1,
            due_date=date.today() - timedelta(days=30),
            principal_minor=15_000_00,
            interest_minor=2_000_00,
            total_minor=17_000_00,
            status=EMIStatus.OVERDUE,
        ),
    )

    yield tenant_id, customer_id, loan_account_id

    cur = pg_conn.cursor()
    cur.execute("DELETE FROM promises_to_pay WHERE customer_id = %s", (customer_id,))
    cur.execute("DELETE FROM emi_entries WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM loan_accounts WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
    pg_conn.commit()


@requires_postgres
class TestCustomerContextAssembly:
    def test_customer_context_assembly(self, pg_conn: Any, customer_and_loan: tuple[TenantId, CustomerId, str]) -> None:
        """Required Sprint-022 test: assemble CustomerContext -> correct fields from real Postgres."""
        tenant_id, customer_id, loan_account_id = customer_and_loan
        emi_service = EMIScheduleService(EMIScheduleRepository(pg_conn))
        assembler = CustomerContextAssembler(
            CustomerService(CRMRepositories(customer=CustomerRepository(pg_conn), party=PartyRepository(pg_conn))),
            PartyService(PartyRepository(pg_conn)),
            LoanAccountService(LoanAccountRepository(pg_conn), emi_service),
            emi_service,
        )

        context = assembler.assemble(tenant_id, customer_id, call_id="call-1")

        assert context.customer_id == customer_id
        assert context.call_id == "call-1"
        assert len(context.loans) == 1
        assert context.loans[0].account_id == loan_account_id
        assert context.loans[0].outstanding_balance.amount_minor == 410_000_00
        assert context.loans[0].dpd == 30
        assert context.outstanding is not None
        assert context.outstanding.total_outstanding.amount_minor == 410_000_00


@requires_postgres
class TestPTPIdempotencyIntegration:
    def test_ptp_create_idempotent(self, pg_conn: Any, customer_and_loan: tuple[TenantId, CustomerId, str]) -> None:
        """Required Sprint-022 test: 10 concurrent PTP creations, same call+loan+date -> 1 record."""
        tenant_id, customer_id, loan_account_id = customer_and_loan
        call_id = CallId(str(uuid.uuid4()))
        promise_date = datetime.now(UTC) + timedelta(days=5)

        def run_once(_index: int) -> None:
            import psycopg2

            conn = psycopg2.connect(POSTGRES_DSN)
            try:
                emi_service = EMIScheduleService(EMIScheduleRepository(conn))
                guard = IdempotencyGuard(
                    IdempotencyRepository(conn), poll_interval_seconds=0.02, poll_timeout_seconds=10.0
                )
                service = PromiseToPayService(PromiseToPayRepository(conn), emi_service, idempotency_guard=guard)
                asyncio.run(
                    service.create(tenant_id, call_id, customer_id, loan_account_id, 20_000_00, "INR", promise_date)
                )
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(run_once, range(10)))

        cur = pg_conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM promises_to_pay WHERE tenant_id = %s AND call_id = %s AND loan_account_id = %s",
            (tenant_id, call_id, loan_account_id),
        )
        (record_count,) = cur.fetchone()

        assert record_count == 1

    def test_ptp_policy_check_called(self, pg_conn: Any, customer_and_loan: tuple[TenantId, CustomerId, str]) -> None:
        """Required Sprint-022 test: verify PolicyEngine.check is invoked on PTP creation."""
        tenant_id, customer_id, loan_account_id = customer_and_loan
        emi_service = EMIScheduleService(EMIScheduleRepository(pg_conn))
        policy = _FakePolicyEngineService()
        service = PromiseToPayService(
            PromiseToPayRepository(pg_conn),
            emi_service,
            policy_engine_service=policy,  # type: ignore[arg-type]
        )

        asyncio.run(
            service.create(
                tenant_id,
                CallId(str(uuid.uuid4())),
                customer_id,
                loan_account_id,
                20_000_00,
                "INR",
                datetime.now(UTC) + timedelta(days=5),
            )
        )

        assert len(policy.requests) == 1
        assert policy.requests[0].domain == "collections"
        assert policy.requests[0].action == "create_ptp"
