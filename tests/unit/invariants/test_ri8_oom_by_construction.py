"""Tests for RI-8: OOM-by-construction — VRAM must be reserved before model load.

Architecture: V1 Appendix E RI-8; V1 Ch26 (GPU Scheduler); V7 Ch3.
"""

from __future__ import annotations

import pytest

from src.libs.invariants import InvariantViolationError, assert_ri8_oom_by_construction


class TestRI8OomByConstructionPass:
    """Cases where RI-8 is satisfied (sufficient VRAM available)."""

    def test_requested_less_than_available(self) -> None:
        assert_ri8_oom_by_construction(
            requested_vram_mb=4096,
            available_vram_mb=8192,
            service_name="stt-service",
        )

    def test_requested_equals_available(self) -> None:
        assert_ri8_oom_by_construction(
            requested_vram_mb=8192,
            available_vram_mb=8192,
            service_name="llm-service",
        )

    def test_small_model_large_gpu(self) -> None:
        assert_ri8_oom_by_construction(
            requested_vram_mb=512,
            available_vram_mb=24576,
            service_name="tts-service",
        )

    def test_zero_requested(self) -> None:
        assert_ri8_oom_by_construction(
            requested_vram_mb=0,
            available_vram_mb=8192,
            service_name="cpu-service",
        )


class TestRI8OomByConstructionFail:
    """Cases where RI-8 is violated (insufficient VRAM)."""

    def test_requested_exceeds_available_raises(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri8_oom_by_construction(
                requested_vram_mb=16384,
                available_vram_mb=8192,
                service_name="llm-service",
            )
        err = exc_info.value
        assert err.invariant_id == "RI-8"

    def test_error_message_contains_service_name(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri8_oom_by_construction(
                requested_vram_mb=9000,
                available_vram_mb=8192,
                service_name="whisper-large-v3-turbo",
            )
        assert "whisper-large-v3-turbo" in exc_info.value.message

    def test_error_message_contains_mb_values(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri8_oom_by_construction(
                requested_vram_mb=12000,
                available_vram_mb=8192,
                service_name="llm-service",
            )
        msg = exc_info.value.message
        assert "12000" in msg or "8192" in msg

    def test_error_context_contains_all_fields(self) -> None:
        with pytest.raises(InvariantViolationError) as exc_info:
            assert_ri8_oom_by_construction(
                requested_vram_mb=20000,
                available_vram_mb=16000,
                service_name="llama3-70b",
            )
        ctx = exc_info.value.context
        assert ctx["requested_vram_mb"] == 20000
        assert ctx["available_vram_mb"] == 16000
        assert ctx["service_name"] == "llama3-70b"

    def test_is_subclass_of_exception(self) -> None:
        with pytest.raises(InvariantViolationError):
            assert_ri8_oom_by_construction(9999, 1000, "svc")
