"""NegotiationMove enum — the moves available to the NegotiationEngine.

Architecture: V2 Ch5 (Negotiation Engine).
"""

from __future__ import annotations

from enum import StrEnum


class NegotiationMove(StrEnum):
    """Moves the NegotiationEngine may propose or take.

    Architecture: V2 Ch5; DocSuite-03 (Data Dictionary).
    """

    OFFER = "OFFER"
    """Agent proposes an amount/instrument to the customer."""

    COUNTER = "COUNTER"
    """Agent counters the customer's proposed amount (concession step)."""

    ACCEPT = "ACCEPT"
    """Agent accepts the customer's proposed amount (≥ floor)."""

    HOLD = "HOLD"
    """Agent defers the negotiation (e.g., to verify facts or seek approval)."""

    DECLINE = "DECLINE"
    """Agent declines the customer's proposal as below the floor."""

    PROPOSE_PTP = "PROPOSE_PTP"
    """Agent proposes a Promise-to-Pay arrangement with a specific date."""
