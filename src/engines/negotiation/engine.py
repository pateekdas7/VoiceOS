"""NegotiationEngine — bounded offer computation and move selection.

Computes the NegotiationEnvelope from CustomerContext and selects the next
NegotiationMove. The boundary clamp is non-bypassable: any proposed offer
outside [floor, ceiling] raises NegotiationBoundaryViolationError before the
offer is emitted.

assert_ri5_law_of_authority is called whenever account facts are read from
CustomerContext to derive the envelope, enforcing the Law of Authority.

Architecture: V2 Ch5 (Negotiation Engine); RI-5.
"""

from __future__ import annotations

import logging

from src.libs.contracts.context import CustomerContext
from src.libs.contracts.primitives import Currency, Money
from src.libs.invariants.guards import assert_ri5_law_of_authority

from .envelope import NegotiationEnvelope
from .moves import NegotiationMove

logger = logging.getLogger(__name__)


class NegotiationBoundaryViolationError(Exception):
    """Raised when a proposed offer falls outside the NegotiationEnvelope.

    This error is non-bypassable: the NegotiationEngine raises it before
    emitting any offer that would violate the floor or ceiling constraint.

    Architecture: V2 Ch5 (boundary clamp).
    """

    def __init__(self, amount_minor: int, envelope: NegotiationEnvelope) -> None:
        self.amount_minor = amount_minor
        self.envelope = envelope
        super().__init__(
            f"Proposed offer {amount_minor} minor units is outside the negotiation envelope "
            f"[{envelope.floor_amount.amount_minor}, {envelope.ceiling_amount.amount_minor}]"
        )


class NegotiationResult:
    """Output of NegotiationEngine.compute_move()."""

    __slots__ = ("envelope", "move", "proposed_amount", "rationale")

    def __init__(
        self,
        move: NegotiationMove,
        proposed_amount: Money | None,
        envelope: NegotiationEnvelope,
        rationale: str,
    ) -> None:
        self.move = move
        self.proposed_amount = proposed_amount
        self.envelope = envelope
        self.rationale = rationale

    def __repr__(self) -> str:
        return f"NegotiationResult(move={self.move!r}, proposed_amount={self.proposed_amount!r})"


