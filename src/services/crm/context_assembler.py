"""CustomerContextAssembler — builds the authoritative CustomerContext (V5 Ch4).

Assembles the sealed, immutable ``CustomerContext`` (defined in
``src/libs/contracts/context.py``, Sprint-001/002) from the CRM (customer,
parties, consent) and Collections (loan accounts, real-time DPD, EMI
schedule) systems of record. This is *the* implementation of RI-5 (Law of
Authority) for the conversation pipeline: every amount and date placed on the
returned context passes through ``assert_ri5_law_of_authority`` first.

Called once per call at call start (Sprint-022.md); re-assembled fresh on
call recovery/resume rather than reused from a cache (DocSuite-03 B.3 "re-read
fresh on recovery"), since a stale snapshot could go stale mid-outage.

Architecture: V5 Ch4 (CRM), Ch5 (Loan & Collections); V2 Ch1 (Law of
Authority); DocSuite-03. Invariant: RI-5.
"""

from __future__ import annotations

import time

from src.libs.contracts.context import (
    ConsentStatus,
    ContactInfo,
    CustomerContext,
    LoanSummary,
    OutstandingBalance,
    PartyInfo,
)
from src.libs.contracts.models.consent import ConsentType
from src.libs.contracts.models.customer import Customer
from src.libs.contracts.primitives import AccountId, Currency, CustomerId, Money, PhoneNumber, TenantId
from src.libs.invariants import assert_ri5_law_of_authority
from src.libs.repositories.consent import ConsentRepository
from src.services.collections.emi_schedule import EMIScheduleService
from src.services.collections.loan_account import LoanAccountService

from . import metrics
from .party import PartyService
from .service import CustomerService

AUTHORITATIVE_SOURCE = "crm_collections"
"""The only source string RI-5 accepts for facts assembled here."""

_AUTHORIZED_SOURCES: set[str] = {AUTHORITATIVE_SOURCE}


class CustomerNotFoundError(LookupError):
    """The customer_id requested for context assembly does not exist for this tenant."""


