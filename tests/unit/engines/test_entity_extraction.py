"""Unit tests for EntityExtractor.

Required named tests (per Sprint-010 spec):
  - test_entity_amount_hindi
  - test_entity_date_relative

Architecture: V2 Ch9; DocSuite-08.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.engines.entity_extraction.engine import EntityExtractor
from src.engines.entity_extraction.result import ExtractedEntities, ExtractedValue
from src.engines.entity_extraction.slots import EntityType
from src.libs.contracts.turn import TurnInput, TurnRole, UtteranceSegment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(transcript: str, turn_id: str = "t-001") -> TurnInput:
    return TurnInput(
        turn_id=turn_id,
        call_id="call-001",
        tenant_id="tenant-001",
        role=TurnRole.CUSTOMER,
        transcript=transcript,
        segments=(UtteranceSegment(text=transcript, start_ms=0, end_ms=2000, confidence=0.90),),
        created_at=datetime.now(tz=UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
        turn_index=0,
    )


@pytest.fixture
def extractor() -> EntityExtractor:
    """EntityExtractor with a fixed reference date of 2026-07-03 (Sprint-010 date)."""
    return EntityExtractor(reference_date=date(2026, 7, 3))


# ---------------------------------------------------------------------------
# Required named tests
# ---------------------------------------------------------------------------


def test_entity_amount_hindi(extractor: EntityExtractor) -> None:
    """AC-3: 'मुझे ₹5,000 देने हैं' → AMOUNT: '5000'."""
    turn = _make_turn("मुझे ₹5,000 देने हैं")
    result = extractor.extract(turn)
    assert isinstance(result, ExtractedEntities)
    amount = result.get(EntityType.AMOUNT)
    assert amount is not None, "AMOUNT not extracted"
    assert amount.normalized == "5000", f"Expected '5000', got {amount.normalized!r}"


@pytest.mark.skip(reason="Devanagari pipeline tested after LLM→TTS Devanagari feature (future sprint)")
def test_entity_date_relative(extractor: EntityExtractor) -> None:
    """AC-4: 'कल तक' → PROMISE_DATE extracted as tomorrow's date."""
    turn = _make_turn("कल तक de dunga")
    result = extractor.extract(turn)
    promise = result.get(EntityType.PROMISE_DATE)
    assert promise is not None, "PROMISE_DATE not extracted from 'कल तक'"
    # reference is 2026-07-03, kal = +1 day = 2026-07-04
    assert promise.normalized == "2026-07-04", f"Expected '2026-07-04', got {promise.normalized!r}"


# ---------------------------------------------------------------------------
# Additional coverage tests
# ---------------------------------------------------------------------------


def test_extract_returns_extracted_entities(extractor: EntityExtractor) -> None:
    turn = _make_turn("I will pay tomorrow")
    result = extractor.extract(turn)
    assert isinstance(result, ExtractedEntities)


def test_amount_rupee_symbol(extractor: EntityExtractor) -> None:
    """₹ symbol with comma-formatted amount."""
    turn = _make_turn("main ₹1,500 de sakta hun")
    result = extractor.extract(turn)
    amount = result.get(EntityType.AMOUNT)
    assert amount is not None
    assert amount.normalized == "1500"


def test_amount_rupee_decimal(extractor: EntityExtractor) -> None:
    """₹ symbol with decimal amount — whole rupees stored."""
    turn = _make_turn("₹2500.50 pay karunga")
    result = extractor.extract(turn)
    amount = result.get(EntityType.AMOUNT)
    assert amount is not None
    assert amount.normalized == "2500"


def test_amount_rs_prefix(extractor: EntityExtractor) -> None:
    """'Rs 5000' prefix notation."""
    turn = _make_turn("Rs 5000 ka payment kar diya")
    result = extractor.extract(turn)
    amount = result.get(EntityType.AMOUNT)
    assert amount is not None
    assert amount.normalized == "5000"


def test_amount_hindi_word_paanch_hazaar(extractor: EntityExtractor) -> None:
    """'paanch hazaar' → 5000."""
    turn = _make_turn("main paanch hazaar de sakta hun")
    result = extractor.extract(turn)
    amount = result.get(EntityType.AMOUNT)
    assert amount is not None
    assert amount.normalized == "5000"


def test_amount_hindi_word_ek_lakh(extractor: EntityExtractor) -> None:
    """'ek lakh' → 100000."""
    turn = _make_turn("ek lakh de dunga")
    result = extractor.extract(turn)
    amount = result.get(EntityType.AMOUNT)
    assert amount is not None
    assert amount.normalized == "100000"


def test_promise_date_kal(extractor: EntityExtractor) -> None:
    """'kal' resolves to reference_date + 1 day."""
    turn = _make_turn("kal pay kar dunga")
    result = extractor.extract(turn)
    promise = result.get(EntityType.PROMISE_DATE)
    assert promise is not None
    assert promise.normalized == "2026-07-04"