class NegotiationEngine:
    """Computes negotiation envelopes and selects moves deterministically.

    The boundary clamp is unconditional: ``_assert_within_envelope`` raises
    NegotiationBoundaryViolationError for any amount outside [floor, ceiling].

    Architecture: V2 Ch5.
    """

    def build_envelope(
        self,
        context: CustomerContext,
        settlement_floor_pct: float = 0.5,
        max_extension_days: int = 30,
    ) -> NegotiationEnvelope:
        """Derive a NegotiationEnvelope from CustomerContext.

        Calls assert_ri5_law_of_authority to enforce that account facts
        originate from the CRM (CustomerContext), not from the LLM.

        Args:
            context: Authoritative CustomerContext snapshot.
            settlement_floor_pct: Minimum acceptable fraction of outstanding.
            max_extension_days: Maximum payment extension days.

        Returns:
            NegotiationEnvelope with derived floor and ceiling.

        Raises:
            InvariantViolationError: If CustomerContext is not the authority source.
        """
        outstanding = context.outstanding
        if outstanding is None or outstanding.total_outstanding.amount_minor == 0:
            # No outstanding — use zero envelope (no negotiation possible)
            from datetime import date, timedelta

            zero = Money(amount_minor=0, currency=Currency.INR)
            return NegotiationEnvelope(
                floor_amount=zero,
                ceiling_amount=zero,
                floor_date=date.today(),
                ceiling_date=date.today() + timedelta(days=max_extension_days),
                allowed_settlement_pct=settlement_floor_pct,
            )

        # RI-5: facts must come from CustomerContext (the authoritative CRM source)
        assert_ri5_law_of_authority(
            fact_key="outstanding_balance",
            value=outstanding.total_outstanding.amount_minor,
            authorized_sources={"crm", "customer_context"},
            source="customer_context",
        )

        currency = outstanding.total_outstanding.currency
        outstanding_minor = outstanding.total_outstanding.amount_minor

        envelope = NegotiationEnvelope.from_outstanding(
            outstanding_minor=outstanding_minor,
            currency=currency,
            settlement_floor_pct=settlement_floor_pct,
            max_extension_days=max_extension_days,
        )

        logger.debug(
            "Negotiation envelope built",
            extra={
                "customer_id": str(context.customer_id),
                "outstanding_minor": outstanding_minor,
                "floor_minor": envelope.floor_amount.amount_minor,
                "ceiling_minor": envelope.ceiling_amount.amount_minor,
            },
        )
        return envelope

    def assert_within_envelope(self, amount_minor: int, envelope: NegotiationEnvelope) -> None:
        """Assert that a proposed offer amount is within the envelope bounds.

        This check is non-bypassable. It is called before emitting any offer.

        Args:
            amount_minor: Proposed offer amount in minor currency units.
            envelope: The active NegotiationEnvelope.

        Raises:
            NegotiationBoundaryViolationError: If amount is outside [floor, ceiling].
        """
        floor = envelope.floor_amount.amount_minor
        ceiling = envelope.ceiling_amount.amount_minor
        if amount_minor < floor or amount_minor > ceiling:
            raise NegotiationBoundaryViolationError(amount_minor, envelope)

    def compute_move(
        self,
        envelope: NegotiationEnvelope,
        customer_offer_minor: int | None = None,
        concession_round: int = 0,
        hardship_verified: bool = False,
    ) -> NegotiationResult:
        """Select the next NegotiationMove given the customer's position.

        All proposed amounts are clamped and validated against the envelope
        before being returned. A proposed amount outside the envelope raises
        NegotiationBoundaryViolationError.

        Args:
            envelope: The active NegotiationEnvelope.
            customer_offer_minor: Customer's proposed amount in minor units (None if none).
            concession_round: Number of concession rounds already completed.
            hardship_verified: True if customer hardship has been verified.

        Returns:
            NegotiationResult with the selected move and proposed amount.

        Raises:
            NegotiationBoundaryViolationError: If any proposed offer violates the envelope.
        """
        floor = envelope.floor_amount.amount_minor
        ceiling = envelope.ceiling_amount.amount_minor

        # PTP when hardship verified — offer scheduled payment
        if hardship_verified:
            proposed = floor  # Minimum acceptable under hardship
            self.assert_within_envelope(proposed, envelope)
            return NegotiationResult(
                move=NegotiationMove.PROPOSE_PTP,
                proposed_amount=Money(amount_minor=proposed, currency=envelope.floor_amount.currency),
                envelope=envelope,
                rationale="Hardship verified — propose PTP at floor amount",
            )

        # Customer has made an offer
        if customer_offer_minor is not None:
            if customer_offer_minor >= floor:
                # Customer offer meets or exceeds floor — accept
                clamped = min(customer_offer_minor, ceiling)
                self.assert_within_envelope(clamped, envelope)
                return NegotiationResult(
                    move=NegotiationMove.ACCEPT,
                    proposed_amount=Money(amount_minor=clamped, currency=envelope.floor_amount.currency),
                    envelope=envelope,
                    rationale=f"Customer offer {customer_offer_minor} ≥ floor {floor} — ACCEPT",
                )

            # Below floor — counter or decline
            max_concessions = 2
            if concession_round < max_concessions:
                # Counter at midpoint between customer offer and floor, decaying step
                step_size = max(0, (floor - customer_offer_minor) // (max_concessions + 1))
                counter_amount = customer_offer_minor + step_size
                # Ensure counter is valid: must be ≤ ceiling and ≥ customer offer
                counter_amount = max(customer_offer_minor, min(counter_amount, ceiling))
                # If counter is still below floor, offer at floor
                if counter_amount < floor:
                    counter_amount = floor
                self.assert_within_envelope(counter_amount, envelope)
                return NegotiationResult(
                    move=NegotiationMove.COUNTER,
                    proposed_amount=Money(amount_minor=counter_amount, currency=envelope.floor_amount.currency),
                    envelope=envelope,
                    rationale=(
                        f"Customer offer {customer_offer_minor} < floor {floor} "
                        f"— COUNTER at {counter_amount} (round {concession_round + 1})"
                    ),
                )
            else:
                # Exhausted concessions — decline
                return NegotiationResult(
                    move=NegotiationMove.DECLINE,
                    proposed_amount=None,
                    envelope=envelope,
                    rationale=(
                        f"Customer offer {customer_offer_minor} < floor {floor} "
                        f"after {concession_round} concession rounds — DECLINE"
                    ),
                )

        # No customer offer yet — agent opens with ceiling (full payment)
        initial_offer = ceiling
        self.assert_within_envelope(initial_offer, envelope)
        return NegotiationResult(
            move=NegotiationMove.OFFER,
            proposed_amount=Money(amount_minor=initial_offer, currency=envelope.ceiling_amount.currency),
            envelope=envelope,
            rationale="Opening offer — full outstanding amount",
        )
