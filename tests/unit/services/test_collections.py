"""Unit tests for the Collections domain (Sprint-022, V5 Ch4).

All tests run fully in-process — no live Postgres required (Phase 1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.libs.contracts.models.collections import CallbackRequest, EscalationRecord, Settlement, SettlementStatus
from src.libs.contracts.models.loan import EMIEntry, EMIStatus, LoanAccount, LoanOutstanding, LoanStatus
from src.libs.contracts.primitives import CallId, CustomerId, TenantId
from src.services.collections.callback import CallbackScheduler
from src.services.collections.emi_schedule import EMIScheduleService
from src.services.collections.escalation import EscalationTarget, EscalationWorkflow
from src.services.collections.loan_account import LoanAccountService
from src.services.collections.settlement import SettlementService, SettlementTransitionError

_NOW = datetime(2026, 7, 6, tzinfo=UTC)
_TENANT = TenantId("tenant-a")
_TODAY = date(2026, 7, 6)


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

    def update_payment(
        self, tenant_id: TenantId, loan_account_id: str, instalment_number: int, paid_minor: int, status: EMIStatus
    ) -> None:
        entries = self._entries.get(loan_account_id, [])
        for i, entry in enumerate(entries):
            if entry.instalment_number == instalment_number:
                entries[i] = entry.model_copy(update={"paid_minor": paid_minor, "status": status})


class _FakeSettlementRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Settlement] = {}
        self._approved_by: dict[str, str] = {}

    def create(self, settlement: Settlement) -> Settlement:
        self._by_id[settlement.settlement_id] = settlement
        return settlement

    def get(self, tenant_id: TenantId, settlement_id: str) -> Settlement | None:
        return self._by_id.get(settlement_id)

    def find_by_customer(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[Settlement, ...]:
        return tuple(s for s in self._by_id.values() if s.customer_id == customer_id)

    def update_status(self, tenant_id: TenantId, settlement_id: str, status: SettlementStatus) -> None:
        current = self._by_id[settlement_id]
        self._by_id[settlement_id] = current.model_copy(update={"status": status})

    def get_approved_by(self, tenant_id: TenantId, settlement_id: str) -> str | None:
        return self._approved_by.get(settlement_id)

    def record_authorization(
        self, tenant_id: TenantId, settlement_id: str, approved_by: str, authorized_at: object
    ) -> None:
        self._approved_by[settlement_id] = approved_by


class _FakeCallbackRepository:
    def __init__(self) -> None:
        self._items: list[CallbackRequest] = []

    def create(self, callback: CallbackRequest) -> CallbackRequest:
        self._items.append(callback)
        return callback

    def find_pending(self, tenant_id: TenantId, customer_id: CustomerId) -> tuple[CallbackRequest, ...]:
        return tuple(c for c in self._items if c.customer_id == customer_id and not c.is_fulfilled)

    def mark_fulfilled(self, tenant_id: TenantId, callback_id: str) -> None:
        for i, c in enumerate(self._items):
            if c.callback_id == callback_id:
                self._items[i] = c.model_copy(update={"is_fulfilled": True})


class _FakeEscalationRepository:
    def __init__(self) -> None:
        self._items: list[EscalationRecord] = []

    def create(self, escalation: EscalationRecord) -> EscalationRecord:
        self._items.append(escalation)
        return escalation

    def find_by_call(self, tenant_id: TenantId, call_id: CallId) -> tuple[EscalationRecord, ...]:
        return tuple(e for e in self._items if e.call_id == call_id)

    def resolve(self, tenant_id: TenantId, escalation_id: str, resolved_at: object, resolution_notes: str) -> None:
        for i, e in enumerate(self._items):
            if e.escalation_id == escalation_id:
                self._items[i] = e.model_copy(update={"resolved_at": resolved_at, "resolution_notes": resolution_notes})


def _make_loan(loan_account_id: str = "loan-1") -> LoanAccount:
    return LoanAccount(
        loan_account_id=loan_account_id,
        tenant_id=_TENANT,
        customer_id=CustomerId("cust-1"),
        product_type="PERSONAL_LOAN",
        disbursed_amount_minor=500_000_00,
        currency="INR",
        interest_rate_bps=1200,
        tenure_months=24,
        disbursement_date=date(2025, 1, 1),
        maturity_date=date(2027, 1, 1),
        status=LoanStatus.DELINQUENT,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestEMIScheduleServiceDPD:
    def test_dpd_calculation(self) -> None:
        """Required Sprint-022 test: 3 unpaid EMIs, oldest due 30 days ago -> DPD = 30."""
        repo = _FakeEMIScheduleRepository()
        service = EMIScheduleService(repo)  # type: ignore[arg-type]
        loan_account_id = "loan-1"
        for i, days_ago in enumerate((30, 0, -30), start=1):
            repo.create_entry(
                _TENANT,
                loan_account_id,
                EMIEntry(
                    instalment_number=i,
                    due_date=_TODAY - timedelta(days=days_ago),
                    principal_minor=10_000_00,
                    interest_minor=1_000_00,
                    total_minor=11_000_00,
                    status=EMIStatus.OVERDUE if days_ago >= 0 else EMIStatus.PENDING,
                ),
            )

        dpd = service.calculate_dpd(_TENANT, loan_account_id, as_of=_TODAY)

        assert dpd == 30

    def test_dpd_is_zero_with_no_unpaid_instalments(self) -> None:
        repo = _FakeEMIScheduleRepository()
        service = EMIScheduleService(repo)  # type: ignore[arg-type]

        assert service.calculate_dpd(_TENANT, "loan-none", as_of=_TODAY) == 0

    def test_next_unpaid_emi_returns_earliest_due(self) -> None:
        repo = _FakeEMIScheduleRepository()
        service = EMIScheduleService(repo)  # type: ignore[arg-type]
        repo.create_entry(
            _TENANT,
            "loan-1",
            EMIEntry(
                instalment_number=2,
                due_date=date(2026, 8, 1),
                principal_minor=10_000_00,
                interest_minor=1_000_00,
                total_minor=11_000_00,
                status=EMIStatus.PENDING,
            ),
        )
        repo.create_entry(
            _TENANT,
            "loan-1",
            EMIEntry(
                instalment_number=1,
                due_date=date(2026, 6, 1),
                principal_minor=10_000_00,
                interest_minor=1_000_00,
                total_minor=11_000_00,
                status=EMIStatus.OVERDUE,
            ),
        )

        next_emi = service.next_unpaid_emi(_TENANT, "loan-1")

        assert next_emi is not None
        assert next_emi.instalment_number == 1

    def test_post_payment_marks_paid_when_full_amount(self) -> None:
        repo = _FakeEMIScheduleRepository()
        service = EMIScheduleService(repo)  # type: ignore[arg-type]

        status = service.post_payment(_TENANT, "loan-1", 1, paid_minor=11_000_00, instalment_total_minor=11_000_00)

        assert status == EMIStatus.PAID

    def test_post_payment_marks_partially_paid_when_short(self) -> None:
        repo = _FakeEMIScheduleRepository()
        service = EMIScheduleService(repo)  # type: ignore[arg-type]

        status = service.post_payment(_TENANT, "loan-1", 1, paid_minor=5_000_00, instalment_total_minor=11_000_00)

        assert status == EMIStatus.PARTIALLY_PAID


class TestLoanAccountService:
    def test_calculate_dpd_delegates_to_emi_schedule_service(self) -> None:
        """LoanAccountService.calculate_dpd has no ``as_of`` param -- it always uses the real
        wall-clock date (V5 Ch4.3 RI-5: real-time DPD), so the fixture must be anchored to
        ``date.today()`` rather than the module's fixed ``_TODAY``."""
        loan_repo = _FakeLoanAccountRepository()
        emi_repo = _FakeEMIScheduleRepository()
        emi_repo.create_entry(
            _TENANT,
            "loan-1",
            EMIEntry(
                instalment_number=1,
                due_date=date.today() - timedelta(days=15),
                principal_minor=10_000_00,
                interest_minor=1_000_00,
                total_minor=11_000_00,
                status=EMIStatus.OVERDUE,
            ),
        )
        service = LoanAccountService(loan_repo, EMIScheduleService(emi_repo))  # type: ignore[arg-type]

        assert service.calculate_dpd(_TENANT, "loan-1") == 15

    def test_find_by_customer_returns_only_matching_loans(self) -> None:
        loan_repo = _FakeLoanAccountRepository()
        loan_repo.create(_make_loan("loan-1"))
        service = LoanAccountService(loan_repo, EMIScheduleService(_FakeEMIScheduleRepository()))  # type: ignore[arg-type]

        loans = service.find_by_customer(_TENANT, CustomerId("cust-1"))

        assert len(loans) == 1
        assert loans[0].loan_account_id == "loan-1"


