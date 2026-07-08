"""Unit tests for StrategyEngine.

Covers required named tests, acceptance criteria, and determinism.
Architecture: V2 Ch4.
"""

from __future__ import annotations

import pytest

from src.engines.risk.flags import RiskFlag
from src.engines.risk.result import RiskAssessment
from src.engines.strategy.actions import StrategyAction
from src.engines.strategy.engine import StrategyEngine, StrategySelection
from src.libs.contracts.response_plan import IntentLabel
from src.libs.contracts.streaming import StressLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_risk(flags: list[RiskFlag]) -> RiskAssessment:
    return RiskAssessment(
        flags=flags,
        escalation_required=any(
            f in (RiskFlag.ABUSE_DETECTED, RiskFlag.LEGAL_THREAT, RiskFlag.ESCALATION_TRIGGER) for f in flags
        ),
        human_handoff_required=RiskFlag.ABUSE_DETECTED in flags,
    )


@pytest.fixture
def engine() -> StrategyEngine:
    return StrategyEngine()


# ---------------------------------------------------------------------------
# Required named tests (AC)
# ---------------------------------------------------------------------------


def test_strategy_payment_intent_ask_action(engine: StrategyEngine) -> None:
    """Intent=PAYMENT, state=DEBT_DISCUSSION → action=ASK."""
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        risk=None,
        stress_level=StressLevel.LOW,
        identity_verified=True,
    )
    assert result.action == StrategyAction.ASK


def test_strategy_dispute_triggers_verify(engine: StrategyEngine) -> None:
    """Intent=DISPUTE → action=VERIFY."""
    result = engine.select(
        primary_intent=IntentLabel.DISPUTE,
        conversation_state="DEBT_DISCUSSION",
        risk=None,
        stress_level=StressLevel.LOW,
        identity_verified=True,
    )
    assert result.action == StrategyAction.VERIFY


def test_strategy_is_deterministic(engine: StrategyEngine) -> None:
    """Same inputs twice → same StrategyAction."""
    kwargs = {
        "primary_intent": IntentLabel.PAYMENT,
        "conversation_state": "DEBT_DISCUSSION",
        "risk": _make_risk([]),
        "stress_level": StressLevel.MEDIUM,
        "identity_verified": True,
    }
    r1 = engine.select(**kwargs)  # type: ignore[arg-type]
    r2 = engine.select(**kwargs)  # type: ignore[arg-type]
    assert r1.action == r2.action
    assert r1.confidence == r2.confidence
    assert r1.rationale == r2.rationale


# ---------------------------------------------------------------------------
# Risk-driven overrides
# ---------------------------------------------------------------------------


def test_abuse_detected_forces_escalate(engine: StrategyEngine) -> None:
    """ABUSE_DETECTED → ESCALATE regardless of intent or state."""
    risk = _make_risk([RiskFlag.ABUSE_DETECTED])
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        risk=risk,
        identity_verified=True,
    )
    assert result.action == StrategyAction.ESCALATE
    assert result.confidence == 1.0


def test_legal_threat_forces_escalate(engine: StrategyEngine) -> None:
    risk = _make_risk([RiskFlag.LEGAL_THREAT])
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        risk=risk,
        identity_verified=True,
    )
    assert result.action == StrategyAction.ESCALATE


def test_consent_risk_forces_close(engine: StrategyEngine) -> None:
    risk = _make_risk([RiskFlag.CONSENT_RISK])
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        risk=risk,
        identity_verified=True,
    )
    assert result.action == StrategyAction.CLOSE


def test_unverified_identity_forces_verify(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="GREETING",
        risk=None,
        identity_verified=False,
    )
    assert result.action == StrategyAction.VERIFY


def test_dispute_claim_flag_forces_verify(engine: StrategyEngine) -> None:
    risk = _make_risk([RiskFlag.DISPUTE_CLAIM])
    result = engine.select(
        primary_intent=IntentLabel.OTHER,
        conversation_state="DEBT_DISCUSSION",
        risk=risk,
        identity_verified=True,
    )
    assert result.action == StrategyAction.VERIFY


def test_hardship_flag_forces_reassure(engine: StrategyEngine) -> None:
    risk = _make_risk([RiskFlag.HARDSHIP_INDICATOR])
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        risk=risk,
        identity_verified=True,
    )
    assert result.action == StrategyAction.REASSURE


def test_high_stress_forces_reassure(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        risk=None,
        stress_level=StressLevel.HIGH,
        identity_verified=True,
    )
    assert result.action == StrategyAction.REASSURE


def test_critical_stress_forces_reassure(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.OTHER,
        conversation_state="DEBT_DISCUSSION",
        risk=None,
        stress_level=StressLevel.CRITICAL,
        identity_verified=True,
    )
    assert result.action == StrategyAction.REASSURE


# ---------------------------------------------------------------------------
# Intent-driven actions
# ---------------------------------------------------------------------------


def test_promise_to_pay_intent_gives_confirm(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.PROMISE_TO_PAY,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert result.action == StrategyAction.CONFIRM


def test_disconnect_intent_gives_close(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.DISCONNECT,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert result.action == StrategyAction.CLOSE


def test_identity_verify_intent_gives_verify(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.IDENTITY_VERIFY,
        conversation_state="VERIFICATION",
        identity_verified=False,
    )
    assert result.action == StrategyAction.VERIFY


def test_silence_intent_gives_ask(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.SILENCE,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert result.action == StrategyAction.ASK


def test_negotiation_state_promotes_ask_to_negotiate(engine: StrategyEngine) -> None:
    """In NEGOTIATION state, PAYMENT intent ASK is promoted to NEGOTIATE."""
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="NEGOTIATION",
        identity_verified=True,
    )
    assert result.action == StrategyAction.NEGOTIATE


# ---------------------------------------------------------------------------
# State-driven defaults
# ---------------------------------------------------------------------------


def test_closing_state_gives_close(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.OTHER,
        conversation_state="CLOSING",
        identity_verified=True,
    )
    assert result.action == StrategyAction.CLOSE


def test_result_type(engine: StrategyEngine) -> None:
    result = engine.select(
        primary_intent=IntentLabel.PAYMENT,
        conversation_state="DEBT_DISCUSSION",
        identity_verified=True,
    )
    assert isinstance(result, StrategySelection)
    assert isinstance(result.action, StrategyAction)
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.rationale, str) and result.rationale


def test_abuse_overrides_hardship(engine: StrategyEngine) -> None:
    """ABUSE_DETECTED takes precedence over HARDSHIP_INDICATOR."""
    risk = _make_risk([RiskFlag.ABUSE_DETECTED, RiskFlag.HARDSHIP_INDICATOR])
    result = engine.select(
        primary_intent=IntentLabel.HARDSHIP,
        conversation_state="HARDSHIP_HANDLING",
        risk=risk,
        identity_verified=True,
    )
    assert result.action == StrategyAction.ESCALATE
