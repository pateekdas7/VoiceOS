"""RelationshipContextBuilder — builds a concise historical context block.

Historical context is injected into the LLM prompt as background information
only — it NEVER overrides current SalesState fields (which come from the
current conversation).

Architecture: VoiceOS Phase 3 Production Action Layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.engines.memory.relationship.schema import RelationshipMemory

# Maximum number of sentiment history entries to include.
_MAX_SENTIMENT_HISTORY = 3

# Maximum total lines in the output block (excluding header/blank line).
_MAX_LINES = 5


class RelationshipContextBuilder:
    """Builds a concise historical context block from RelationshipMemory.

    Historical context is injected into the LLM prompt as background
    information only — it NEVER overrides current SalesState fields
    (which come from the current conversation).

    Security: PTP amounts are never included (sensitive financial data).
    """

    @staticmethod
    def build_block(relationship_memory: "RelationshipMemory | None") -> str:
        """Return a compact prompt block with historical context.

        Returns empty string if relationship_memory is None or has no history.
        Explicitly labels content as HISTORICAL to prevent the LLM from
        treating it as current customer state.

        The block is capped at _MAX_LINES lines to avoid prompt bloat
        (architecture constraint: ≤10 lines).

        Args:
            relationship_memory: The loaded RelationshipMemory for this customer,
                or None if no cross-call history exists.

        Returns:
            Formatted prompt block string, or "" if no relevant history.
        """
        if relationship_memory is None:
            return ""

        lines: list[str] = []

        # Total past calls — only include if > 0 (first-time callers have no history).
        if relationship_memory.total_calls > 0:
            lines.append(f"- Total past calls: {relationship_memory.total_calls}")

        # Last call outcome (not PTP amounts — never include financial specifics).
        if relationship_memory.last_call_outcome:
            lines.append(f"- Last outcome: {relationship_memory.last_call_outcome}")

        # Language preference — only include if non-default (most customers use hi-IN).
        if relationship_memory.preferred_language and relationship_memory.preferred_language != "hi-IN":
            lines.append(f"- Language preference: {relationship_memory.preferred_language}")

        # Escalation history — only if non-zero (no point noting 0 escalations).
        if relationship_memory.escalation_count > 0:
            lines.append(
                f"- Escalation history: {relationship_memory.escalation_count} escalation(s)"
            )

        # Sentiment trend from last N calls (no amounts — just sentiment labels).
        if relationship_memory.sentiment_history:
            recent = relationship_memory.sentiment_history[-_MAX_SENTIMENT_HISTORY:]
            sentiment_summary = ", ".join(recent)
            lines.append(f"- Recent sentiment trend: {sentiment_summary}")

        # No history to show at all.
        if not lines:
            return ""

        # Cap to _MAX_LINES.
        lines = lines[:_MAX_LINES]

        header = (
            "HISTORICAL CUSTOMER CONTEXT "
            "(from previous calls — do NOT treat as current state):"
        )
        return "\n".join([header] + lines)