class TestSettlementService:
    def test_offer_accept_authorize_transitions(self) -> None:
        """Sprint-022 AC: settlement offer -> accept -> authorize transitions correctly."""
        repo = _FakeSettlementRepository()
        service = SettlementService(repo)  # type: ignore[arg-type]

        offer = service.offer(
            _TENANT,
            CustomerId("cust-1"),
            "loan-1",
            settlement_amount_minor=100_000_00,
            waiver_amount_minor=20_000_00,
            currency="INR",
            offer_expiry=_NOW + timedelta(days=7),
        )
        assert offer.status == SettlementStatus.PROPOSED

        accepted = service.accept(_TENANT, offer.settlement_id)
        assert accepted.status == SettlementStatus.ACCEPTED

        authorized = service.authorize(_TENANT, offer.settlement_id, approved_by="supervisor-1")
        assert authorized.status == SettlementStatus.ACCEPTED  # authorize is a metadata gate, not a new status
        assert repo.get_approved_by(_TENANT, offer.settlement_id) == "supervisor-1"

    def test_disburse_requires_authorization_above_threshold(self) -> None:
        repo = _FakeSettlementRepository()
        service = SettlementService(repo, approval_threshold_minor=50_000_00)  # type: ignore[arg-type]
        offer = service.offer(_TENANT, CustomerId("cust-1"), "loan-1", 100_000_00, 0, "INR", _NOW + timedelta(days=7))
        service.accept(_TENANT, offer.settlement_id)

        with pytest.raises(SettlementTransitionError):
            service.disburse(_TENANT, offer.settlement_id)

        disbursed = service.disburse(_TENANT, offer.settlement_id, approved_by="supervisor-1")
        assert disbursed.status == SettlementStatus.PAID

    def test_disburse_below_threshold_needs_no_authorization(self) -> None:
        repo = _FakeSettlementRepository()
        service = SettlementService(repo, approval_threshold_minor=50_000_00)  # type: ignore[arg-type]
        offer = service.offer(_TENANT, CustomerId("cust-1"), "loan-1", 10_000_00, 0, "INR", _NOW + timedelta(days=7))
        service.accept(_TENANT, offer.settlement_id)

        disbursed = service.disburse(_TENANT, offer.settlement_id)

        assert disbursed.status == SettlementStatus.PAID

    def test_cannot_accept_a_non_proposed_settlement(self) -> None:
        repo = _FakeSettlementRepository()
        service = SettlementService(repo)  # type: ignore[arg-type]
        offer = service.offer(_TENANT, CustomerId("cust-1"), "loan-1", 10_000_00, 0, "INR", _NOW + timedelta(days=7))
        service.accept(_TENANT, offer.settlement_id)

        with pytest.raises(SettlementTransitionError):
            service.accept(_TENANT, offer.settlement_id)


