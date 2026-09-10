"""Installment-plan arithmetic for the scripted-response golden path
(Path-A Phase 6f).

Ported from evaluation/founder-validation/conv_server.py's compute_emi_hint().
This is deterministic arithmetic (months = ceil(outstanding / monthly_offer)),
not business logic or policy — no existing engine computes it, so it lives
here rather than duplicating anything in NegotiationEngine or StrategyEngine.
NegotiationEngine bounds *whether* an offer is acceptable (floor/ceiling); this
module only computes the month count so the scripted reply can state it.

Architecture: V2 Ch8 (Negotiation Engine bounds this module's inputs).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class InstallmentPlanKind(str, Enum):
    LUMPSUM_FULL = "lumpsum_full"
    LUMPSUM_PARTIAL = "lumpsum_partial"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class InstallmentPlan:
    kind: InstallmentPlanKind
    amount_minor: int
    months: int | None = None
    remaining_minor: int | None = None


def compute_installment_plan(
    outstanding_minor: int,
    offered_amount_minor: int,
    cadence: str | None,
) -> InstallmentPlan:
    """Compute a deterministic installment plan for a customer's offer.

    Args:
        outstanding_minor: The authoritative outstanding balance, in minor
            currency units (from CustomerContext.primary_loan, never guessed).
        offered_amount_minor: The customer's offered amount this turn, in
            minor currency units (from ResponsePlan.entities["AMOUNT"]).
        cadence: "monthly" | "one-shot" | None, from the customer's phrasing.
    """
    if cadence == "one-shot" or (cadence is None and offered_amount_minor >= outstanding_minor):
        if offered_amount_minor >= outstanding_minor:
            return InstallmentPlan(kind=InstallmentPlanKind.LUMPSUM_FULL, amount_minor=offered_amount_minor)
        remaining = outstanding_minor - offered_amount_minor
        return InstallmentPlan(
            kind=InstallmentPlanKind.LUMPSUM_PARTIAL,
            amount_minor=offered_amount_minor,
            remaining_minor=remaining,
        )
    months = math.ceil(outstanding_minor / offered_amount_minor)
    return InstallmentPlan(kind=InstallmentPlanKind.MONTHLY, amount_minor=offered_amount_minor, months=months)


__all__ = ["InstallmentPlan", "InstallmentPlanKind", "compute_installment_plan"]
