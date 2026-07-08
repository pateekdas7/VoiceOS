"""Unit tests for src/libs/contracts/context.py.

Tests: CustomerContext creation, field access, DPD helper properties,
       ConsentStatus enum, outstanding balance access, immutability.

AC-1: context module types pass mypy --strict.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from src.libs.contracts.context import (
    ConsentStatus,
    ContactInfo,
    CustomerContext,
    LoanSummary,
    OutstandingBalance,
    PartyInfo,
)
from src.libs.contracts.primitives import (
    AccountId,
    Currency,
    CustomerId,
    Money,
    PhoneNumber,
    TenantId,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_contact() -> ContactInfo:
    return ContactInfo(
        phone_number=PhoneNumber("+919876543210"),
        preferred_language="hi-IN",
    )


def make_party(party_id: str = "cust-001") -> PartyInfo:
    return PartyInfo(
        party_id=CustomerId(party_id),
        role="BORROWER",
        name="Ramesh Kumar",
        contact=make_contact(),
    )


def make_loan(account_id: str = "acct-001", dpd: int = 45) -> LoanSummary:
    return LoanSummary(
        account_id=AccountId(account_id),
        product_type="PERSONAL_LOAN",
        outstanding_balance=Money(amount_minor=500000, currency=Currency.INR),
        dpd=dpd,
    )


def make_context(**overrides: object) -> CustomerContext:
    defaults: dict[str, object] = {
        "customer_id": CustomerId("cust-001"),
        "tenant_id": TenantId("tenant-xyz"),
        "primary_party": make_party(),
        "consent_status": ConsentStatus.GRANTED,
        "call_id": "call-abc",
    }
    defaults.update(overrides)
    return CustomerContext(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CustomerContext creation
# ---------------------------------------------------------------------------


class TestCustomerContextCreation:
    def test_minimal_context(self) -> None:
        ctx = make_context()
        assert ctx.customer_id == "cust-001"
        assert ctx.tenant_id == "tenant-xyz"
        assert ctx.consent_status == ConsentStatus.GRANTED

    def test_defaults(self) -> None:
        ctx = make_context()
        assert ctx.loans == ()
        assert ctx.additional_parties == ()
        assert ctx.outstanding is None
        assert ctx.context_version == 1

    def test_with_loans(self) -> None:
        loans = (make_loan("acct-001", dpd=30), make_loan("acct-002", dpd=60))
        ctx = make_context(loans=loans)
        assert len(ctx.loans) == 2

    def test_with_outstanding_balance(self) -> None:
        outstanding = OutstandingBalance(
            total_outstanding=Money(amount_minor=1000000, currency=Currency.INR),
            total_overdue=Money(amount_minor=500000, currency=Currency.INR),
            account_count=2,
        )
        ctx = make_context(outstanding=outstanding)
        assert ctx.outstanding is not None
        assert ctx.outstanding.total_outstanding.amount_minor == 1000000

    def test_with_additional_parties(self) -> None:
        parties = (make_party("cust-002"),)
        ctx = make_context(additional_parties=parties)
        assert len(ctx.additional_parties) == 1


# ---------------------------------------------------------------------------
# DPD helper properties
# ---------------------------------------------------------------------------


class TestDpdProperties:
    def test_max_dpd_no_loans(self) -> None:
        ctx = make_context()
        assert ctx.max_dpd == 0

    def test_max_dpd_single_loan(self) -> None:
        ctx = make_context(loans=(make_loan(dpd=45),))
        assert ctx.max_dpd == 45

    def test_max_dpd_multiple_loans(self) -> None:
        loans = (make_loan("a", dpd=30), make_loan("b", dpd=90), make_loan("c", dpd=15))
        ctx = make_context(loans=loans)
        assert ctx.max_dpd == 90

    def test_primary_loan_no_loans(self) -> None:
        ctx = make_context()
        assert ctx.primary_loan is None

    def test_primary_loan_returns_highest_dpd(self) -> None:
        loans = (make_loan("a", dpd=30), make_loan("b", dpd=90), make_loan("c", dpd=15))
        ctx = make_context(loans=loans)
        assert ctx.primary_loan is not None
        assert ctx.primary_loan.dpd == 90
        assert ctx.primary_loan.account_id == "b"


# ---------------------------------------------------------------------------
# ConsentStatus enum
# ---------------------------------------------------------------------------


class TestConsentStatus:
    def test_all_statuses(self) -> None:
        statuses = {s.value for s in ConsentStatus}
        assert "GRANTED" in statuses
        assert "REVOKED" in statuses
        assert "PENDING" in statuses
        assert "EXPIRED" in statuses

    def test_context_with_revoked_consent(self) -> None:
        ctx = make_context(consent_status=ConsentStatus.REVOKED)
        assert ctx.consent_status == ConsentStatus.REVOKED


# ---------------------------------------------------------------------------
# LoanSummary fields
# ---------------------------------------------------------------------------


class TestLoanSummary:
    def test_loan_dpd_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            LoanSummary(
                account_id=AccountId("acct-001"),
                product_type="PERSONAL_LOAN",
                outstanding_balance=Money(amount_minor=100000, currency=Currency.INR),
                dpd=-1,
            )

    def test_optional_fields_default_none(self) -> None:
        loan = make_loan()
        assert loan.next_emi_date is None
        assert loan.next_emi_amount is None
        assert loan.total_overdue is None

    def test_with_all_optional_fields(self) -> None:
        loan = LoanSummary(
            account_id=AccountId("acct-001"),
            product_type="HOME_LOAN",
            outstanding_balance=Money(amount_minor=5000000, currency=Currency.INR),
            dpd=0,
            next_emi_date=date(2026, 7, 15),
            next_emi_amount=Money(amount_minor=25000, currency=Currency.INR),
            total_overdue=Money(amount_minor=0, currency=Currency.INR),
            loan_start_date=date(2022, 1, 1),
            sanctioned_amount=Money(amount_minor=6000000, currency=Currency.INR),
        )
        assert loan.next_emi_date == date(2026, 7, 15)
        assert loan.dpd == 0


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestCustomerContextImmutability:
    def test_mutation_raises(self) -> None:
        ctx = make_context()
        with pytest.raises(ValidationError):
            ctx.customer_id = CustomerId("tampered")  # type: ignore[misc]

    def test_tenant_id_mutation_raises(self) -> None:
        ctx = make_context()
        with pytest.raises(ValidationError):
            ctx.tenant_id = TenantId("other-tenant")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ContactInfo
# ---------------------------------------------------------------------------


class TestContactInfo:
    def test_contact_creation(self) -> None:
        c = make_contact()
        assert c.phone_number == "+919876543210"
        assert c.preferred_language == "hi-IN"

    def test_contact_with_alternate(self) -> None:
        c = ContactInfo(
            phone_number=PhoneNumber("+919876543210"),
            alternate_phone=PhoneNumber("+919123456789"),
        )
        assert c.alternate_phone == "+919123456789"

    def test_contact_immutable(self) -> None:
        c = make_contact()
        with pytest.raises(ValidationError):
            c.phone_number = PhoneNumber("+0000000000")  # type: ignore[misc]
