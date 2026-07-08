#!/usr/bin/env python3
"""Sprint-022 Phase 2 infrastructure validation — run against the real
Postgres (migration 0019) on the CPU node.

Exercises the scenarios in implementation/sprints/Sprint-022.md's Phase 2
"Integration validation"/"Infrastructure Validation" sections against real
infrastructure. Not part of the pytest suite (pytest coverage of the same
behaviors lives in tests/unit/services/test_{crm,collections,ptp_idempotency}.py
and tests/integration/services/test_customer_context_assembly.py) — this is
an operational smoke-test / evidence script, following the Sprint-013/.../021
precedent.

Usage:
    POSTGRES_DSN=<dsn> python scripts/sprint022_infra_validation.py
"""

from __future__ import annotations

import asyncio
import datetime
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.models.loan import EMIEntry, EMIStatus, LoanAccount, LoanOutstanding
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.invariants import InvariantViolationError, assert_ri5_law_of_authority
from src.libs.repositories.audit import AuditRepository
from src.libs.repositories.customer import CustomerRepository
from src.libs.repositories.emi_schedule import EMIScheduleRepository
from src.libs.repositories.idempotency import IdempotencyRepository
from src.libs.repositories.loan_account import LoanAccountRepository
from src.libs.repositories.party import PartyRepository
from src.libs.repositories.promise_to_pay import PromiseToPayRepository
from src.libs.repositories.settlement import SettlementRepository
from src.services.collections.emi_schedule import EMIScheduleService
from src.services.collections.loan_account import LoanAccountService
from src.services.collections.promise_to_pay import PromiseToPayService
from src.services.collections.settlement import SettlementService
from src.services.crm.context_assembler import CustomerContextAssembler
from src.services.crm.party import PartyService
from src.services.crm.repository import CRMRepositories
from src.services.crm.service import CustomerService

POSTGRES_DSN = os.environ["POSTGRES_DSN"]

