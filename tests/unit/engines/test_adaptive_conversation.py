"""Unit tests for AdaptiveConversationEngine, ConversationLoopDetector, SilenceRecoveryPolicy."""

from __future__ import annotations

import pytest

from src.engines.adaptive_conversation import (
    AdaptiveConversationEngine,
    ConversationLoopDetector,
    SilenceRecoveryPolicy,
)
from src.libs.contracts.response_plan import IntentLabel

# ---------------------------------------------------------------------------
# ConversationLoopDetector
# ---------------------------------------------------------------------------


class TestConversationLoopDetector:
    def test_no_loop_when_history_empty(self) -> None:
        detector = ConversationLoopDetector()
        detected, count = detector.detect([])
        assert not detected
        assert count == 0

    def test_no_loop_below_threshold(self) -> None:
        detector = ConversationLoopDetector(window=5, threshold=3)
        history = [IntentLabel.PAYMENT, IntentLabel.OTHER, IntentLabel.PAYMENT]
        detected, count = detector.detect(history)
        assert not detected
        assert count == 2

    def test_adaptive_conversation_loop_detection(self) -> None:  # required named test
        """test_adaptive_conversation_loop_detection: 3 identical intents triggers loop."""
        detector = ConversationLoopDetector(window=5, threshold=3)
        history = [IntentLabel.PAYMENT, IntentLabel.PAYMENT, IntentLabel.PAYMENT]
        detected, count = detector.detect(history)
        assert detected
        assert count == 3

    def test_only_recent_window_examined(self) -> None:
        detector = ConversationLoopDetector(window=3, threshold=3)
        # First 3 are PAYMENT (loop) but outside window; recent 3 are OTHER.
        history = [
            IntentLabel.PAYMENT,
            IntentLabel.PAYMENT,
            IntentLabel.PAYMENT,
            IntentLabel.OTHER,
            IntentLabel.OTHER,
            IntentLabel.OTHER,
        ]
        detected, _count = detector.detect(history)
        # Window=3 → only last 3 (all OTHER): count=3, threshold=3 → loop detected
        assert detected

    def test_invalid_window_raises(self) -> None:
        with pytest.raises(ValueError):
            ConversationLoopDetector(window=0)

    def test_invalid_threshold_raises(self) -> None:
        with pytest.raises(ValueError):
            ConversationLoopDetector(threshold=1)


# ---------------------------------------------------------------------------
# SilenceRecoveryPolicy
# ---------------------------------------------------------------------------


class TestSilenceRecoveryPolicy:
    def test_adaptive_conversation_silence_recovery(self) -> None:  # required named test
        """test_adaptive_conversation_silence_recovery: silence < 800ms returns None."""
        policy = SilenceRecoveryPolicy()
        assert policy.get_recovery_text(500) is None

    def test_light_recovery_text_returned(self) -> None:
        policy = SilenceRecoveryPolicy()
        text = policy.get_recovery_text(1000)
        assert text is not None
        assert len(text) > 0

    def test_strong_recovery_text_returned(self) -> None:
        policy = SilenceRecoveryPolicy()
        text = policy.get_recovery_text(5000)
        assert text is not None

    def test_escalation_at_10s(self) -> None:
        policy = SilenceRecoveryPolicy()
        assert policy.requires_escalation(10_000)
        assert not policy.requires_escalation(9_999)


# ---------------------------------------------------------------------------
# AdaptiveConversationEngine
# ---------------------------------------------------------------------------


class TestAdaptiveConversationEngine:
    def test_continue_on_empty_history(self) -> None:
        engine = AdaptiveConversationEngine()
        signal = engine.process(intent_history=[], silence_duration_ms=0)
        assert signal.recommended_action == "continue"
        assert not signal.loop_detected

    def test_clarify_on_short_silence(self) -> None:
        engine = AdaptiveConversationEngine()
        signal = engine.process(intent_history=[], silence_duration_ms=1500)
        assert signal.recommended_action == "clarify"
        assert signal.silence_recovery_text is not None

    def test_escalate_on_long_silence(self) -> None:
        engine = AdaptiveConversationEngine()
        signal = engine.process(intent_history=[], silence_duration_ms=15_000)
        assert signal.escalation_recommended
        assert signal.recommended_action == "escalate"

    def test_escalate_on_persistent_loop(self) -> None:
        engine = AdaptiveConversationEngine()
        history = [IntentLabel.PAYMENT] * 4
        signal = engine.process(intent_history=history, silence_duration_ms=0)
        # loop_count >= 4 triggers escalation
        assert signal.escalation_recommended

    def test_no_escalation_when_no_loop_no_silence(self) -> None:
        engine = AdaptiveConversationEngine()
        history = [IntentLabel.PAYMENT, IntentLabel.OTHER, IntentLabel.DISPUTE]
        signal = engine.process(intent_history=history, silence_duration_ms=0)
        assert not signal.escalation_recommended
        assert signal.recommended_action == "continue"
