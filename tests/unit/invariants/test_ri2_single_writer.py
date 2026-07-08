"""Tests for RI-2: Single-writer state — lock must be held for any mutation.

Architecture: V1 Appendix E RI-2; V6 AR-10; V3 Ch4.
"""

from __future__ import annotations

import pytest

from src.libs.invariants import InvariantViolationError, assert_ri2_single_writer


class TestRI2SingleWriterPass:
    """Cases where RI-2 is satisfied."""

    def test_valid_lock_token(self) -> None:
        assert_ri2_single_writer(
            resource_id="call:abc:state",
            writer_id="worker-1",
            lock_token="fencing-token-001",
        )

    def test_long_lock_token(self) -> None:
        assert_ri2_single_writer(
            resource_id="session:xyz",
            writer_id="cognition-worker-2",
            lock_token="a" * 128,
        )

    def test_single_char_lock_token(self) -> None:
        """Any non-empty string is a valid lock token."""
        assert_ri2_single_writer(
            resource_id="call:001:counter",
            writer_id="writer-A",
            lock_token="x",
        )


class TestRI2SingleWriterFail:
    """Cases where RI-2 is violated."""

    def test_empty_lock_token_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri2_single_writer(
                resource_id="call:abc:state",
                writer_id="worker-1",
                lock_token="",
            )
        err = exc_info.value
        assert err.invariant_id == "RI-2"

    def test_error_message_contains_resource(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri2_single_writer(
                resource_id="call:abc:state",
                writer_id="worker-1",
                lock_token="",
            )
        assert "call:abc:state" in exc_info.value.message

    def test_error_context_has_expected_keys(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri2_single_writer(
                resource_id="res-001",
                writer_id="w-1",
                lock_token="",
            )
        ctx = exc_info.value.context
        assert "resource_id" in ctx
        assert "writer_id" in ctx
        assert "lock_token" in ctx
        assert ctx["resource_id"] == "res-001"

    def test_is_subclass_of_exception(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri2_single_writer("r", "w", "")
