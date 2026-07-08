"""CustomerContext and associated types.

CustomerContext is the authoritative, immutable snapshot of customer and loan
data assembled from the CRM and collections system of record at the start of
a call. It is the *only* authoritative data source for the intelligence
engines. Any fact the LLM presents to the customer must originate here (RI-5,
Law of Authority).

CustomerContext is assembled in Sprint-022 (CRM service) but defined here so
all downstream components (PromptBuilder, IntentEngine, OutputValidator) can
import it without depending on the CRM service.

Architecture: V1 Ch11, V1 Appendix A; V2 Ch10; V5 Ch4, Ch5; DocSuite-02 A.4;
              DocSuite-03 (Data Dictionary).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .primitives import AccountId, CustomerId, Money, PhoneNumber, TenantId


class ConsentStatus(StrEnum):
    """Customer consent state for VoiceOS contact and data processing.

    Governs whether a call may proceed (DPDP / RBI compliance, Sprint-017).
    The Policy Engine checks consent before the call is allowed to continue.

    Architecture: V4 Ch2 (DPDP), V5 Ch4.
    """

    GRANTED = "GRANTED"
    """Customer has given explicit consent to be contacted."""

    REVOKED = "REVOKED"
    """Customer has revoked consent — call must not proceed past this point."""

    PENDING = "PENDING"
    """Consent has not yet been confirmed for this session."""

    EXPIRED = "EXPIRED"
    """Previously granted consent has lapsed per retention policy."""


class ContactInfo(BaseModel):
    """Contact details for a party in the loan relationship.

    Sourced from the CRM (V5 Ch4). PII fields are subject to encryption
    at rest (V4 Ch8) and redaction in logs (CS-10).

    Architecture: V5 Ch4; DocSuite-03.
    """

    model_config = ConfigDict(frozen=True)

    phone_number: PhoneNumber
    """Primary E.164 phone number for this party."""

    alternate_phone: PhoneNumber | None = None
    """Alternate contact number (optional)."""

    preferred_language: str = "hi-IN"
    """BCP-47 language tag for communication preference."""

    preferred_call_times: tuple[str, ...] = ()
    """Preferred contact windows (e.g., ['morning', 'evening'])."""


class LoanSummary(BaseModel):
    """Summary of a single loan account.

    Assembled from the loan accounts table (V5 Ch5). All monetary values
    are in minor currency units (paise for INR). The DPD (Days Past Due)
    calculation is always real-time from the EMI schedule — never cached.

    Architecture: V5 Ch5; DocSuite-03.
    """

    model_config = ConfigDict(frozen=True)

    account_id: AccountId
    """Loan account identifier."""

    product_type: str
    """Loan product type (e.g., 'PERSONAL_LOAN', 'HOME_LOAN', 'CREDIT_CARD')."""

    outstanding_balance: Money
    """Total outstanding amount owed (principal + interest as of today)."""

    dpd: int = Field(ge=0)
    """Days Past Due — number of days since the earliest overdue EMI. Real-time."""

    next_emi_date: date | None = None
    """Next scheduled EMI due date."""

    next_emi_amount: Money | None = None
    """Amount of the next scheduled EMI."""

    total_overdue: Money | None = None
    """Total overdue amount across all missed EMIs."""

    loan_start_date: date | None = None
    """Origination date of the loan."""

    sanctioned_amount: Money | None = None
    """Original sanctioned amount at origination."""


class PartyInfo(BaseModel):
    """Information about a party in the loan relationship (borrower / guarantor).

    A loan may have multiple parties; the primary borrower is always first.
    PII fields (name, phone) are encrypted at rest and redacted in logs.

    Architecture: V5 Ch4; DocSuite-03.
    """

    model_config = ConfigDict(frozen=True)

    party_id: CustomerId
    """Authoritative party identifier from the CRM."""

    role: str
    """Party role: 'BORROWER', 'CO_BORROWER', 'GUARANTOR'."""

    name: str
    """Full legal name (PII — encrypted at rest, redacted in logs per CS-10)."""

    contact: ContactInfo
    """Contact details for this party."""

    identity_verified: bool = False
    """Whether identity was verified at the start of this call session."""


class OutstandingBalance(BaseModel):
    """Aggregated outstanding balance across all accounts for a customer.

    Used by the NegotiationEngine to compute floor/ceiling amounts without
    querying each loan separately during a call.

    Architecture: V5 Ch5; DocSuite-03.
    """

    model_config = ConfigDict(frozen=True)

    total_outstanding: Money
    """Sum of outstanding balances across all active loan accounts."""

    total_overdue: Money
    """Sum of overdue amounts across all accounts with DPD > 0."""

    account_count: int = Field(ge=0)
    """Number of active loan accounts included in this summary."""


class CustomerContext(BaseModel):
    """Authoritative, immutable customer snapshot for a single call session.

    Assembled by the CRM service at call start from the CRM and collections
    system of record. Once assembled, this object is immutable for the
    duration of the call — derived state must never overwrite it (RI-5).

    The PromptBuilder reads from CustomerContext to inject authoritative
    facts into the LLM prompt. The LLM must never produce a value that
    contradicts CustomerContext.facts — the OutputValidator enforces this.

    Architecture: V1 Ch11, V1 Appendix A; V2 Ch10; V5 Ch4-5;
                  DocSuite-02 A.4; DocSuite-03.
    Invariant: RI-5 (Law of Authority) — every fact presented to the customer
               must originate from this object, never from the LLM.
    """

    model_config = ConfigDict(frozen=True)

    customer_id: CustomerId
    """Authoritative customer identifier from the CRM."""

    tenant_id: TenantId
    """Tenant scope (AR-8). Prevents cross-tenant data access."""

    primary_party: PartyInfo
    """The primary borrower for this call session."""

    additional_parties: tuple[PartyInfo, ...] = ()
    """Co-borrowers and guarantors, if any."""

    loans: tuple[LoanSummary, ...] = ()
    """Summary of all active loan accounts for this customer."""

    outstanding: OutstandingBalance | None = None
    """Aggregated outstanding balance snapshot."""

    consent_status: ConsentStatus = ConsentStatus.PENDING
    """Current consent status. The Policy Engine rejects calls if not GRANTED."""

    call_id: str = ""
    """The call session this context was assembled for."""

    assembled_at: datetime = Field(default_factory=datetime.utcnow)
    """UTC timestamp when this context snapshot was assembled."""

    context_version: int = Field(default=1, ge=1)
    """Schema version for this context snapshot (EV-4 additive evolution)."""

    @property
    def primary_loan(self) -> LoanSummary | None:
        """The highest-DPD loan account for this customer (primary collection target)."""
        if not self.loans:
            return None
        return max(self.loans, key=lambda loan: loan.dpd)

    @property
    def max_dpd(self) -> int:
        """Maximum DPD across all loan accounts. 0 if no loans."""
        if not self.loans:
            return 0
        return max(loan.dpd for loan in self.loans)
