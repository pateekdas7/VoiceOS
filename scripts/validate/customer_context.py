#!/usr/bin/env python3
"""Sprint-022 DR validation — CustomerContext assembly, real Postgres.

Referenced by implementation/sprints/Sprint-022.md's DR Validation section:

    python3 scripts/validate/customer_context.py --customer-id test-customer-001
    # Expected: sealed CustomerContext with correct amounts and DPD

Seeds a real customer + loan account + one overdue EMI under the given
customer_id (idempotent — safe to re-run), assembles the CustomerContext via
CustomerContextAssembler, and prints the result. Cleans up nothing (the
seeded fixture is meant to persist across DR-drill re-runs, same as
seed-db.sh's test tenant/customers).

Reads the DB credential from the POSTGRES_DSN environment variable — never
hardcoded here.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")

_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_NOW = datetime.now(UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customer-id", default="test-customer-001", help="CRM external ID to seed/assemble")
    args = parser.parse_args()

    if not POSTGRES_DSN:
        print("ERROR: POSTGRES_DSN is not set. Export it and re-run.", file=sys.stderr)
        return 1

    import psycopg2

    from src.libs.contracts.models.customer import Customer
    from src.libs.contracts.models.loan import EMIEntry, EMIStatus, LoanAccount, LoanOutstanding
    from src.libs.contracts.primitives import CustomerId, TenantId
    from src.libs.repositories.customer import CustomerRepository
    from src.libs.repositories.emi_schedule import EMIScheduleRepository
    from src.libs.repositories.loan_account import LoanAccountRepository
    from src.libs.repositories.party import PartyRepository
    from src.services.collections.emi_schedule import EMIScheduleService
    from src.services.collections.loan_account import LoanAccountService
    from src.services.crm.context_assembler import CustomerContextAssembler
    from src.services.crm.party import PartyService
    from src.services.crm.repository import CRMRepositories
    from src.services.crm.service import CustomerService

    tenant_id = TenantId(_TENANT_ID)
    conn = psycopg2.connect(POSTGRES_DSN)
    customer_repo = CustomerRepository(conn)
    loan_repo = LoanAccountRepository(conn)
    emi_repo = EMIScheduleRepository(conn)

    customer_service = CustomerService(CRMRepositories(customer=customer_repo, party=PartyRepository(conn)))
    existing = customer_service.find_by_external_id(tenant_id, args.customer_id)
    if existing is not None:
        customer_id = existing.customer_id
        print(f"Customer {args.customer_id} already exists (customer_id={customer_id}) — reusing.")
    else:
        customer_id = CustomerId(str(uuid.uuid4()))
        customer_service.create(
            Customer(
                customer_id=customer_id,
                tenant_id=tenant_id,
                crm_id=args.customer_id,
                name="DR Validation Customer",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        loan_account_id = f"loan-{uuid.uuid4()}"
        loan_repo.create(
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
        emi_repo.create_entry(
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
        print(f"Seeded new customer {args.customer_id} (customer_id={customer_id}, loan={loan_account_id}).")

    emi_service = EMIScheduleService(emi_repo)
    assembler = CustomerContextAssembler(
        customer_service,
        PartyService(PartyRepository(conn)),
        LoanAccountService(loan_repo, emi_service),
        emi_service,
    )
    context = assembler.assemble(tenant_id, customer_id, call_id=f"dr-validation-{uuid.uuid4()}")
    conn.close()

    print(f"CustomerContext: customer_id={context.customer_id}")
    print(f"  loans: {len(context.loans)}")
    for loan in context.loans:
        print(f"    {loan.account_id}: outstanding={loan.outstanding_balance.amount_minor} dpd={loan.dpd}")
    print(f"  sealed/frozen: {context.model_config.get('frozen', False)}")

    ok = len(context.loans) >= 1 and context.model_config.get("frozen", False) is True
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