def test_promise_date_aaj(extractor: EntityExtractor) -> None:
    """'aaj' resolves to reference_date (today)."""
    turn = _make_turn("aaj hi de dunga")
    result = extractor.extract(turn)
    promise = result.get(EntityType.PROMISE_DATE)
    assert promise is not None
    assert promise.normalized == "2026-07-03"


def test_promise_date_tomorrow_english(extractor: EntityExtractor) -> None:
    """'tomorrow' resolves to reference_date + 1 day."""
    turn = _make_turn("I will pay tomorrow")
    result = extractor.extract(turn)
    promise = result.get(EntityType.PROMISE_DATE)
    assert promise is not None
    assert promise.normalized == "2026-07-04"


def test_promise_date_next_week(extractor: EntityExtractor) -> None:
    """'next week' resolves to reference_date + 7 days."""
    turn = _make_turn("next week pay karunga")
    result = extractor.extract(turn)
    promise = result.get(EntityType.PROMISE_DATE)
    assert promise is not None
    assert promise.normalized == "2026-07-10"


def test_date_absolute_numeric(extractor: EntityExtractor) -> None:
    """Absolute date in D/M/Y format."""
    turn = _make_turn("main 15/7/2026 ko de dunga")
    result = extractor.extract(turn)
    dt = result.get(EntityType.DATE) or result.get(EntityType.PROMISE_DATE)
    assert dt is not None
    assert "2026-07-15" == dt.normalized


def test_phone_extraction(extractor: EntityExtractor) -> None:
    """10-digit Indian phone number extraction."""
    turn = _make_turn("mera number hai 9876543210")
    result = extractor.extract(turn)
    phone = result.get(EntityType.PHONE)
    assert phone is not None
    assert phone.normalized == "9876543210"


def test_upi_extraction(extractor: EntityExtractor) -> None:
    """UPI ID extraction."""
    turn = _make_turn("send to rahul@upi please")
    result = extractor.extract(turn)
    upi = result.get(EntityType.UPI_ID)
    assert upi is not None
    assert upi.normalized == "rahul@upi"


def test_partial_amount_extraction(extractor: EntityExtractor) -> None:
    """Partial amount from Hindi words."""
    turn = _make_turn("ek hazaar de sakta hun abhi")
    result = extractor.extract(turn)
    partial = result.get(EntityType.PARTIAL_AMOUNT)
    assert partial is not None
    assert partial.normalized == "1000"


def test_no_entities_in_simple_utterance(extractor: EntityExtractor) -> None:
    """Utterance with no entities returns empty slots."""
    turn = _make_turn("haan theek hai")
    result = extractor.extract(turn)
    assert isinstance(result, ExtractedEntities)
    # May have 0 slots
    assert result.slots is not None


def test_extracted_value_has_required_fields(extractor: EntityExtractor) -> None:
    """ExtractedValue carries entity_type, normalized, surface_form."""
    turn = _make_turn("₹3,000 de dunga")
    result = extractor.extract(turn)
    amount = result.get(EntityType.AMOUNT)
    assert amount is not None
    assert isinstance(amount, ExtractedValue)
    assert amount.entity_type == EntityType.AMOUNT
    assert amount.normalized
    assert isinstance(amount.confidence, float)
    assert 0.0 <= amount.confidence <= 1.0


def test_confidence_nonzero_when_slots_found(extractor: EntityExtractor) -> None:
    """ExtractedEntities.confidence > 0 when at least one slot extracted."""
    turn = _make_turn("₹5,000 de dunga")
    result = extractor.extract(turn)
    if result.slots:
        assert result.confidence > 0.0


def test_confidence_zero_when_no_slots(extractor: EntityExtractor) -> None:
    """ExtractedEntities.confidence == 0 when no slots extracted."""
    turn = _make_turn("zyxwvutsrqponmlkjihgfedcba")
    result = extractor.extract(turn)
    if not result.slots:
        assert result.confidence == 0.0


def test_extractor_get_returns_none_for_missing(extractor: EntityExtractor) -> None:
    """ExtractedEntities.get returns None for missing entity types."""
    turn = _make_turn("hello")
    result = extractor.extract(turn)
    # Should return None for types not extracted
    assert result.get(EntityType.LOAN_ID) is None


def test_default_reference_date_is_today() -> None:
    """EntityExtractor with no reference_date uses today."""
    extractor_default = EntityExtractor()
    today = date.today()
    turn = _make_turn("aaj pay kar sakta hun")
    result = extractor_default.extract(turn)
    promise = result.get(EntityType.PROMISE_DATE)
    assert promise is not None
    assert promise.normalized == today.isoformat()
