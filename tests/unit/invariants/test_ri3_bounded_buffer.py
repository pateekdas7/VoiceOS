"""Tests for RI-3: All buffers and queues must have a declared maximum depth.

Architecture: V1 Appendix E RI-3; V6 AR-11; V3 Ch10.
"""

from __future__ import annotations

import pytest

from src.libs.invariants import InvariantViolationError, assert_ri3_bounded_buffer


class TestRI3BoundedBufferPass:
    """Cases where RI-3 is satisfied (queue not full)."""

    def test_empty_queue(self) -> None:
        assert_ri3_bounded_buffer(queue_depth=0, max_depth=100, queue_name="audio-frame-queue")

    def test_queue_one_below_max(self) -> None:
        assert_ri3_bounded_buffer(queue_depth=99, max_depth=100, queue_name="tts-clause-queue")

    def test_very_small_queue_not_full(self) -> None:
        assert_ri3_bounded_buffer(queue_depth=0, max_depth=1, queue_name="control-queue")

    def test_half_full_queue(self) -> None:
        assert_ri3_bounded_buffer(queue_depth=50, max_depth=100, queue_name="llm-token-queue")


class TestRI3BoundedBufferFail:
    """Cases where RI-3 is violated (queue at or over capacity)."""

    def test_queue_at_max_depth_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri3_bounded_buffer(queue_depth=100, max_depth=100, queue_name="audio-frame-queue")
        err = exc_info.value
        assert err.invariant_id == "RI-3"

    def test_queue_over_max_depth_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri3_bounded_buffer(queue_depth=150, max_depth=100, queue_name="overflow-queue")
        assert exc_info.value.invariant_id == "RI-3"

    def test_error_message_contains_queue_name(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri3_bounded_buffer(queue_depth=10, max_depth=10, queue_name="playback-queue")
        assert "playback-queue" in exc_info.value.message

    def test_error_context_has_queue_fields(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri3_bounded_buffer(queue_depth=5, max_depth=5, queue_name="test-q")
        ctx = exc_info.value.context
        assert ctx["queue_name"] == "test-q"
        assert ctx["queue_depth"] == 5
        assert ctx["max_depth"] == 5

    def test_is_subclass_of_exception(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri3_bounded_buffer(1, 1, "q")
