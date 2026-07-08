"""StrategyAction enum — bounded action space for the StrategyEngine.

The StrategyEngine selects exactly one action per turn from this set.
Action selection is a deterministic lookup/scoring function — no LLM call.

Architecture: V2 Ch4 (Strategy Engine).
"""

from __future__ import annotations

from enum import StrEnum


class StrategyAction(StrEnum):
    """Available conversational actions for the StrategyEngine.

    The action space is deliberately small and bounded so that selection
    can be exhaustive (all candidates scored, top chosen) with deterministic,
    sub-millisecond latency.

    Architecture: V2 Ch4; DocSuite-03 (Data Dictionary).
    """

    ASK = "ASK"
    """Ask the customer for information needed to advance the goal."""

    VERIFY = "VERIFY"
    """Verify the customer's identity or validate a claimed fact."""

    NEGOTIATE = "NEGOTIATE"
    """Initiate or continue a negotiation move (delegates to NegotiationEngine)."""

    REASSURE = "REASSURE"
    """De-escalate or provide reassurance to a distressed customer."""

    ESCALATE = "ESCALATE"
    """Escalate to a supervisor or senior agent within the current session."""

    TRANSFER = "TRANSFER"
    """Transfer the call to a human agent queue."""

    CLOSE = "CLOSE"
    """Close the call with an appropriate closing statement."""

    CONFIRM = "CONFIRM"
    """Confirm a detail the customer has stated (amount, date, promise)."""
