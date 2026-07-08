"""InvariantViolationError — the exception raised when a runtime invariant fails.

All eight RI-1 through RI-8 guards raise this exception on violation.
It carries a stable ``invariant_id``, a human-readable message, and a
structured ``context`` dict so alert systems and logs can route and display
violations without parsing the message string.

Architecture: V1 Appendix E (Runtime Invariants RI-1…RI-8);
              V6 Ch4 AR-9 through AR-14; CLAUDE.md Engineering Principles.
"""

from __future__ import annotations


class InvariantViolationError(Exception):
    """Raised when a VoiceOS runtime invariant (RI-1 … RI-8) is violated.

    Invariant violations are always programmer errors, never user errors.
    They indicate that a precondition the system relies on for correctness,
    safety, or compliance has been breached.

    The ``invariant_id`` field is a stable identifier (e.g., 'RI-1') that
    alert routing and dashboards can filter on without parsing message text.

    Architecture: V1 Appendix E; V6 Ch4.
    """

    def __init__(
        self,
        invariant_id: str,
        message: str,
        context: dict[str, str | int | float | bool | None],
    ) -> None:
        """Create an InvariantViolationError.

        Args:
            invariant_id: Stable invariant identifier (e.g. 'RI-1', 'RI-5').
            message: Human-readable description of the violation.
            context: Structured key→value context for alerting and logs.
                     Values must be JSON-serialisable primitives.
        """
        super().__init__(f"[{invariant_id}] {message}")
        self.invariant_id = invariant_id
        self.message = message
        self.context = context

    def __repr__(self) -> str:
        return f"InvariantViolationError(invariant_id={self.invariant_id!r}, message={self.message!r})"
