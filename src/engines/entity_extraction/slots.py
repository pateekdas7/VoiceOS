"""Entity slot types for the Entity Extraction Engine.

Defines the canonical set of entity types extracted from customer
utterances by the EntityExtractor. These types cover the collections
and payments domain for Hindi and English (Hinglish) conversations.

Architecture: V2 Ch9; DocSuite-03 (Data Dictionary — entity types).
"""

from __future__ import annotations

from enum import StrEnum


class EntityType(StrEnum):
    """Named entity types extracted by the EntityExtractor.

    Each type maps to a slot that the StrategyEngine (Sprint-011) uses
    when planning negotiation actions (e.g., confirming PROMISE_DATE).

    Architecture: V2 Ch9; DocSuite-03.
    """

    AMOUNT = "AMOUNT"
    """Monetary amount, e.g., ₹5,000 → '5000' (minor-unit string)."""

    DATE = "DATE"
    """Absolute calendar date in ISO 8601 format (YYYY-MM-DD)."""

    PROMISE_DATE = "PROMISE_DATE"
    """Relative or absolute promise-to-pay date, resolved to YYYY-MM-DD."""

    ACCOUNT_NUMBER = "ACCOUNT_NUMBER"
    """Loan/account number referenced by the customer."""

    PHONE = "PHONE"
    """Phone number mentioned in the utterance."""

    NAME = "NAME"
    """Customer or third-party name mentioned."""

    UPI_ID = "UPI_ID"
    """UPI payment identifier (e.g., 'name@bank')."""

    LOAN_ID = "LOAN_ID"
    """Loan reference identifier."""

    PARTIAL_AMOUNT = "PARTIAL_AMOUNT"
    """Partial payment amount offer, e.g., 'ek hazaar de sakta hun' → '1000'."""
