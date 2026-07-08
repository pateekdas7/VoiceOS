#!/usr/bin/env python3
"""Sprint-022 DR validation — PTP concurrent-creation idempotency, real Postgres.

Referenced by implementation/sprints/Sprint-022.md's DR Validation section:

    python3 scripts/validate/ptp_idempotency.py --concurrent 10
    # Expected: exactly 1 record in Postgres after 10 concurrent requests

Spawns ``--concurrent`` OS threads, each with its own psycopg2 connection,
all calling ``PromiseToPayService.create()`` with identical
(tenant_id, call_id, loan_account_id, promise_date). Exactly one row must
land in ``promises_to_pay`` for that combination (IdempotencyGuard +
PromiseToPayRepository.create_idempotent()'s own DB unique constraint).

Reads the DB credential from the POSTGRES_DSN environment variable — never
hardcoded here.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")

_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_NOW = datetime.now(UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrent", type=int, default=10, help="Number of concurrent callers")
    args = parser.parse_args()

    if not POSTGRES_DSN:
        print("ERROR: POSTGRES_DSN is not set. Export it and re-run.", file=sys.stderr)
        return 1

    import psycopg2

    from src.libs.contracts.models.customer import Customer
    from src.libs.contracts.models.loan import LoanAccount, LoanOutstanding
    from src.libs.contracts.primitives import CallId, CustomerId, TenantId
    from src.libs.idempotency.guard import IdempotencyGuard
    from src.libs.repositories.customer import CustomerRepository
    from src.libs.repositories.emi_schedule import EMIScheduleRepository
    from src.libs.repositories.idempotency import IdempotencyRepository
    from src.libs.repositories.loan_account import LoanAccountRepository
    from src.libs.repositories.promise_to_pay import PromiseToPayRepository
    from src.services.collections.emi_schedule import EMIScheduleService
    from src.services.collections.promise_to_pay import PromiseToPayService

    tenant_id = TenantId(_TENANT_ID)
    customer_id = CustomerId(str(uuid.uuid4()))
    loan_account_id = f"loan-drvalidation-{uuid.uuid4()}"
    call_id = CallId(str(uuid.uuid4()))
    promise_date = datetime.now(UTC) + timedelta(days=5)

    conn = psycopg2.connect(POSTGRES_DSN)
    CustomerRepository(conn).create(
        Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            crm_id=f"crm-drvalidation-{uuid.uuid4()}",
            name="PTP DR Validation Customer",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    LoanAccountRepository(conn).create(
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
            outstanding=LoanOutstanding(
                loan_account_id=loan_account_id,
                principal_minor=90_000_00,
                interest_minor=2_000_00,
                total_minor=92_000_00,
                currency="INR",
                as_of=_NOW,
            ),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    conn.commit()

    def run_once(_index: int) -> None:
        thread_conn = psycopg2.connect(POSTGRES_DSN)
        try:
            emi_service = EMIScheduleService(EMIScheduleRepository(thread_conn))
            guard = IdempotencyGuard(IdempotencyRepository(thread_conn), poll_interval_seconds=0.02)
            service = PromiseToPayService(PromiseToPayRepository(thread_conn), emi_service, idempotency_guard=guard)
            asyncio.run(
                service.create(tenant_id, call_id, customer_id, loan_account_id, 10_000_00, "INR", promise_date)
            )
        finally:
            thread_conn.close()

    print(f"Spawning {args.concurrent} concurrent callers for loan_account_id={loan_account_id!r} ...")
    with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
        list(pool.map(run_once, range(args.concurrent)))

    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM promises_to_pay WHERE tenant_id = %s AND call_id = %s AND loan_account_id = %s",
        (tenant_id, call_id, loan_account_id),
    )
    (record_count,) = cur.fetchone()
    # Cleanup: seeded fixture data only, no shared/production rows touched.
    cur.execute("DELETE FROM promises_to_pay WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM loan_accounts WHERE loan_account_id = %s", (loan_account_id,))
    cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
    conn.commit()
    conn.close()

    print(f"Postgres record count for this PTP: {record_count} (expected: 1)")
    ok = record_count == 1
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
