"""Tests for RI-1: No blocking I/O on the real-time audio path.

Each test covers both the passing case (no violation) and the failing case
(InvariantViolationError raised).

Architecture: V1 Appendix E RI-1; V6 AR-9.
"""

from __future__ import annotations

import pytest

from src.libs.invariants import InvariantViolationError, assert_ri1_realtime_purity


class TestRI1RealtimePurityPass:
    """Cases where RI-1 is satisfied — no exception raised."""

    def test_non_blocking_call_any_duration(self) -> None:
        """Non-blocking operations are always permitted regardless of duration."""
        assert_ri1_realtime_purity(is_blocking_call=False, duration_ms=100.0)

    def test_non_blocking_zero_duration(self) -> None:
        assert_ri1_realtime_purity(is_blocking_call=False, duration_ms=0.0)

    def test_blocking_call_under_1ms(self) -> None:
        """A blocking call completing in ≤1ms is at the boundary."""
        assert_ri1_realtime_purity(is_blocking_call=True, duration_ms=0.5)

    def test_blocking_call_exactly_1ms(self) -> None:
        """Exactly 1ms is permitted (limit is > 1.0, not >= 1.0)."""
        assert_ri1_realtime_purity(is_blocking_call=True, duration_ms=1.0)

    def test_non_blocking_large_duration(self) -> None:
        """Duration doesn't matter if not blocking."""
        assert_ri1_realtime_purity(is_blocking_call=False, duration_ms=5000.0)


class TestRI1RealtimePurityFail:
    """Cases where RI-1 is violated — InvariantViolationError must be raised."""

    def test_blocking_call_exceeds_1ms(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri1_realtime_purity(is_blocking_call=True, duration_ms=1.1)
        err = exc_info.value
        assert err.invariant_id == "RI-1"
        assert "1.1" in err.message or "blocking" in err.message.lower()

    def test_blocking_call_50ms(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri1_realtime_purity(is_blocking_call=True, duration_ms=50.0)
        assert exc_info.value.invariant_id == "RI-1"

    def test_blocking_call_large_duration(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri1_realtime_purity(is_blocking_call=True, duration_ms=2000.0)
        assert exc_info.value.invariant_id == "RI-1"

    def test_error_context_contains_duration(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri1_realtime_purity(is_blocking_call=True, duration_ms=25.0)
        ctx = exc_info.value.context
        assert "duration_ms" in ctx
        assert ctx["duration_ms"] == 25.0

    def test_error_is_subclass_of_exception(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri1_realtime_purity(is_blocking_call=True, duration_ms=5.0)
