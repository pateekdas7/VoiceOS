"""Tests for RI-5: Law of Authority — every fact presented to the customer
must originate from an authoritative source. LLM-generated facts are never
authoritative.

Architecture: V1 Appendix E RI-5; Law of Authority; V6 AR-3.
"""

from __future__ import annotations

import pytest

from src.libs.invariants import InvariantViolationError, assert_ri5_law_of_authority

AUTHORIZED = {"crm", "collections_system", "loan_management"}


class TestRI5LawOfAuthorityPass:
    """Cases where RI-5 is satisfied."""

    def test_crm_is_authorized(self) -> None:
        assert_ri5_law_of_authority(
            fact_key="outstanding_balance",
            value=500000,
            authorized_sources=AUTHORIZED,
            source="crm",
        )

    def test_collections_system_is_authorized(self) -> None:
        assert_ri5_law_of_authority(
            fact_key="dpd",
            value=45,
            authorized_sources=AUTHORIZED,
            source="collections_system",
        )

    def test_loan_management_is_authorized(self) -> None:
        assert_ri5_law_of_authority(
            fact_key="emi_amount",
            value=25000,
            authorized_sources=AUTHORIZED,
            source="loan_management",
        )

    def test_single_authorized_source(self) -> None:
        assert_ri5_law_of_authority(
            fact_key="customer_name",
            value="Ramesh Kumar",
            authorized_sources={"kyc_service"},
            source="kyc_service",
        )


class TestRI5LawOfAuthorityFail:
    """Cases where RI-5 is violated (LLM/unauthorized source)."""

    def test_llm_source_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri5_law_of_authority(
                fact_key="outstanding_balance",
                value=600000,
                authorized_sources=AUTHORIZED,
                source="llm",
            )
        err = exc_info.value
        assert err.invariant_id == "RI-5"

    def test_unknown_source_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri5_law_of_authority(
                fact_key="dpd",
                value=90,
                authorized_sources=AUTHORIZED,
                source="model_hallucination",
            )
        assert exc_info.value.invariant_id == "RI-5"

    def test_error_message_contains_fact_key(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri5_law_of_authority(
                fact_key="account_number",
                value="ACCT001",
                authorized_sources=AUTHORIZED,
                source="llm",
            )
        assert "account_number" in exc_info.value.message

    def test_error_message_contains_unauthorized_source(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri5_law_of_authority(
                fact_key="balance",
                value=100,
                authorized_sources=AUTHORIZED,
                source="user_input",
            )
        assert "user_input" in exc_info.value.message

    def test_error_context_fields(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri5_law_of_authority(
                fact_key="promised_date",
                value="2026-07-01",
                authorized_sources=AUTHORIZED,
                source="llm",
            )
        ctx = exc_info.value.context
        assert ctx["fact_key"] == "promised_date"
        assert ctx["source"] == "llm"
        assert "authorized_sources" in ctx

    def test_empty_authorized_sources_raises(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri5_law_of_authority(
                fact_key="balance",
                value=100,
                authorized_sources=set(),
                source="crm",
            )

    def test_is_subclass_of_exception(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri5_law_of_authority("k", "v", set(), "src")
