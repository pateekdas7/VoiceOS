"""Invariant suite smoke tests — verifies all 8 guards are importable and callable.

Architecture: V1 Appendix E (RI-1 through RI-8).
"""

from __future__ import annotations

import inspect

from src.libs.invariants import (
    InvariantViolationError,
    assert_ri1_realtime_purity,
    assert_ri2_single_writer,
    assert_ri3_bounded_buffer,
    assert_ri4_commit_before_act,
    assert_ri5_law_of_authority,
    assert_ri6_output_coherence,
    assert_ri7_deterministic_prompt,
    assert_ri8_oom_by_construction,
)


def test_invariant_violation_error_is_exception_subclass() -> None:
    assert issubclass(InvariantViolationError, Exception)


def test_invariant_violation_error_is_instantiable() -> None:
    err = InvariantViolationError(
        invariant_id="RI-X",
        message="test",
        context={"k": "v"},
    )
    assert err.invariant_id == "RI-X"
    assert err.message == "test"
    assert err.context == {"k": "v"}
    assert "RI-X" in str(err)


def test_invariant_violation_error_repr() -> None:
    err = InvariantViolationError(invariant_id="RI-5", message="unauthorized", context={})
    r = repr(err)
    assert "RI-5" in r
    assert "unauthorized" in r


def test_all_guards_are_callable() -> None:
    guards = [
        assert_ri1_realtime_purity,
        assert_ri2_single_writer,
        assert_ri3_bounded_buffer,
        assert_ri4_commit_before_act,
        assert_ri5_law_of_authority,
        assert_ri6_output_coherence,
        assert_ri7_deterministic_prompt,
        assert_ri8_oom_by_construction,
    ]
    for guard in guards:
        assert callable(guard), f"{guard.__name__} is not callable"


def test_all_guards_are_functions() -> None:
    guards = [
        assert_ri1_realtime_purity,
        assert_ri2_single_writer,
        assert_ri3_bounded_buffer,
        assert_ri4_commit_before_act,
        assert_ri5_law_of_authority,
        assert_ri6_output_coherence,
        assert_ri7_deterministic_prompt,
        assert_ri8_oom_by_construction,
    ]
    for guard in guards:
        assert inspect.isfunction(guard), f"{guard.__name__} is not a function"


def test_guard_count_is_eight() -> None:
    guards = [
        assert_ri1_realtime_purity,
        assert_ri2_single_writer,
        assert_ri3_bounded_buffer,
        assert_ri4_commit_before_act,
        assert_ri5_law_of_authority,
        assert_ri6_output_coherence,
        assert_ri7_deterministic_prompt,
        assert_ri8_oom_by_construction,
    ]
    assert len(guards) == 8
