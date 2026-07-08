"""PromptContract — RI-7 prompt determinism gate.

Enforces that every prompt submitted to the LLM has a non-empty hash,
enabling replay and audit of the full generation lineage.

Sprint-009 implements the structural gate (hash must be present and
non-empty).  The full determinism check (hash == expected_hash per template
version) is implemented by PromptBuilder in Sprint-012.

Architecture: V1 Appendix E RI-7; V1 Ch12 (PromptBuilder); V6 AR-13.
"""

from __future__ import annotations

import hashlib

from src.libs.invariants.errors import InvariantViolationError


class PromptContractError(InvariantViolationError):
    """Raised when a prompt fails the RI-7 structural gate."""

    def __init__(self, message: str) -> None:
        super().__init__(
            invariant_id="RI-7",
            message=message,
            context={},
        )


class PromptContract:
    """Gate that enforces RI-7 prompt determinism before LLM submission.

    Sprint-009 scope: validates that the prompt hash is non-empty (structural
    check only).  Sprint-012 will extend this to compare against the expected
    hash stored in the versioned prompt registry.

    Usage::

        contract = PromptContract()
        contract.validate(prompt_hash)  # raises PromptContractError if invalid

    Architecture: V1 Appendix E RI-7.
    """

    def validate(self, prompt_hash: str) -> None:
        """Assert that the prompt hash satisfies the RI-7 structural gate.

        Args:
            prompt_hash: SHA-256 hex digest of the assembled prompt.

        Raises:
            PromptContractError: When ``prompt_hash`` is empty or None.
        """
        if not prompt_hash or not prompt_hash.strip():
            raise PromptContractError(
                "RI-7 violated: prompt_hash is empty. "
                "Every LLM submission must carry a non-empty prompt hash for "
                "replay and audit (V1 Appendix E RI-7)."
            )

    @staticmethod
    def hash_prompt(prompt: str) -> str:
        """Compute the SHA-256 hex digest of a prompt string.

        Args:
            prompt: The fully assembled prompt text.

        Returns:
            64-character lowercase hex string.
        """
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
