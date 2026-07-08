"""Unit tests for NegotiationEngine.

Covers the critical boundary invariant (100% within envelope), all move types,
RI-5 law-of-authority enforcement, and acceptance criteria.
Architecture: V2 Ch5.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.engines.negotiation.engine import (
    NegotiationBoundaryViolationError,
    NegotiationEngine,
    NegotiationResult,
)
from src.engines.negotiation.envelope import NegotiationEnvelope
from src.engines.negotiation.moves import NegotiationMove
from src.libs.contracts.context import (
    ConsentStatus,
    ContactInfo,
    CustomerContext,
    OutstandingBalance,
    PartyInfo,
)
from src.libs.contracts.primitives import Currency, CustomerId, Money, TenantId

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(
    floor_minor: int = 50_000,
    ceiling_minor: int = 100_000,
    extension_days: int = 30,
) -> NegotiationEnvelope:
    today = date.today()
    return NegotiationEnvelope(
        floor_amount=Money(amount_minor=floor_minor, currency=Currency.INR),
        ceiling_amount=Money(amount_minor=ceiling_minor, currency=Currency.INR),
        floor_date=today,
        ceiling_date=today + timedelta(days=extension_days),
    )


def _make_context(outstanding_minor: int = 100_000) -> CustomerContext:
    return CustomerContext(
        customer_id=CustomerId("cust-001"),
        tenant_id=TenantId("tenant-001"),
        primary_party=PartyInfo(
            party_id=CustomerId("cust-001"),
            role="BORROWER",
            name="Test Customer",
            contact=ContactInfo(phone_number="+919876543210"),  # type: ignore[arg-type]
        ),
        outstanding=OutstandingBalance(
            total_outstanding=Money(amount_minor=outstanding_minor, currency=Currency.INR),
            total_overdue=Money(amount_minor=outstanding_minor // 2, currency=Currency.INR),
            account_count=1,
        ),
        consent_status=ConsentStatus.GRANTED,
    )


@pytest.fixture
def engine() -> NegotiationEngine:
    return NegotiationEngine()


@pytest.fixture
def envelope() -> NegotiationEnvelope:
    return _make_envelope(floor_minor=50_000, ceiling_minor=100_000)


# ---------------------------------------------------------------------------
# Required named tests (AC)
# ---------------------------------------------------------------------------


def test_negotiation_boundary_clamp_floor(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """Offer below floor → NegotiationBoundaryViolationError."""
    with pytest.raises(NegotiationBoundaryViolationError) as exc_info:
        engine.assert_within_envelope(49_999, envelope)
    assert exc_info.value.amount_minor == 49_999
    assert exc_info.value.envelope is envelope


def test_negotiation_boundary_clamp_ceiling(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """Offer above ceiling → NegotiationBoundaryViolationError."""
    with pytest.raises(NegotiationBoundaryViolationError) as exc_info:
        engine.assert_within_envelope(100_001, envelope)
    assert exc_info.value.amount_minor == 100_001


def test_negotiation_valid_offer_within_envelope(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """Offer within floor/ceiling → passes (no exception)."""
    engine.assert_within_envelope(50_000, envelope)
    engine.assert_within_envelope(75_000, envelope)
    engine.assert_within_envelope(100_000, envelope)


# ---------------------------------------------------------------------------
# Boundary invariant — 1000 generated offers must be within envelope
# ---------------------------------------------------------------------------


def test_boundary_invariant_1000_offers(engine: NegotiationEngine) -> None:
    """100% of generated offers must be within the envelope (critical invariant)."""
    import random

    rng = random.Random(42)
    env = _make_envelope(floor_minor=10_000, ceiling_minor=500_000)
    violations = 0
    total = 1000

    for _ in range(total):
        # Vary customer offers: some below floor, some within, some above
        customer_offer = rng.randint(0, 600_000)
        concession_round = rng.randint(0, 3)
        try:
            result = engine.compute_move(
                envelope=env,
                customer_offer_minor=customer_offer,
                concession_round=concession_round,
            )
            # If a proposed amount is returned, verify it's within envelope
            if result.proposed_amount is not None:
                minor = result.proposed_amount.amount_minor
                floor = env.floor_amount.amount_minor
                ceiling = env.ceiling_amount.amount_minor
                if minor < floor or minor > ceiling:
                    violations += 1
        except NegotiationBoundaryViolationError:
            # The engine raised the error — this is the correct protective behaviour
            # (the move was internally rejected, not emitted)
            # This test checks *emitted* offers, so an exception means boundary held
            pass

    assert violations == 0, f"{violations}/{total} emitted offers violated the envelope"


# ---------------------------------------------------------------------------
# Move selection logic
# ---------------------------------------------------------------------------


def test_opening_move_is_offer_at_ceiling(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """No customer offer → agent opens with OFFER at ceiling."""
    result = engine.compute_move(envelope, customer_offer_minor=None)
    assert result.move == NegotiationMove.OFFER
    assert result.proposed_amount is not None
    assert result.proposed_amount.amount_minor == envelope.ceiling_amount.amount_minor


def test_customer_offer_above_floor_gives_accept(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """Customer offer ≥ floor → ACCEPT."""
    result = engine.compute_move(envelope, customer_offer_minor=60_000)
    assert result.move == NegotiationMove.ACCEPT
    assert result.proposed_amount is not None
    assert result.proposed_amount.amount_minor == 60_000


def test_customer_offer_at_floor_gives_accept(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """Customer offer at exactly the floor → ACCEPT."""
    result = engine.compute_move(envelope, customer_offer_minor=50_000)
    assert result.move == NegotiationMove.ACCEPT


def test_customer_offer_below_floor_round_0_gives_counter(
    engine: NegotiationEngine, envelope: NegotiationEnvelope
) -> None:
    """Customer offer below floor, first round → COUNTER."""
    result = engine.compute_move(envelope, customer_offer_minor=30_000, concession_round=0)
    assert result.move == NegotiationMove.COUNTER
    assert result.proposed_amount is not None
    # Counter must be within envelope
    assert result.proposed_amount.amount_minor >= envelope.floor_amount.amount_minor
    assert result.proposed_amount.amount_minor <= envelope.ceiling_amount.amount_minor


def test_exhausted_concessions_gives_decline(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """Below floor after max concessions → DECLINE."""
    result = engine.compute_move(envelope, customer_offer_minor=10_000, concession_round=2)
    assert result.move == NegotiationMove.DECLINE
    assert result.proposed_amount is None


def test_hardship_verified_gives_propose_ptp(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """Hardship verified → PROPOSE_PTP at floor amount."""
    result = engine.compute_move(envelope, hardship_verified=True)
    assert result.move == NegotiationMove.PROPOSE_PTP
    assert result.proposed_amount is not None
    assert result.proposed_amount.amount_minor == envelope.floor_amount.amount_minor


# ---------------------------------------------------------------------------
# Envelope construction from CustomerContext (RI-5)
# ---------------------------------------------------------------------------


def test_build_envelope_from_context(engine: NegotiationEngine) -> None:
    """build_envelope derives bounds from CustomerContext (RI-5)."""
    ctx = _make_context(outstanding_minor=200_000)
    env = engine.build_envelope(ctx, settlement_floor_pct=0.5)
    assert env.ceiling_amount.amount_minor == 200_000
    assert env.floor_amount.amount_minor >= 100_000  # 50% of 200_000


def test_build_envelope_floor_le_ceiling(engine: NegotiationEngine) -> None:
    """Derived envelope always has floor ≤ ceiling."""
    ctx = _make_context(outstanding_minor=100_000)
    env = engine.build_envelope(ctx)
    assert env.floor_amount <= env.ceiling_amount


def test_build_envelope_no_outstanding(engine: NegotiationEngine) -> None:
    """Zero outstanding → zero envelope (no negotiation possible)."""
    ctx = _make_context(outstanding_minor=0)
    env = engine.build_envelope(ctx)
    assert env.floor_amount.amount_minor == 0
    assert env.ceiling_amount.amount_minor == 0


# ---------------------------------------------------------------------------
# Error message content
# ---------------------------------------------------------------------------


def test_boundary_error_message_contains_amounts(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    """NegotiationBoundaryViolationError message mentions the bad amount."""
    with pytest.raises(NegotiationBoundaryViolationError) as exc_info:
        engine.assert_within_envelope(1, envelope)
    assert "1" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------


def test_envelope_rejects_floor_greater_than_ceiling() -> None:
    """NegotiationEnvelope raises ValueError if floor > ceiling."""
    today = date.today()
    with pytest.raises(ValueError, match="floor_amount"):
        NegotiationEnvelope(
            floor_amount=Money(amount_minor=100_000, currency=Currency.INR),
            ceiling_amount=Money(amount_minor=50_000, currency=Currency.INR),
            floor_date=today,
            ceiling_date=today + timedelta(days=30),
        )


def test_envelope_rejects_floor_date_after_ceiling_date() -> None:
    today = date.today()
    with pytest.raises(ValueError, match="floor_date"):
        NegotiationEnvelope(
            floor_amount=Money(amount_minor=10_000, currency=Currency.INR),
            ceiling_amount=Money(amount_minor=50_000, currency=Currency.INR),
            floor_date=today + timedelta(days=10),
            ceiling_date=today,
        )


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


def test_result_is_negotiation_result(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    result = engine.compute_move(envelope)
    assert isinstance(result, NegotiationResult)
    assert isinstance(result.move, NegotiationMove)
    assert isinstance(result.rationale, str) and result.rationale


def test_all_moves_have_envelope_reference(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    result = engine.compute_move(envelope)
    assert result.envelope is envelope


# ---------------------------------------------------------------------------
# Boundary: exactly at limits
# ---------------------------------------------------------------------------


def test_offer_at_floor_is_valid(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    engine.assert_within_envelope(50_000, envelope)


def test_offer_at_ceiling_is_valid(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    engine.assert_within_envelope(100_000, envelope)


def test_offer_one_below_floor_invalid(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    with pytest.raises(NegotiationBoundaryViolationError):
        engine.assert_within_envelope(49_999, envelope)


def test_offer_one_above_ceiling_invalid(engine: NegotiationEngine, envelope: NegotiationEnvelope) -> None:
    with pytest.raises(NegotiationBoundaryViolationError):
        engine.assert_within_envelope(100_001, envelope)
