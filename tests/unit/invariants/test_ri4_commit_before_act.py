"""Tests for RI-4: Commit-before-act — state event must be committed before
any externally-visible action is performed.

Architecture: V1 Appendix E RI-4; V6 AR-12; V3 Ch8.
"""

from __future__ import annotations

import pytest

from src.libs.invariants import InvariantViolationError, assert_ri4_commit_before_act


class TestRI4CommitBeforeActPass:
    """Cases where RI-4 is satisfied."""

    def test_event_committed_true(self) -> None:
        assert_ri4_commit_before_act(event_committed=True, action_name="create_ptp")

    def test_committed_before_sms(self) -> None:
        assert_ri4_commit_before_act(event_committed=True, action_name="send_sms")

    def test_committed_before_outbound_call(self) -> None:
        assert_ri4_commit_before_act(event_committed=True, action_name="initiate_call")


class TestRI4CommitBeforeActFail:
    """Cases where RI-4 is violated."""

    def test_uncommitted_event_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri4_commit_before_act(event_committed=False, action_name="create_ptp")
        err = exc_info.value
        assert err.invariant_id == "RI-4"

    def test_error_message_contains_action_name(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri4_commit_before_act(event_committed=False, action_name="write_to_crm")
        assert "write_to_crm" in exc_info.value.message

    def test_error_context_fields(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri4_commit_before_act(event_committed=False, action_name="send_payment_link")
        ctx = exc_info.value.context
        assert ctx["event_committed"] is False
        assert ctx["action_name"] == "send_payment_link"

    def test_is_subclass_of_exception(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri4_commit_before_act(False, "action")