class CustomerContextAssembler:
    """Assembles the single authoritative ``CustomerContext`` for a call (V5 Ch4)."""

    def __init__(
        self,
        customer_service: CustomerService,
        party_service: PartyService,
        loan_account_service: LoanAccountService,
        emi_schedule_service: EMIScheduleService,
        consent_repository: ConsentRepository | None = None,
    ) -> None:
        self._customers = customer_service
        self._parties = party_service
        self._loans = loan_account_service
        self._emi = emi_schedule_service
        self._consent_repo = consent_repository

    def assemble(self, tenant_id: TenantId, customer_id: CustomerId, call_id: str) -> CustomerContext:
        """Build the sealed CustomerContext for one call.

        Raises:
            CustomerNotFoundError: No customer with ``customer_id`` exists for ``tenant_id``.
        """
        start = time.perf_counter()
        customer = self._customers.get(tenant_id, customer_id)
        if customer is None:
            raise CustomerNotFoundError(f"customer not found: tenant={tenant_id} customer_id={customer_id}")

        primary_contact = self._primary_contact(customer)
        primary_party = PartyInfo(
            party_id=customer_id,
            role="PRIMARY_BORROWER",
            name=customer.name,
            contact=primary_contact,
            identity_verified=False,
        )
        additional_parties = tuple(
            PartyInfo(
                party_id=CustomerId(party.party_id),
                role=party.role.value,
                name=party.name,
                # The `parties` table has no per-party contact columns (V5 Ch3.3) —
                # additional parties share the primary customer's contact record.
                contact=primary_contact,
            )
            for party in self._parties.list_parties(tenant_id, customer_id)
        )

        loan_summaries, outstanding = self._assemble_loans(tenant_id, customer_id)

        consent_status = self._resolve_consent(tenant_id, customer_id)

        context = CustomerContext(
            customer_id=customer_id,
            tenant_id=tenant_id,
            primary_party=primary_party,
            additional_parties=additional_parties,
            loans=loan_summaries,
            outstanding=outstanding,
            consent_status=consent_status,
            call_id=call_id,
        )

        duration_ms = (time.perf_counter() - start) * 1000
        metrics.record_context_assembly_latency_ms(duration_ms)
        return context

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _assemble_loans(
        self, tenant_id: TenantId, customer_id: CustomerId
    ) -> tuple[tuple[LoanSummary, ...], OutstandingBalance | None]:
        loans = self._loans.find_by_customer(tenant_id, customer_id)
        summaries: list[LoanSummary] = []
        total_outstanding_minor = 0
        total_overdue_minor = 0
        currency = Currency.INR

        for loan in loans:
            currency = Currency(loan.currency)
            dpd = self._loans.calculate_dpd(tenant_id, loan.loan_account_id)
            next_emi = self._emi.next_unpaid_emi(tenant_id, loan.loan_account_id)
            overdue_minor = self._emi.total_overdue_minor(tenant_id, loan.loan_account_id)
            outstanding_minor = loan.outstanding.total_minor if loan.outstanding is not None else 0

            self._assert_authoritative("outstanding_balance", outstanding_minor)
            self._assert_authoritative("dpd", dpd)
            self._assert_authoritative("total_overdue", overdue_minor)
            self._assert_authoritative("sanctioned_amount", loan.disbursed_amount_minor)
            if next_emi is not None:
                self._assert_authoritative("next_emi_amount", next_emi.total_minor)
                self._assert_authoritative("next_emi_date", next_emi.due_date)
            self._assert_authoritative("loan_start_date", loan.disbursement_date)

            summaries.append(
                LoanSummary(
                    account_id=AccountId(loan.loan_account_id),
                    product_type=loan.product_type,
                    outstanding_balance=Money(amount_minor=outstanding_minor, currency=currency),
                    dpd=dpd,
                    next_emi_date=next_emi.due_date if next_emi is not None else None,
                    next_emi_amount=(
                        Money(amount_minor=next_emi.total_minor, currency=currency) if next_emi is not None else None
                    ),
                    total_overdue=Money(amount_minor=overdue_minor, currency=currency),
                    loan_start_date=loan.disbursement_date,
                    sanctioned_amount=Money(amount_minor=loan.disbursed_amount_minor, currency=currency),
                )
            )
            total_outstanding_minor += outstanding_minor
            total_overdue_minor += overdue_minor

        if not summaries:
            return (), None

        self._assert_authoritative("total_outstanding", total_outstanding_minor)
        self._assert_authoritative("total_overdue_aggregate", total_overdue_minor)
        outstanding = OutstandingBalance(
            total_outstanding=Money(amount_minor=total_outstanding_minor, currency=currency),
            total_overdue=Money(amount_minor=total_overdue_minor, currency=currency),
            account_count=len(summaries),
        )
        return tuple(summaries), outstanding

    def _resolve_consent(self, tenant_id: TenantId, customer_id: CustomerId) -> ConsentStatus:
        if self._consent_repo is None:
            return ConsentStatus.PENDING
        consent = self._consent_repo.check_consent(tenant_id, customer_id, ConsentType.VOICE_RECORDING)
        return consent.status if consent is not None else ConsentStatus.PENDING

    @staticmethod
    def _primary_contact(customer: Customer) -> ContactInfo:
        contacts = customer.contacts
        primary = next((c for c in contacts if c.is_primary), contacts[0] if contacts else None)
        phone = primary.value if primary is not None else ""
        return ContactInfo(phone_number=PhoneNumber(phone), preferred_language=customer.preferred_language)

    @staticmethod
    def _assert_authoritative(fact_key: str, value: object) -> None:
        assert_ri5_law_of_authority(fact_key, value, _AUTHORIZED_SOURCES, AUTHORITATIVE_SOURCE)
