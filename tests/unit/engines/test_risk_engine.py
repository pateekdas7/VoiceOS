"""Unit tests for RiskEngine.

Covers all 10 RiskFlags, acceptance criteria, and boundary conditions.
Architecture: V2 Ch6.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.engines.risk.engine import RiskEngine
from src.engines.risk.flags import RiskFlag
from src.engines.risk.result import RiskAssessment
from src.libs.contracts.streaming import Sentiment, StressLevel
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
        segments=(
            UtteranceSegment(
                text=transcript,
                start_ms=0,
                end_ms=2000,
                confidence=0.95,
            ),
        ),
        created_at=datetime.now(tz=UTC),
        correlation_id="corr-001",
        trace_id="trace-001",
        turn_index=0,
    )


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine()


# ---------------------------------------------------------------------------
# Required named tests (AC)
# ---------------------------------------------------------------------------


def test_risk_abuse_triggers_handoff(engine: RiskEngine) -> None:
    """ABUSE_DETECTED flag → human_handoff_required=True."""
    turn = _make_turn("Saala chutiya hai tu, marenge tujhe")
    result = engine.evaluate(turn)
    assert RiskFlag.ABUSE_DETECTED in result.flags
    assert result.human_handoff_required is True


def test_risk_hardship_no_escalation(engine: RiskEngine) -> None:
    """HARDSHIP_INDICATOR only → escalation_required=False, handoff=False."""
    turn = _make_turn("Meri naukri chali gayi hai, paise nahi hai")
    result = engine.evaluate(turn)
    assert RiskFlag.HARDSHIP_INDICATOR in result.flags
    assert result.human_handoff_required is False
    # HARDSHIP alone is not in _ESCALATION_FLAGS
    assert result.escalation_required is False


# ---------------------------------------------------------------------------
# Flag-specific tests
# ---------------------------------------------------------------------------


def test_legal_threat_flag_raises_escalation(engine: RiskEngine) -> None:
    turn = _make_turn("Main court mein jaaunga aur vakeel se baat karunga")
    result = engine.evaluate(turn)
    assert RiskFlag.LEGAL_THREAT in result.flags
    assert result.escalation_required is True
    assert result.human_handoff_required is False


def test_dispute_claim_detected(engine: RiskEngine) -> None:
    turn = _make_turn("Yeh galat hai, yeh mera loan nahi hai, dispute hai")
    result = engine.evaluate(turn)
    assert RiskFlag.DISPUTE_CLAIM in result.flags


def test_abuse_from_hostile_sentiment(engine: RiskEngine) -> None:
    """Sentiment.HOSTILE triggers ABUSE_DETECTED even without keyword match."""
    turn = _make_turn("Main kuch nahi bolunga")
    result = engine.evaluate(turn, sentiment=Sentiment.HOSTILE)
    assert RiskFlag.ABUSE_DETECTED in result.flags
    assert result.human_handoff_required is True


def test_no_flags_for_cooperative_turn(engine: RiskEngine) -> None:
    """Cooperative customer → no risk flags."""
    turn = _make_turn("Haan ji, main kal payment kar dunga, theek hai")
    result = engine.evaluate(turn)
    assert result.flags == []
    assert result.escalation_required is False
    assert result.human_handoff_required is False


def test_consent_risk_detected(engine: RiskEngine) -> None:
    turn = _make_turn("Band karo yeh calls, consent nahi hai")
    result = engine.evaluate(turn)
    assert RiskFlag.CONSENT_RISK in result.flags


def test_recording_objection_detected(engine: RiskEngine) -> None:
    turn = _make_turn("Stop recording, nahi chahta")
    result = engine.evaluate(turn)
    assert RiskFlag.RECORDING_OBJECTION in result.flags


def test_third_party_detected(engine: RiskEngine) -> None:
    turn = _make_turn("Mera bhai baat karega, main abhi nahi bolunga")
    result = engine.evaluate(turn)
    assert RiskFlag.THIRD_PARTY_ON_CALL in result.flags


def test_escalation_trigger_from_critical_stress(engine: RiskEngine) -> None:
    """CRITICAL stress without abuse keyword → ESCALATION_TRIGGER."""
    turn = _make_turn("Main bahut pareshaan hoon")
    result = engine.evaluate(turn, stress_level=StressLevel.CRITICAL)
    assert RiskFlag.ESCALATION_TRIGGER in result.flags
    assert result.escalation_required is True
    assert result.human_handoff_required is False


def test_multiple_flags_can_be_active(engine: RiskEngine) -> None:
    """Multiple flags may be raised in a single turn."""
    turn = _make_turn("Gaali mat do, court jaaunga, aur naukri chali gayi hai, paise nahi")
    result = engine.evaluate(turn)
    assert len(result.flags) >= 2


def test_risk_assessment_is_frozen(engine: RiskEngine) -> None:
    """RiskAssessment is immutable."""
    turn = _make_turn("Theek hai")
    result = engine.evaluate(turn)
    with pytest.raises(ValidationError):
        result.flags = []  # type: ignore[misc]


def test_result_type(engine: RiskEngine) -> None:
    turn = _make_turn("Theek hai")
    result = engine.evaluate(turn)
    assert isinstance(result, RiskAssessment)


def test_abuse_detection_with_multiple_keywords(engine: RiskEngine) -> None:
    turn = _make_turn("Kamina hai tu, bc, kill karunga")
    result = engine.evaluate(turn)
    assert RiskFlag.ABUSE_DETECTED in result.flags
    assert result.human_handoff_required is True


def test_elderly_vulnerable_detected(engine: RiskEngine) -> None:
    turn = _make_turn("Main bujurg hoon, pension pe hoon")
    result = engine.evaluate(turn)
    assert RiskFlag.ELDERLY_VULNERABLE in result.flags


def test_no_hardship_for_normal_call(engine: RiskEngine) -> None:
    turn = _make_turn("Main payment karna chahta hoon")
    result = engine.evaluate(turn)
    assert RiskFlag.HARDSHIP_INDICATOR not in result.flags


def test_legal_threat_triggers_escalation_not_handoff(engine: RiskEngine) -> None:
    turn = _make_turn("FIR karunga aur thane jaaunga")
    result = engine.evaluate(turn)
    assert result.escalation_required is True
    assert result.human_handoff_required is False


def test_deterministic_same_inputs(engine: RiskEngine) -> None:
    """Same inputs → same output (determinism)."""
    turn = _make_turn("Yeh galat loan hai, dispute hai, court jaaunga")
    r1 = engine.evaluate(turn)
    r2 = engine.evaluate(turn)
    assert r1.flags == r2.flags
    assert r1.escalation_required == r2.escalation_required
    assert r1.human_handoff_required == r2.human_handoff_required
