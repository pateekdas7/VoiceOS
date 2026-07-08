"""Unit tests for LoanAccountRepository (V5 Ch4)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.libs.contracts.models.loan import LoanAccount, LoanOutstanding, LoanStatus
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.repositories.loan_account import LoanAccountRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor

_NOW = datetime(2026, 7, 4, tzinfo=UTC)

_LOAN_ROW = (
    "loan-1",
    "tenant-a",
    "cust-1",
    "PERSONAL_LOAN",
    100_000_00,
    "INR",
    1200,
    12,
    date(2026, 1, 1),
    date(2027, 1, 1),
    "ACTIVE",
    5,
    90_000_00,
    5_000_00,
    0,
    95_000_00,
    _NOW,
    _NOW,
    _NOW,
)


def _loan() -> LoanAccount:
    return LoanAccount(
        loan_account_id="loan-1",
        tenant_id=TenantId("tenant-a"),
        customer_id=CustomerId("cust-1"),
        product_type="PERSONAL_LOAN",
        disbursed_amount_minor=100_000_00,
        currency="INR",
        interest_rate_bps=1200,
        tenure_months=12,
        disbursement_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
        status=LoanStatus.ACTIVE,
        dpd=5,
        outstanding=LoanOutstanding(
            loan_account_id="loan-1",
            principal_minor=90_000_00,
            interest_minor=5_000_00,
            penalty_minor=0,
            total_minor=95_000_00,
            currency="INR",
            as_of=_NOW,
        ),
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestCreate:
    def test_inserts_and_commits(self) -> None:
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        repo = LoanAccountRepository(conn)

        repo.create(_loan())

        assert "INSERT INTO loan_accounts" in cursor.executed[0][0]
        assert conn.commit_count == 1


class TestGet:
    def test_returns_none_when_missing(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = LoanAccountRepository(FakeConnection(cursor))

        assert repo.get(TenantId("tenant-a"), "missing") is None

    def test_hydrates_loan_with_outstanding(self) -> None:
        cursor = FakeCursor(fetchall_results=[[_LOAN_ROW]])
        repo = LoanAccountRepository(FakeConnection(cursor))

        loan = repo.get(TenantId("tenant-a"), "loan-1")

        assert loan is not None
        assert loan.status == LoanStatus.ACTIVE
        assert loan.outstanding is not None
        assert loan.outstanding.total_minor == 95_000_00

    def test_tenant_scoped(self) -> None:
        cursor = FakeCursor(fetchall_results=[[]])
        repo = LoanAccountRepository(FakeConnection(cursor))

        repo.get(TenantId("tenant-a"), "loan-1")

        sql, params = cursor.executed[0]
        assert "tenant_id = %s" in sql
        assert params[0] == "tenant-a"


class TestFindByCustomer:
    def test_returns_all_matching_loans(self) -> None:
        cursor = FakeCursor(fetchall_results=[[_LOAN_ROW, _LOAN_ROW]])
        repo = LoanAccountRepository(FakeConnection(cursor))

        loans = repo.find_by_customer(TenantId("tenant-a"), CustomerId("cust-1"))

        assert len(loans) == 2


class TestOutstandingBalance:
    def test_returns_none_when_never_computed(self) -> None:
        cursor = FakeCursor(fetchall_results=[[(None, None, None, None, None, "INR")]])
        repo = LoanAccountRepository(FakeConnection(cursor))

        assert repo.outstanding_balance(TenantId("tenant-a"), "loan-1") is None

    def test_returns_outstanding_snapshot(self) -> None:
        cursor = FakeCursor(fetchall_results=[[(90_000_00, 5_000_00, 0, 95_000_00, _NOW, "INR")]])
        repo = LoanAccountRepository(FakeConnection(cursor))

        outstanding = repo.outstanding_balance(TenantId("tenant-a"), "loan-1")

        assert outstanding is not None
        assert outstanding.total_minor == 95_000_00
