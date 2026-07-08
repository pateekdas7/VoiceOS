"""Primitive value types shared across all VoiceOS v2 domains.

Provides strongly-typed identifiers (NewType wrappers), Money with correct
decimal arithmetic, and the Currency enumeration. These types are the
vocabulary building blocks imported by every other contract module.

Architecture: V1 Appendix A, V6 Ch3 (CS-1, CS-4), DocSuite-03.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import NewType

from pydantic import BaseModel, ConfigDict, model_validator

# ---------------------------------------------------------------------------
# Typed identifiers (NewType wrappers — prevent mixing up plain str values)
# ---------------------------------------------------------------------------

TenantId = NewType("TenantId", str)
"""Globally unique tenant identifier. Scopes every resource and data access (AR-8)."""

CallId = NewType("CallId", str)
"""Unique call session identifier. Present on every turn, event, and log line."""

CustomerId = NewType("CustomerId", str)
"""Authoritative customer/party identifier from the CRM (V5 Ch4)."""

AccountId = NewType("AccountId", str)
"""Loan account identifier from the collections system (V5 Ch5)."""

CampaignId = NewType("CampaignId", str)
"""Campaign identifier from the campaign management service (V5 Ch6)."""

EntityId = NewType("EntityId", str)
"""Generic domain entity identifier (UUID / ULID)."""

PhoneNumber = NewType("PhoneNumber", str)
"""E.164 phone number string (e.g. '+919876543210')."""

Timestamp = NewType("Timestamp", datetime)
"""UTC datetime. Use datetime directly where possible; this alias adds semantic clarity."""


# ---------------------------------------------------------------------------
# Currency & Money
# ---------------------------------------------------------------------------


class Currency(StrEnum):
    """ISO 4217 currency codes supported by VoiceOS v2.

    Money is always stored as minor units (paise, cents) + currency — never
    as a float. This enumeration constrains the supported currencies so that
    amount comparisons are always within the same denomination.
    """

    INR = "INR"
    USD = "USD"


class Money(BaseModel):
    """Immutable monetary value: minor units + currency.

    ``amount_minor`` is expressed in the smallest denomination of the currency
    (e.g., paise for INR, cents for USD) to avoid floating-point errors in
    financial arithmetic.

    Architecture: V5 Ch4 (collections), DocSuite-03 (data dictionary).
    V6 DM-2 mandates (amount_minor BIGINT, currency CHAR(3)) in storage.
    """

    model_config = ConfigDict(frozen=True)

    amount_minor: int
    """Amount in the smallest denomination (e.g., 100 paise = ₹1.00)."""

    currency: Currency
    """ISO 4217 currency code."""

    @model_validator(mode="after")
    def _validate_non_negative(self) -> Money:
        """Money amounts must be non-negative in authoritative contexts.

        Negative values may appear in adjustment or reversal records — those
        callers must construct them explicitly. This validator ensures that
        Money created in normal flows is always ≥ 0.
        """
        if self.amount_minor < 0:
            raise ValueError(f"amount_minor must be >= 0, got {self.amount_minor}")
        return self

    def __add__(self, other: Money) -> Money:
        """Add two Money values of the same currency."""
        if self.currency != other.currency:
            raise ValueError(f"Cannot add {self.currency} and {other.currency}: currency mismatch")
        return Money(amount_minor=self.amount_minor + other.amount_minor, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        """Subtract two Money values of the same currency.

        Raises ValueError if the result would be negative (use the raw
        constructor directly when signed Money is intentional).
        """
        if self.currency != other.currency:
            raise ValueError(f"Cannot subtract {other.currency} from {self.currency}: currency mismatch")
        result = self.amount_minor - other.amount_minor
        if result < 0:
            raise ValueError(
                f"Subtraction would produce negative Money: {self.amount_minor} - {other.amount_minor} = {result}"
            )
        return Money(amount_minor=result, currency=self.currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.amount_minor == other.amount_minor and self.currency == other.currency

    def __lt__(self, other: Money) -> bool:
        if self.currency != other.currency:
            raise ValueError(f"Cannot compare {self.currency} and {other.currency}")
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        return self == other or self < other

    def __gt__(self, other: Money) -> bool:
        return not self <= other

    def __ge__(self, other: Money) -> bool:
        return not self < other

    def __repr__(self) -> str:
        major = self.amount_minor // 100
        minor = self.amount_minor % 100
        return f"Money({major}.{minor:02d} {self.currency.value})"

    def __hash__(self) -> int:
        return hash((self.amount_minor, self.currency))
