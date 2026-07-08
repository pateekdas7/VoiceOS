"""Unit tests for the CRM domain (Sprint-022, V5 Ch3/Ch4).

All tests run fully in-process — no live Postgres required (Phase 1).
Repository interactions use small in-memory fake doubles, mirroring the
``_Fake*Repository`` precedent from ``test_tenant_management.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.libs.contracts.context import ConsentStatus
from src.libs.contracts.models.consent import Consent, ConsentType
from src.libs.contracts.models.customer import Customer, CustomerContact, Party, PartyRole
from src.libs.contracts.models.loan import EMIEntry, EMIStatus, LoanAccount, LoanOutstanding, LoanStatus
from src.libs.contracts.primitives import CustomerId, TenantId
from src.services.collections.emi_schedule import EMIScheduleService
from src.services.collections.loan_account import LoanAccountService
from src.services.crm.context_assembler import CustomerContextAssembler, CustomerNotFoundError
from src.services.crm.importer import CustomerImporter
from src.services.crm.party import PartyService
from src.services.crm.repository import CRMRepositories
from src.services.crm.service import CustomerService

_NOW = datetime(2026, 7, 6, tzinfo=UTC)
_TENANT = TenantId("tenant-a")


class _FakeCustomerRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Customer] = {}

    def create(self, customer: Customer) -> Customer:
        self._by_id[customer.customer_id] = customer
        return customer

    def get(self, tenant_id: TenantId, customer_id: CustomerId) -> Customer | None:
        return self._by_id.get(customer_id)

    def find_by_external_id(self, tenant_id: TenantId, crm_id: str) -> Customer | None:
        return next((c for c in self._by_id.values() if c.crm_id == crm_id), None)

    def find_by_phone(self, tenant_id: TenantId, phone: str) -> Customer | None:
        for c in self._by_id.values():
            if any(contact.value == phone for contact in c.contacts):
                return c
        return None


class _FakePartyRepository:
    def __init__(self) -> None:
        self._by_customer: dict[str, list[Party]] = {}

    def create(self, tenant_id: TenantId, party: Party) -> Party:
        self._by_customer.setdefault(party.customer_id, []).append(party)
        return party

    def find_by_customer(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[Party, ...]:
        return tuple(self._by_customer.get(customer_id, []))


class _FakeLoanAccountRepository:
    def __init__(self) -> None:
        self._loans: list[LoanAccount] = []

    def create(self, loan: LoanAccount) -> LoanAccount:
        self._loans.append(loan)
        return loan

    def get(self, tenant_id: TenantId, loan_account_id: str) -> LoanAccount | None:
        return next((loan for loan in self._loans if loan.loan_account_id == loan_account_id), None)

    def find_by_customer(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[LoanAccount, ...]:
        return tuple(loan for loan in self._loans if loan.customer_id == customer_id)

    def outstanding_balance(self, tenant_id: TenantId, loan_account_id: str) -> LoanOutstanding | None:
        loan = self.get(tenant_id, loan_account_id)
        return loan.outstanding if loan else None


class _FakeEMIScheduleRepository:
    def __init__(self) -> None:
        self._entries: dict[str, list[EMIEntry]] = {}

    def create_entry(self, tenant_id: TenantId, loan_account_id: str, entry: EMIEntry) -> EMIEntry:
        self._entries.setdefault(loan_account_id, []).append(entry)
        return entry

    def get_schedule(self, tenant_id: TenantId, loan_account_id: str, currency: str = "INR"):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def find_unpaid(self, tenant_id: TenantId, loan_account_id: str) -> tuple[EMIEntry, ...]:
        entries = self._entries.get(loan_account_id, [])
        unpaid = [e for e in entries if e.status in (EMIStatus.PENDING, EMIStatus.PARTIALLY_PAID, EMIStatus.OVERDUE)]
        return tuple(sorted(unpaid, key=lambda e: e.due_date))

    def update_payment(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError


class _FakeConsentRepository:
    def __init__(self, status: ConsentStatus | None = None) -> None:
        self._status = status

    def check_consent(self, tenant_id: TenantId, customer_id: CustomerId, consent_type: ConsentType) -> Consent | None:
        if self._status is None:
            return None
        return Consent(
            consent_id="consent-1",
            tenant_id=tenant_id,
            customer_id=customer_id,
            consent_type=consent_type,
            status=self._status,
            created_at=_NOW,
            updated_at=_NOW,
        )


def _make_customer(crm_id: str = "CRM-1") -> Customer:
    return Customer(
        customer_id=CustomerId(str(uuid.uuid4())),
        tenant_id=_TENANT,
        crm_id=crm_id,
        name="Asha Rao",
        contacts=(CustomerContact(contact_type="MOBILE", value="+919876543210", is_primary=True),),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_loan(customer_id: CustomerId, loan_account_id: str = "loan-1") -> LoanAccount:
    return LoanAccount(
        loan_account_id=loan_account_id,
        tenant_id=_TENANT,
        customer_id=customer_id,
        product_type="PERSONAL_LOAN",
        disbursed_amount_minor=500_000_00,
        currency="INR",
        interest_rate_bps=1200,
        tenure_months=24,
        disbursement_date=date(2025, 1, 1),
        maturity_date=date(2027, 1, 1),
        status=LoanStatus.DELINQUENT,
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


def _repos(customer_repo: _FakeCustomerRepository | None = None) -> CRMRepositories:
    # CRMRepositories/CustomerService are typed against the concrete
    # CustomerRepository/PartyRepository classes (not Protocols) — same
    # accepted fake-vs-concrete-type mypy --strict precedent as
    # test_customer_repository.py and others (see grep for "type: ignore[arg-type]").
    return CRMRepositories(
        customer=customer_repo or _FakeCustomerRepository(),  # type: ignore[arg-type]
        party=_FakePartyRepository(),  # type: ignore[arg-type]
    )


class TestCustomerService:
    def test_create_and_get_round_trips(self) -> None:
        service = CustomerService(_repos())
        customer = _make_customer()

        service.create(customer)

        assert service.get(_TENANT, customer.customer_id) == customer
        assert service.find_by_external_id(_TENANT, "CRM-1") == customer

    def test_find_by_external_id_missing_returns_none(self) -> None:
        service = CustomerService(_repos())

        assert service.find_by_external_id(_TENANT, "NOPE") is None


class TestPartyService:
    def test_add_and_list_parties(self) -> None:
        service = PartyService(_FakePartyRepository())  # type: ignore[arg-type]
        customer_id = CustomerId(str(uuid.uuid4()))

        service.add_party(_TENANT, customer_id, PartyRole.CO_BORROWER, "Ravi Rao")
        service.add_party(_TENANT, customer_id, PartyRole.GUARANTOR, "Meena Rao")

        parties = service.list_parties(_TENANT, customer_id)
        assert len(parties) == 2
        assert {p.role for p in parties} == {PartyRole.CO_BORROWER, PartyRole.GUARANTOR}


class TestCustomerImporter:
    def test_import_rows_creates_new_customers(self) -> None:
        importer = CustomerImporter(CustomerService(_repos()))

        result = importer.import_rows(
            _TENANT,
            [
                {"crm_id": "CRM-1", "name": "Asha Rao", "phone": "+919876543210"},
                {"crm_id": "CRM-2", "name": "Ravi Kumar", "phone": ""},
            ],
        )

        assert result.imported == 2
        assert result.skipped_duplicates == 0
        assert result.errors == []

    def test_import_rows_deduplicates_by_crm_id(self) -> None:
        service = CustomerService(_repos())
        service.create(_make_customer(crm_id="CRM-1"))
        importer = CustomerImporter(service)

        result = importer.import_rows(_TENANT, [{"crm_id": "CRM-1", "name": "Asha Rao (dup)"}])

        assert result.imported == 0
        assert result.skipped_duplicates == 1

    def test_import_rows_reports_missing_required_field(self) -> None:
        importer = CustomerImporter(CustomerService(_repos()))

        result = importer.import_rows(_TENANT, [{"crm_id": "", "name": "No CRM ID"}])

        assert result.imported == 0
        assert len(result.errors) == 1
        assert "crm_id" in result.errors[0]


class TestCustomerContextAssembler:
    def _assembler(
        self,
        customer_repo: _FakeCustomerRepository,
        party_repo: _FakePartyRepository,
        loan_repo: _FakeLoanAccountRepository,
        emi_repo: _FakeEMIScheduleRepository,
        consent_repo: _FakeConsentRepository | None = None,
    ) -> CustomerContextAssembler:
        emi_service = EMIScheduleService(emi_repo)  # type: ignore[arg-type]
        return CustomerContextAssembler(
            CustomerService(CRMRepositories(customer=customer_repo, party=party_repo)),  # type: ignore[arg-type]
            PartyService(party_repo),  # type: ignore[arg-type]
            LoanAccountService(loan_repo, emi_service),  # type: ignore[arg-type]
            emi_service,
            consent_repository=consent_repo,  # type: ignore[arg-type]
        )

    def test_assemble_raises_when_customer_not_found(self) -> None:
        assembler = self._assembler(
            _FakeCustomerRepository(),
            _FakePartyRepository(),
            _FakeLoanAccountRepository(),
            _FakeEMIScheduleRepository(),
        )

        with pytest.raises(CustomerNotFoundError):
            assembler.assemble(_TENANT, CustomerId("missing"), "call-1")

    def test_assemble_builds_primary_party_and_loans(self) -> None:
        customer_repo = _FakeCustomerRepository()
        loan_repo = _FakeLoanAccountRepository()
        emi_repo = _FakeEMIScheduleRepository()
        customer = _make_customer()
        customer_repo.create(customer)
        loan = _make_loan(customer.customer_id)
        loan_repo.create(loan)
        emi_repo.create_entry(
            _TENANT,
            loan.loan_account_id,
            EMIEntry(
                instalment_number=1,
                due_date=date(2026, 6, 6),
                principal_minor=15_000_00,
                interest_minor=2_000_00,
                total_minor=17_000_00,
                status=EMIStatus.OVERDUE,
            ),
        )
        assembler = self._assembler(customer_repo, _FakePartyRepository(), loan_repo, emi_repo)

        context = assembler.assemble(_TENANT, customer.customer_id, "call-1")

        assert context.customer_id == customer.customer_id
        assert context.primary_party.name == "Asha Rao"
        assert len(context.loans) == 1
        assert context.loans[0].outstanding_balance.amount_minor == 410_000_00
        assert context.outstanding is not None
        assert context.outstanding.total_outstanding.amount_minor == 410_000_00
        assert context.call_id == "call-1"

    def test_customer_context_immutable(self) -> None:
        """Required Sprint-022 test: attempt to set a field after assembly -> raises."""
        customer_repo = _FakeCustomerRepository()
        customer = _make_customer()
        customer_repo.create(customer)
        assembler = self._assembler(
            customer_repo, _FakePartyRepository(), _FakeLoanAccountRepository(), _FakeEMIScheduleRepository()
        )

        context = assembler.assemble(_TENANT, customer.customer_id, "call-1")

        with pytest.raises(ValidationError):
            context.call_id = "call-2"  # type: ignore[misc]

    def test_assemble_resolves_consent_status_from_repository(self) -> None:
        customer_repo = _FakeCustomerRepository()
        customer = _make_customer()
        customer_repo.create(customer)
        assembler = self._assembler(
            customer_repo,
            _FakePartyRepository(),
            _FakeLoanAccountRepository(),
            _FakeEMIScheduleRepository(),
            consent_repo=_FakeConsentRepository(ConsentStatus.GRANTED),
        )

        context = assembler.assemble(_TENANT, customer.customer_id, "call-1")

        assert context.consent_status == ConsentStatus.GRANTED

    def test_assemble_defaults_consent_to_pending_without_repository(self) -> None:
        customer_repo = _FakeCustomerRepository()
        customer = _make_customer()
        customer_repo.create(customer)
        assembler = self._assembler(
            customer_repo, _FakePartyRepository(), _FakeLoanAccountRepository(), _FakeEMIScheduleRepository()
        )

        context = assembler.assemble(_TENANT, customer.customer_id, "call-1")

        assert context.consent_status == ConsentStatus.PENDING

    def test_assemble_with_no_loans_leaves_outstanding_none(self) -> None:
        customer_repo = _FakeCustomerRepository()
        customer = _make_customer()
        customer_repo.create(customer)
        assembler = self._assembler(
            customer_repo, _FakePartyRepository(), _FakeLoanAccountRepository(), _FakeEMIScheduleRepository()
        )

        context = assembler.assemble(_TENANT, customer.customer_id, "call-1")

        assert context.loans == ()
        assert context.outstanding is None
