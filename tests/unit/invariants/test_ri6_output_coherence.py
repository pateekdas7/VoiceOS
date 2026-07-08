"""Tests for RI-6: Output coherence — LLM output must match the current
ResponsePlan by plan_id. Mismatched IDs indicate a stale or wrong plan.

Architecture: V1 Appendix E RI-6; V1 Ch14 (Output Validator); V6 AR-6.
"""

from __future__ import annotations

import pytest

from src.libs.invariants import InvariantViolationError, assert_ri6_output_coherence


class TestRI6OutputCoherencePass:
    """Cases where RI-6 is satisfied (plan IDs match)."""

    def test_matching_ids(self) -> None:
        assert_ri6_output_coherence(
            response_plan_id="plan-001",
            llm_output_plan_id="plan-001",
        )

    def test_matching_uuid_ids(self) -> None:
        uid = "550e8400-e29b-41d4-a716-446655440000"
        assert_ri6_output_coherence(
            response_plan_id=uid,
            llm_output_plan_id=uid,
        )

    def test_matching_complex_ids(self) -> None:
        pid = "tenant-abc:call-xyz:plan-v3-20260630"
        assert_ri6_output_coherence(
            response_plan_id=pid,
            llm_output_plan_id=pid,
        )


class TestRI6OutputCoherenceFail:
    """Cases where RI-6 is violated (plan ID mismatch)."""

    def test_different_ids_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri6_output_coherence(
                response_plan_id="plan-001",
                llm_output_plan_id="plan-002",
            )
        err = exc_info.value
        assert err.invariant_id == "RI-6"

    def test_error_message_contains_both_ids(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri6_output_coherence(
                response_plan_id="plan-CURRENT",
                llm_output_plan_id="plan-STALE",
            )
        msg = exc_info.value.message
        assert "plan-CURRENT" in msg
        assert "plan-STALE" in msg

    def test_empty_llm_plan_id_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri6_output_coherence(
                response_plan_id="plan-001",
                llm_output_plan_id="",
            )
        assert exc_info.value.invariant_id == "RI-6"

    def test_error_context_contains_both_ids(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri6_output_coherence(
                response_plan_id="plan-A",
                llm_output_plan_id="plan-B",
            )
        ctx = exc_info.value.context
        assert ctx["response_plan_id"] == "plan-A"
        assert ctx["llm_output_plan_id"] == "plan-B"

    def test_is_subclass_of_exception(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri6_output_coherence("plan-1", "plan-2")
