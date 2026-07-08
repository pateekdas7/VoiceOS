"""Persistent data models for loan accounts, EMI schedules, and DPD tracking.

These models represent the financial state of a borrower's loan. They are
authoritative: values must come from the collections/lending system, never
invented by the LLM (Invariant RI-5 — Law of Authority).

Architecture: V5 Ch4 (Loan & Collections); V2 Ch1 (CustomerContext);
              Invariant RI-5.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..primitives import CustomerId, TenantId


class LoanStatus(StrEnum):
    """Overall status of a loan account."""

    ACTIVE = "ACTIVE"
    DELINQUENT = "DELINQUENT"
    NPA = "NPA"
    """Non-Performing Asset — past the NPA threshold."""
    SETTLED = "SETTLED"
    WRITTEN_OFF = "WRITTEN_OFF"
    CLOSED = "CLOSED"


class EMIStatus(StrEnum):
    """Status of a single EMI instalment."""

    PENDING = "PENDING"
    PAID = "PAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    OVERDUE = "OVERDUE"
    WAIVED = "WAIVED"


class EMIEntry(BaseModel):
    """A single scheduled EMI instalment (V5 Ch4.2)."""

    model_config = ConfigDict(frozen=True)

    instalment_number: int = Field(ge=1)
    due_date: date
    principal_minor: int = Field(ge=0)
    """Principal component in minor currency units."""
    interest_minor: int = Field(ge=0)
    """Interest component in minor currency units."""
    total_minor: int = Field(ge=0)
    """Total EMI amount in minor currency units (principal + interest + fees)."""
    paid_minor: int = Field(default=0, ge=0)
    """Amount actually paid against this instalment."""
    status: EMIStatus = EMIStatus.PENDING


class EMISchedule(BaseModel):
    """Full EMI repayment schedule for a loan account (V5 Ch4.2)."""

    model_config = ConfigDict(frozen=True)

    loan_account_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    """ISO 4217 currency code."""
    instalments: tuple[EMIEntry, ...] = Field(default=())


class DPDRecord(BaseModel):
    """Days-Past-Due snapshot for a loan account at a point in time (V5 Ch4.3).

    DPD values are authoritative credit data (RI-5). The agent must never
    fabricate or adjust DPD values during a call.
    """

    model_config = ConfigDict(frozen=True)

    loan_account_id: str = Field(min_length=1)
    dpd: int = Field(ge=0)
    """Number of days the loan is past due at ``as_of_date``."""
    as_of_date: date
    overdue_minor: int = Field(ge=0)
    """Total overdue amount in minor currency units as of ``as_of_date``."""
    currency: str = Field(min_length=3, max_length=3)


class LoanOutstanding(BaseModel):
    """Current outstanding balance breakdown for a loan account.

    Named LoanOutstanding (not OutstandingBalance) to avoid collision with
    the runtime OutstandingBalance type in context.py.

    Architecture: V2 Ch1 (CustomerContext.outstanding); Invariant RI-5.
    """

    model_config = ConfigDict(frozen=True)

    loan_account_id: str = Field(min_length=1)
    principal_minor: int = Field(ge=0)
    """Outstanding principal in minor currency units."""
    interest_minor: int = Field(ge=0)
    """Accrued interest in minor currency units."""
    penalty_minor: int = Field(default=0, ge=0)
    """Late payment penalty in minor currency units."""
    total_minor: int = Field(ge=0)
    """Total outstanding = principal + interest + penalty."""
    currency: str = Field(min_length=3, max_length=3)
    as_of: datetime
    """Timestamp when this outstanding snapshot was computed."""


class LoanAccount(BaseModel):
    """Authoritative loan account record (V5 Ch4).

    The ``loan_account_id`` is the lending system's stable identifier (RI-5).
    All monetary fields are stored in minor currency units to avoid
    floating-point precision errors.
    """

    model_config = ConfigDict(frozen=True)

    loan_account_id: str = Field(min_length=1)
    tenant_id: TenantId
    customer_id: CustomerId
    product_type: str = Field(min_length=1)
    """Loan product: 'PERSONAL_LOAN' | 'HOME_LOAN' | 'VEHICLE_LOAN' | 'CREDIT_CARD'."""
    disbursed_amount_minor: int = Field(ge=0)
    """Original disbursed amount in minor currency units."""
    currency: str = Field(min_length=3, max_length=3)
    interest_rate_bps: int = Field(ge=0)
    """Annual interest rate in basis points (e.g. 1200 = 12.00%)."""
    tenure_months: int = Field(ge=1)
    disbursement_date: date
    maturity_date: date
    status: LoanStatus = LoanStatus.ACTIVE
    dpd: int = Field(default=0, ge=0)
    """Current days-past-due (denormalised for fast query)."""
    outstanding: LoanOutstanding | None = None
    emi_schedule: EMISchedule | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "DPDRecord",
    "EMIEntry",
    "EMISchedule",
    "EMIStatus",
    "LoanAccount",
    "LoanOutstanding",
    "LoanStatus",
]
