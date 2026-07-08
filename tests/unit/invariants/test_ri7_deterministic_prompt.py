"""Tests for RI-7: Prompts must be deterministic — same inputs produce same prompt.

Architecture: V1 Appendix E RI-7; V1 Ch12 (Prompt Builder); V6 AR-13.
"""

from __future__ import annotations

import pytest

from src.libs.invariants import InvariantViolationError, assert_ri7_deterministic_prompt


class TestRI7DeterministicPromptPass:
    """Cases where RI-7 is satisfied (hashes match)."""

    def test_matching_hashes(self) -> None:
        h = "abc123def456"
        assert_ri7_deterministic_prompt(
            prompt_hash=h,
            prompt_version="v3.1.2",
            expected_hash=h,
        )

    def test_matching_sha256_hashes(self) -> None:
        sha = "a" * 64  # 64-char hex string (SHA-256 length)
        assert_ri7_deterministic_prompt(
            prompt_hash=sha,
            prompt_version="v1.0.0",
            expected_hash=sha,
        )


class TestRI7DeterministicPromptFail:
    """Cases where RI-7 is violated (hash mismatch)."""

    def test_different_hashes_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri7_deterministic_prompt(
                prompt_hash="hash-A",
                prompt_version="v3.1.2",
                expected_hash="hash-B",
            )
        err = exc_info.value
        assert err.invariant_id == "RI-7"

    def test_error_message_contains_version(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri7_deterministic_prompt(
                prompt_hash="wrong-hash",
                prompt_version="v2.5.0",
                expected_hash="correct-hash",
            )
        assert "v2.5.0" in exc_info.value.message

    def test_error_context_contains_hashes(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri7_deterministic_prompt(
                prompt_hash="got-this",
                prompt_version="v1.0.0",
                expected_hash="expected-that",
            )
        ctx = exc_info.value.context
        assert ctx["prompt_hash"] == "got-this"
        assert ctx["expected_hash"] == "expected-that"
        assert ctx["prompt_version"] == "v1.0.0"

    def test_empty_hash_raises(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri7_deterministic_prompt(
                prompt_hash="",
                prompt_version="v1",
                expected_hash="something",
            )

    def test_is_subclass_of_exception(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri7_deterministic_prompt("h1", "v1", "h2")
