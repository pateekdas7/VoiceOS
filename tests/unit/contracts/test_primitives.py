"""Unit tests for src/libs/contracts/primitives.py.

Tests: Money arithmetic, currency mismatch, TenantId / CallId NewTypes,
       immutability (frozen Pydantic model), JSON serialisation round-trip.

AC-1: primitives module imported and types pass mypy --strict.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.libs.contracts.primitives import (
    AccountId,
    CallId,
    CampaignId,
    Currency,
    CustomerId,
    EntityId,
    Money,
    PhoneNumber,
    TenantId,
    Timestamp,
)

# ---------------------------------------------------------------------------
# Money creation
# ---------------------------------------------------------------------------


class TestMoneyCreation:
    def test_create_valid_inr(self) -> None:
        m = Money(amount_minor=50000, currency=Currency.INR)
        assert m.amount_minor == 50000
        assert m.currency == Currency.INR

    def test_create_zero(self) -> None:
        m = Money(amount_minor=0, currency=Currency.INR)
        assert m.amount_minor == 0

    def test_create_usd(self) -> None:
        m = Money(amount_minor=100, currency=Currency.USD)
        assert m.currency == Currency.USD

    def test_negative_amount_raises(self) -> None:
        with pytest.raises(ValueError, match="amount_minor must be >= 0"):
            Money(amount_minor=-1, currency=Currency.INR)


# ---------------------------------------------------------------------------
# Money arithmetic
# ---------------------------------------------------------------------------


class TestMoneyArithmetic:
    def test_add_same_currency(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=200, currency=Currency.INR)
        result = a + b
        assert result.amount_minor == 300
        assert result.currency == Currency.INR

    def test_add_currency_mismatch_raises(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.USD)
        with pytest.raises(ValueError, match="currency mismatch"):
            _ = a + b

    def test_subtract_same_currency(self) -> None:
        a = Money(amount_minor=500, currency=Currency.INR)
        b = Money(amount_minor=200, currency=Currency.INR)
        result = a - b
        assert result.amount_minor == 300

    def test_subtract_to_zero(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.INR)
        result = a - b
        assert result.amount_minor == 0

    def test_subtract_negative_raises(self) -> None:
        a = Money(amount_minor=50, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.INR)
        with pytest.raises(ValueError, match="negative Money"):
            _ = a - b

    def test_subtract_currency_mismatch_raises(self) -> None:
        a = Money(amount_minor=500, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.USD)
        with pytest.raises(ValueError, match="currency mismatch"):
            _ = a - b


# ---------------------------------------------------------------------------
# Money comparison
# ---------------------------------------------------------------------------


class TestMoneyComparison:
    def test_equality(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.INR)
        assert a == b

    def test_inequality_amount(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=200, currency=Currency.INR)
        assert a != b

    def test_inequality_currency(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.USD)
        assert a != b

    def test_less_than(self) -> None:
        a = Money(amount_minor=50, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.INR)
        assert a < b
        assert not b < a

    def test_greater_than(self) -> None:
        a = Money(amount_minor=200, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.INR)
        assert a > b

    def test_less_than_or_equal(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.INR)
        assert a <= b

    def test_greater_than_or_equal(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.INR)
        assert a >= b

    def test_compare_different_currencies_raises(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.USD)
        with pytest.raises(ValueError, match="Cannot compare"):
            _ = a < b


# ---------------------------------------------------------------------------
# Money immutability (frozen Pydantic model)
# ---------------------------------------------------------------------------


class TestMoneyImmutability:
    def test_mutation_raises(self) -> None:
        m = Money(amount_minor=100, currency=Currency.INR)
        with pytest.raises(ValidationError):
            m.amount_minor = 200  # type: ignore[misc]

    def test_hash_stable(self) -> None:
        a = Money(amount_minor=100, currency=Currency.INR)
        b = Money(amount_minor=100, currency=Currency.INR)
        assert hash(a) == hash(b)
        s: set[Money] = {a, b}
        assert len(s) == 1


# ---------------------------------------------------------------------------
# Money serialisation
# ---------------------------------------------------------------------------


class TestMoneySerialization:
    def test_json_round_trip(self) -> None:
        m = Money(amount_minor=99900, currency=Currency.INR)
        j = m.model_dump_json()
        m2 = Money.model_validate_json(j)
        assert m == m2

    def test_repr(self) -> None:
        m = Money(amount_minor=12345, currency=Currency.INR)
        r = repr(m)
        assert "123.45" in r
        assert "INR" in r


# ---------------------------------------------------------------------------
# Typed identifier smoke tests (NewType)
# ---------------------------------------------------------------------------


class TestTypedIdentifiers:
    def test_tenant_id_is_str_subtype(self) -> None:
        tid = TenantId("tenant-abc")
        assert isinstance(tid, str)
        assert tid == "tenant-abc"

    def test_call_id_is_str_subtype(self) -> None:
        cid = CallId("call-xyz-123")
        assert isinstance(cid, str)

    def test_customer_id(self) -> None:
        cust = CustomerId("cust-001")
        assert isinstance(cust, str)

    def test_account_id(self) -> None:
        acct = AccountId("acct-999")
        assert isinstance(acct, str)

    def test_campaign_id(self) -> None:
        camp = CampaignId("camp-2024-q1")
        assert isinstance(camp, str)

    def test_entity_id(self) -> None:
        eid = EntityId("550e8400-e29b-41d4-a716-446655440000")
        assert isinstance(eid, str)

    def test_phone_number(self) -> None:
        phone = PhoneNumber("+919876543210")
        assert phone.startswith("+91")

    def test_currency_values(self) -> None:
        assert Currency.INR.value == "INR"
        assert Currency.USD.value == "USD"

    def test_timestamp_alias(self) -> None:
        from datetime import datetime

        now = datetime.utcnow()
        ts = Timestamp(now)
        assert ts == now
