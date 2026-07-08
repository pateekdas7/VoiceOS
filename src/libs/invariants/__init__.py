"""VoiceOS v2 — runtime invariant guards.

Exports the InvariantViolationError and all eight RI guards so any module
can import them with a single line:

    from src.libs.invariants import assert_ri1_realtime_purity, InvariantViolationError

Architecture: V1 Appendix E (RI-1 … RI-8); V6 Ch4 AR-9 through AR-14.
"""

from .errors import InvariantViolationError
from .guards import (
    assert_ri1_realtime_purity,
    assert_ri2_single_writer,
    assert_ri3_bounded_buffer,
    assert_ri4_commit_before_act,
    assert_ri5_law_of_authority,
    assert_ri6_output_coherence,
    assert_ri7_deterministic_prompt,
    assert_ri8_oom_by_construction,
)

__all__ = [
    "InvariantViolationError",
    "assert_ri1_realtime_purity",
    "assert_ri2_single_writer",
    "assert_ri3_bounded_buffer",
    "assert_ri4_commit_before_act",
    "assert_ri5_law_of_authority",
    "assert_ri6_output_coherence",
    "assert_ri7_deterministic_prompt",
    "assert_ri8_oom_by_construction",
]