class TestCallbackScheduler:
    def test_schedule_and_find_pending(self) -> None:
        scheduler = CallbackScheduler(_FakeCallbackRepository())  # type: ignore[arg-type]
        customer_id = CustomerId("cust-1")

        callback = scheduler.schedule(
            _TENANT, CallId("call-1"), customer_id, "loan-1", _NOW + timedelta(days=1), "+919876543210"
        )

        pending = scheduler.find_pending(_TENANT, customer_id)
        assert len(pending) == 1
        assert pending[0].callback_id == callback.callback_id

    def test_mark_fulfilled_removes_from_pending(self) -> None:
        repo = _FakeCallbackRepository()
        scheduler = CallbackScheduler(repo)  # type: ignore[arg-type]
        customer_id = CustomerId("cust-1")
        callback = scheduler.schedule(_TENANT, CallId("call-1"), customer_id, "loan-1", _NOW, "+919876543210")

        scheduler.mark_fulfilled(_TENANT, callback.callback_id)

        assert scheduler.find_pending(_TENANT, customer_id) == ()


class TestEscalationWorkflow:
    def test_escalate_records_and_defaults_target(self) -> None:
        workflow = EscalationWorkflow(_FakeEscalationRepository())  # type: ignore[arg-type]

        escalation = workflow.escalate(_TENANT, CallId("call-1"), CustomerId("cust-1"), "DISPUTE")

        assert escalation.escalated_to == EscalationTarget.SUPERVISOR.value

    def test_escalate_legal_threat_routes_to_legal(self) -> None:
        workflow = EscalationWorkflow(_FakeEscalationRepository())  # type: ignore[arg-type]

        escalation = workflow.escalate(_TENANT, CallId("call-1"), CustomerId("cust-1"), "LEGAL_THREAT")

        assert escalation.escalated_to == EscalationTarget.LEGAL.value

    def test_resolve_sets_resolution_notes(self) -> None:
        repo = _FakeEscalationRepository()
        workflow = EscalationWorkflow(repo)  # type: ignore[arg-type]
        escalation = workflow.escalate(_TENANT, CallId("call-1"), CustomerId("cust-1"), "CUSTOMER_REQUEST")

        workflow.resolve(_TENANT, escalation.escalation_id, "Resolved after callback")

        resolved = workflow.find_by_call(_TENANT, CallId("call-1"))[0]
        assert resolved.resolution_notes == "Resolved after callback"
        assert resolved.resolved_at is not None
