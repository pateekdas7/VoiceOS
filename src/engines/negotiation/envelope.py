"""NegotiationEnvelope — bounded parameters for a negotiation session.

The NegotiationEnvelope defines the hard floor and ceiling that all offers
must stay within. The clamp is non-bypassable: the NegotiationEngine raises
NegotiationBoundaryViolationError before emitting any offer outside the
envelope.

Architecture: V2 Ch5 (Negotiation Engine); RI-5 (Law of Authority).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.libs.contracts.primitives import Currency, Money


class NegotiationEnvelope(BaseModel):
    """Hard bounds for a negotiation session.

    All amounts offered or accepted during the negotiation must lie within
    [floor_amount, ceiling_amount]. No path in the NegotiationEngine may emit
    a value outside these bounds.

    Architecture: V2 Ch5.
    """

    model_config = ConfigDict(frozen=True)

    floor_amount: Money
    """Minimum acceptable payment amount (regulatory floor + policy floor)."""

    ceiling_amount: Money
    """Maximum offered amount (full outstanding balance or campaign cap)."""

    floor_date: date
    """Earliest acceptable payment date (today or next working day)."""

    ceiling_date: date
    """Latest acceptable payment date (campaign policy maximum extension)."""

    allowed_settlement_pct: float = Field(ge=0.0, le=1.0, default=1.0)
    """Maximum settlement as a fraction of outstanding. 1.0 = full payment only."""

    @model_validator(mode="after")
    def _validate_floor_le_ceiling(self) -> NegotiationEnvelope:
        if self.floor_amount.currency != self.ceiling_amount.currency:
            raise ValueError(
                f"floor_amount and ceiling_amount must use the same currency: "
                f"floor={self.floor_amount.currency}, ceiling={self.ceiling_amount.currency}"
            )
        if self.floor_amount > self.ceiling_amount:
            raise ValueError(f"floor_amount ({self.floor_amount}) must be ≤ ceiling_amount ({self.ceiling_amount})")
        if self.floor_date > self.ceiling_date:
            raise ValueError(f"floor_date ({self.floor_date}) must be ≤ ceiling_date ({self.ceiling_date})")
        return self

    @classmethod
    def from_outstanding(
        cls,
        outstanding_minor: int,
        currency: Currency = Currency.INR,
        settlement_floor_pct: float = 0.5,
        max_extension_days: int = 30,
        reference_date: date | None = None,
    ) -> NegotiationEnvelope:
        """Derive an envelope from an outstanding balance amount.

        The floor is ``settlement_floor_pct`` of the outstanding (minimum the
        engine will accept). The ceiling is the full outstanding balance.

        Args:
            outstanding_minor: Total outstanding in minor currency units.
            currency: Currency code.
            settlement_floor_pct: Minimum acceptable fraction of outstanding.
            max_extension_days: Maximum days for payment extension from today.
            reference_date: Reference date for date bounds (defaults to today).

        Returns:
            NegotiationEnvelope with derived floor and ceiling.
        """
        import math
        from datetime import timedelta

        ref = reference_date or date.today()

        floor_minor = max(1, math.ceil(outstanding_minor * settlement_floor_pct))
        ceiling_minor = outstanding_minor

        return cls(
            floor_amount=Money(amount_minor=floor_minor, currency=currency),
            ceiling_amount=Money(amount_minor=ceiling_minor, currency=currency),
            floor_date=ref,
            ceiling_date=ref + timedelta(days=max_extension_days),
            allowed_settlement_pct=settlement_floor_pct,
        )
