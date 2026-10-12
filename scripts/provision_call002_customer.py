"""Provisions the real Postgres customer/loan/consent rows Call-002 needs.

Call-002 (free-form live trial) is placed to the founder's real phone
number with the founder's real name ("Prateek Das"). CustomerContextAssembler
resolves CustomerContext from real `customers`/`loan_accounts`/`emi_entries`/
`consents` rows (RI-5, Law of Authority) -- it never accepts an in-memory
context -- so this call needs a real row to look up, exactly like any real
production customer would have.

Idempotent: safe to re-run. Uses the real CustomerRepository/LoanAccountService/
EMIScheduleService/ConsentRepository code paths (not hand-rolled SQL against
tables those repositories don't own), mirroring deployment/cpu/app.py's own
composition (`build_customer_context_assembler`).

Must be run with the same env vars as the WS server (POSTGRES_DSN) so it
writes to the same database. On the CPU node:
    sudo -u postgres /opt/voiceos/venv/bin/python scripts/provision_call002_customer.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deployment", "cpu"))

TENANT_ID = "13b3b771-fe09-430b-9e7e-6995da366c45"
"""customers.tenant_id is a Postgres `uuid` column (no FK to `tenants` --
tenant isolation is enforced at query level, AR-8 -- but the column type
itself rejects non-UUID text). DEFAULT_TENANT_ID must be updated on the
CPU node to this same value for ConversationEngine.start_call() to resolve
this customer."""
CUSTOMER_ID = "95dc31bc-f4b0-40cc-9cb9-78d250c043a4"  # customers.customer_id is also `uuid`
LOAN_ACCOUNT_ID = "acc-prateek-das-001"  # loan_accounts.loan_account_id is `text` -- free-form OK
PHONE = "+919911954448"
CUSTOMER_NAME = "Prateek Das"
OUTSTANDING_MINOR = 5_000_000  # INR 50,000 -- same demo scenario as Call-001/Phase-7 dry run
DPD_TARGET = 45


def main() -> None:
    import app as composition_root  # deployment/cpu/app.py

    from src.libs.contracts.models.consent import ConsentType
    from src.libs.contracts.models.customer import Customer, CustomerContact
    from src.libs.contracts.models.loan import EMIEntry, EMIStatus, LoanAccount, LoanOutstanding, LoanStatus
    from src.libs.contracts.primitives import CustomerId, TenantId
    from src.libs.repositories.consent import ConsentRepository
    from src.libs.repositories.customer import CustomerRepository
    from src.libs.repositories.emi_schedule import EMIScheduleRepository
    from src.libs.repositories.loan_account import LoanAccountRepository
    from src.services.collections.emi_schedule import EMIScheduleService
    from src.services.collections.loan_account import LoanAccountService

    tenant_id = TenantId(TENANT_ID)
    customer_id = CustomerId(CUSTOMER_ID)
    now = datetime.utcnow()
    today = date.today()

    conn = composition_root.build_postgres_connection()
    customers = CustomerRepository(conn)
    loans = LoanAccountRepository(conn)
    emi_repo = EMIScheduleRepository(conn)
    emi_service = EMIScheduleService(repository=emi_repo)
    loan_service = LoanAccountService(repository=loans, emi_schedule_service=emi_service)
    consents = ConsentRepository(conn)

    print("=" * 70)
    print(f"Provisioning Call-002 customer: {CUSTOMER_ID} / {LOAN_ACCOUNT_ID}")
    print("=" * 70)

    existing_customer = customers.get(tenant_id, customer_id)
    if existing_customer is not None:
        print(f"[1/4] Customer {CUSTOMER_ID} already exists -- skipping create.")
    else:
        customer = Customer(
            customer_id=customer_id,
            tenant_id=tenant_id,
            crm_id="crm-prateek-das-001",
            name=CUSTOMER_NAME,
            preferred_language="hi",
            contacts=(
                CustomerContact(
                    contact_type="MOBILE", value=PHONE, is_primary=True, is_dnc=False, consent_captured=True
                ),
            ),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        customers.create(customer)
        print(f"[1/4] Created customer {CUSTOMER_ID} ({CUSTOMER_NAME}, {PHONE}).")

    existing_loan = loans.get(tenant_id, LOAN_ACCOUNT_ID)
    if existing_loan is not None:
        print(f"[2/4] Loan account {LOAN_ACCOUNT_ID} already exists -- skipping create.")
    else:
        disbursement_date = today - timedelta(days=200)
        maturity_date = disbursement_date + timedelta(days=365)
        loan = LoanAccount(
            loan_account_id=LOAN_ACCOUNT_ID,
            tenant_id=tenant_id,
            customer_id=customer_id,
            product_type="PERSONAL_LOAN",
            disbursed_amount_minor=OUTSTANDING_MINOR + 1_500_000,
            currency="INR",
            interest_rate_bps=1500,
            tenure_months=12,
            disbursement_date=disbursement_date,
            maturity_date=maturity_date,
            status=LoanStatus.DELINQUENT,
            dpd=DPD_TARGET,
            outstanding=LoanOutstanding(
                loan_account_id=LOAN_ACCOUNT_ID,
                principal_minor=int(OUTSTANDING_MINOR * 0.9),
                interest_minor=int(OUTSTANDING_MINOR * 0.1),
                penalty_minor=0,
                total_minor=OUTSTANDING_MINOR,
                currency="INR",
                as_of=now,
            ),
            created_at=now,
            updated_at=now,
        )
        loan_service.create(loan)
        print(f"[2/4] Created loan account {LOAN_ACCOUNT_ID} (outstanding=INR {OUTSTANDING_MINOR / 100:,.2f}).")

    existing_schedule = emi_service.get_schedule(tenant_id, LOAN_ACCOUNT_ID)
    if existing_schedule.instalments:
        print(f"[3/4] EMI schedule for {LOAN_ACCOUNT_ID} already has {len(existing_schedule.instalments)} rows -- skipping.")
    else:
        emi_total_minor = (OUTSTANDING_MINOR + 1_500_000) // 12
        entries = []
        for i in range(1, 13):
            due_date = today - timedelta(days=200) + timedelta(days=30 * i)
            # First 2 instalments are overdue+unpaid (drives DPD ~= 45); rest pending/future.
            is_overdue_unpaid = i <= 2
            entries.append(
                EMIEntry(
                    instalment_number=i,
                    due_date=due_date,
                    principal_minor=int(emi_total_minor * 0.85),
                    interest_minor=int(emi_total_minor * 0.15),
                    total_minor=emi_total_minor,
                    paid_minor=0 if is_overdue_unpaid or due_date > today else emi_total_minor,
                    status=(
                        EMIStatus.OVERDUE
                        if is_overdue_unpaid
                        else (EMIStatus.PENDING if due_date > today else EMIStatus.PAID)
                    ),
                )
            )
        emi_service.create_schedule(tenant_id, LOAN_ACCOUNT_ID, tuple(entries))
        print(f"[3/4] Created 12-instalment EMI schedule (2 overdue unpaid) for {LOAN_ACCOUNT_ID}.")

    existing_consent = consents.check_consent(tenant_id, customer_id, ConsentType.VOICE_RECORDING)
    if existing_consent is not None and existing_consent.status.value == "GRANTED":
        print("[4/4] VOICE_RECORDING consent already GRANTED -- skipping.")
    else:
        consents.record_grant(
            tenant_id, customer_id, ConsentType.VOICE_RECORDING, actor_id="call002-provisioning-script", channel="SYSTEM"
        )
        print("[4/4] Granted VOICE_RECORDING consent.")

    real_dpd = emi_service.calculate_dpd(tenant_id, LOAN_ACCOUNT_ID)
    real_overdue = emi_service.total_overdue_minor(tenant_id, LOAN_ACCOUNT_ID)
    print("\nVerification (as CustomerContextAssembler will compute it):")
    print(f"  real-time DPD           = {real_dpd}")
    print(f"  real-time overdue_minor = {real_overdue} (INR {real_overdue / 100:,.2f})")
    print(f"\ncustomer_id = {CUSTOMER_ID}")
    print(f"tenant_id   = {TENANT_ID}")


if __name__ == "__main__":
    main()