_NOW = datetime.datetime.now(datetime.UTC)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    conn = psycopg2.connect(POSTGRES_DSN)
    print(f"Connected to Postgres: {POSTGRES_DSN.split('@')[-1]}")
    print()

    tenant_id = TenantId(str(uuid.uuid4()))
    customer_id = CustomerId(str(uuid.uuid4()))
    loan_account_id = f"loan-{uuid.uuid4()}"

    try:
        customer_repo = CustomerRepository(conn)
        party_repo = PartyRepository(conn)
        loan_repo = LoanAccountRepository(conn)
        emi_repo = EMIScheduleRepository(conn)
        ptp_repo = PromiseToPayRepository(conn)
        settlement_repo = SettlementRepository(conn)
        audit_repo = AuditRepository(conn)
        audit_logger = AuditLogger(audit_repo)

        customer_repo.create(
            Customer(
                customer_id=customer_id,
                tenant_id=tenant_id,
                crm_id=f"crm-{uuid.uuid4()}",
                name="Sprint-022 Validation Customer",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
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

        # 1. CustomerContext assembly from real Postgres data -> correct amounts and DPD.
        emi_service = EMIScheduleService(emi_repo)
        assembler = CustomerContextAssembler(
            CustomerService(CRMRepositories(customer=customer_repo, party=party_repo)),
            PartyService(party_repo),
            LoanAccountService(loan_repo, emi_service),
            emi_service,
        )
        context = assembler.assemble(tenant_id, customer_id, call_id="validation-call-1")
        assembly_ok = (
            len(context.loans) == 1
            and context.loans[0].outstanding_balance.amount_minor == 410_000_00
            and context.loans[0].dpd == 30
            and context.outstanding is not None
            and context.outstanding.total_outstanding.amount_minor == 410_000_00
        )
        results.append(
            (
                "CustomerContextAssembler.assemble() -> correct amounts + DPD from real Postgres",
                assembly_ok,
                f"outstanding={context.loans[0].outstanding_balance.amount_minor} dpd={context.loans[0].dpd}",
            )
        )

        # 2. CustomerContext is sealed (immutable) -> mutation raises.
        immutable_raised = False
        try:
            context.call_id = "tampered"  # type: ignore[misc]
        except Exception:
            immutable_raised = True
        results.append(
            ("CustomerContext immutability: mutation attempt raises", immutable_raised, f"raised={immutable_raised}")
        )

        # 3. RI-5 Law of Authority guard: unauthorized source raises, authoritative source passes.
        ri5_blocks_unauthorized = False
        try:
            assert_ri5_law_of_authority("outstanding_balance", 100, {"crm_collections"}, "llm_hallucination")
        except InvariantViolationError:
            ri5_blocks_unauthorized = True
        ri5_permits_authoritative = True
        try:
            assert_ri5_law_of_authority("outstanding_balance", 100, {"crm_collections"}, "crm_collections")
        except InvariantViolationError:
            ri5_permits_authoritative = False
        results.append(
            (
                "RI-5 Law of Authority: blocks unauthorized source, permits crm_collections",
                ri5_blocks_unauthorized and ri5_permits_authoritative,
                f"blocks_unauthorized={ri5_blocks_unauthorized} permits_authoritative={ri5_permits_authoritative}",
            )
        )

        # 4. PTP concurrent creation: 10 concurrent requests -> 1 Postgres row.
        ptp_call_id = CallId(str(uuid.uuid4()))
        promise_date = datetime.datetime.now(datetime.UTC) + timedelta(days=5)

        def run_ptp_once(_index: int) -> None:
            thread_conn = psycopg2.connect(POSTGRES_DSN)
            try:
                thread_emi = EMIScheduleService(EMIScheduleRepository(thread_conn))
                guard = IdempotencyGuard(IdempotencyRepository(thread_conn), poll_interval_seconds=0.02)
                service = PromiseToPayService(PromiseToPayRepository(thread_conn), thread_emi, idempotency_guard=guard)
                asyncio.run(
                    service.create(tenant_id, ptp_call_id, customer_id, loan_account_id, 20_000_00, "INR", promise_date)
                )
            finally:
                thread_conn.close()

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(run_ptp_once, range(10)))
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM promises_to_pay WHERE tenant_id = %s AND call_id = %s", (tenant_id, ptp_call_id)
        )
        (ptp_count,) = cur.fetchone()
        results.append(("PTP concurrent creation: 10 requests -> 1 Postgres row", ptp_count == 1, f"count={ptp_count}"))

        # 5. PolicyDecisionMade / PTP creation audit event emitted.
        ptp_via_audit = PromiseToPayService(ptp_repo, emi_service, audit_logger=audit_logger)
        ptp2_call_id = CallId(str(uuid.uuid4()))
        asyncio.run(
            ptp_via_audit.create(tenant_id, ptp2_call_id, customer_id, loan_account_id, 20_000_00, "INR", promise_date)
        )
        cur.execute(
            "SELECT COUNT(*) FROM audit_log WHERE tenant_id = %s AND resource_type = 'PromiseToPay'", (tenant_id,)
        )
        (audit_count,) = cur.fetchone()
        results.append(("PTP creation audit event persisted", audit_count >= 1, f"audit_rows={audit_count}"))

        # 6. Settlement workflow: offer -> accept -> authorize transitions via real Postgres.
        settlement_service = SettlementService(settlement_repo, approval_threshold_minor=50_000_00)
        offer = settlement_service.offer(
            tenant_id, customer_id, loan_account_id, 100_000_00, 20_000_00, "INR", _NOW + timedelta(days=7)
        )
        accepted = settlement_service.accept(tenant_id, offer.settlement_id)
        authorized = settlement_service.authorize(tenant_id, offer.settlement_id, approved_by="validation-supervisor")
        disbursed = settlement_service.disburse(tenant_id, offer.settlement_id)
        results.append(
            (
                "Settlement workflow: offer->accept->authorize->disburse",
                accepted.status.value == "ACCEPTED"
                and authorized.status.value == "ACCEPTED"
                and disbursed.status.value == "PAID",
                f"accepted={accepted.status.value} disbursed={disbursed.status.value}",
            )
        )

        # 7. context_assembly_latency_ms: p99 target check (single-sample smoke, not a real p99).
        import time

        start = time.perf_counter()
        assembler.assemble(tenant_id, customer_id, call_id="validation-call-2")
        elapsed_ms = (time.perf_counter() - start) * 1000
        results.append(
            (
                "context_assembly_latency_ms < 100ms (Sprint-022 AC pass/fail bar)",
                elapsed_ms < 100,
                f"{elapsed_ms:.2f}ms",
            )
        )
    finally:
        # audit_log is intentionally append-only (Sprint-020 immutability
        # trigger, V4 Ch11) — its rows from this run are never deleted.
        cur = conn.cursor()
        cur.execute("DELETE FROM promises_to_pay WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM settlements WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM emi_entries WHERE loan_account_id = %s", (loan_account_id,))
        cur.execute("DELETE FROM loan_accounts WHERE loan_account_id = %s", (loan_account_id,))
        cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))
        conn.commit()
        conn.close()

    print(f"{'CHECK':<75} {'RESULT':<8} DETAIL")
    print("-" * 125)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<75} {status_str:<8} {detail}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
