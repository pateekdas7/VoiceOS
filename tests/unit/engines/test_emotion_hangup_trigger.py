"""Unit tests for the hostility→hangup trigger (NEEDS-WORK #12).

The trigger is the tripwire that converts EmotionIntelligenceEngine's
per-turn sentiment signal into a compliance-relevant call-end decision.
The default (``3 consecutive HOSTILE turns``) is deliberately picked and
tested here — any change to that threshold must go through code review
because it directly determines when Kavya walks away from an escalating
encounter (RBI FPC harassment clause).
"""

from __future__ import annotations

import pytest

from src.engines.emotion.hangup_trigger import (
    HangupDecision,
    HostilityHangupTracker,
)
from src.engines.emotion.result import EmotionSignal
from src.libs.contracts.streaming import Sentiment, StressLevel


def _signal(sentiment: Sentiment) -> EmotionSignal:
    """Build a minimally-valid EmotionSignal for a given sentiment.
    The tracker only reads .sentiment; other fields are set to plausible
    defaults so the contract's own validation doesn't reject the object."""
    return EmotionSignal(
        sentiment=sentiment,
        arousal=0.9 if sentiment == Sentiment.HOSTILE else 0.3,
        valence=-0.9 if sentiment == Sentiment.HOSTILE else 0.0,
        stress_level=StressLevel.CRITICAL if sentiment == Sentiment.HOSTILE else StressLevel.LOW,
        dominant_emotion=sentiment.value,
    )


CALL = "call-test-1"


class TestHostilityHangupTracker:
    def test_single_hostile_turn_does_not_trigger(self) -> None:
        """One hostile turn is normal venting — de-escalation must have
        a chance to run before we hang up."""
        tracker = HostilityHangupTracker()
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert decision.should_hangup is False
        assert decision.consecutive_hostile_turns == 1

    def test_two_hostile_turns_do_not_trigger_at_default_threshold(self) -> None:
        """Threshold defaults to 3; two hostile turns are the empathy window."""
        tracker = HostilityHangupTracker()
        tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert decision.should_hangup is False
        assert decision.consecutive_hostile_turns == 2

    def test_three_consecutive_hostile_triggers_hangup(self) -> None:
        tracker = HostilityHangupTracker()
        tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert decision.should_hangup is True
        assert decision.consecutive_hostile_turns == 3
        assert decision.reason == "sustained_hostility"
        assert decision.farewell_text  # non-empty scripted farewell

    def test_neutral_turn_resets_counter(self) -> None:
        """The whole point of the *consecutive* rule: cooperation between
        hostile turns is the de-escalation success signal — the tracker
        must forgive it, not carry a grudge."""
        tracker = HostilityHangupTracker()
        tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        tracker.observe(CALL, _signal(Sentiment.NEUTRAL))
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert decision.should_hangup is False
        assert decision.consecutive_hostile_turns == 1

    def test_positive_turn_also_resets(self) -> None:
        tracker = HostilityHangupTracker()
        tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        tracker.observe(CALL, _signal(Sentiment.POSITIVE))
        assert tracker.consecutive_hostile(CALL) == 0

    def test_negative_but_not_hostile_resets(self) -> None:
        """NEGATIVE is not HOSTILE — a frustrated but non-abusive customer
        does not accumulate the abuse counter, because the compliance
        signal is specifically about verbal aggression, not disagreement."""
        tracker = HostilityHangupTracker()
        tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        tracker.observe(CALL, _signal(Sentiment.NEGATIVE))
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert decision.should_hangup is False
        assert decision.consecutive_hostile_turns == 1

    def test_trigger_fires_exactly_once_per_call(self) -> None:
        """After the trigger fires, immediately-subsequent hostile turns
        must not re-trigger — the orchestrator is already tearing the
        call down and a second hangup event would be spurious."""
        tracker = HostilityHangupTracker()
        for _ in range(3):
            tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        second = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert second.should_hangup is False
        assert second.consecutive_hostile_turns == 4

    def test_per_call_isolation(self) -> None:
        """Two calls in flight must not share state. If they did, a caller
        who never got hostile could get hung up on because another caller
        did — a critical safety failure."""
        tracker = HostilityHangupTracker()
        for _ in range(3):
            tracker.observe("call-A", _signal(Sentiment.HOSTILE))
        decision = tracker.observe("call-B", _signal(Sentiment.HOSTILE))
        assert decision.should_hangup is False
        assert decision.consecutive_hostile_turns == 1

    def test_release_forgets_state(self) -> None:
        """A repeat caller (same customer, new call, same call_id would
        never be reused but defense in depth) must not inherit prior
        state — release() is what makes end_call() safe."""
        tracker = HostilityHangupTracker()
        for _ in range(3):
            tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        tracker.release(CALL)
        assert tracker.consecutive_hostile(CALL) == 0
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert decision.should_hangup is False

    def test_release_of_unknown_call_is_noop(self) -> None:
        tracker = HostilityHangupTracker()
        tracker.release("never-tracked")  # must not raise

    def test_custom_threshold_of_1_triggers_immediately(self) -> None:
        """Some tenants (e.g. TRAI complaint-triggered runs) may run in
        zero-tolerance mode — threshold=1 must fire on the very first
        hostile turn."""
        tracker = HostilityHangupTracker(consecutive_hostile_threshold=1)
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert decision.should_hangup is True
        assert decision.consecutive_hostile_turns == 1

    def test_custom_threshold_of_5(self) -> None:
        tracker = HostilityHangupTracker(consecutive_hostile_threshold=5)
        for _ in range(4):
            d = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
            assert d.should_hangup is False
        d = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert d.should_hangup is True

    def test_zero_threshold_rejected(self) -> None:
        """A threshold of 0 would fire on the very first turn regardless
        of sentiment — nonsensical, so rejected at construction."""
        with pytest.raises(ValueError):
            HostilityHangupTracker(consecutive_hostile_threshold=0)

    def test_negative_threshold_rejected(self) -> None:
        with pytest.raises(ValueError):
            HostilityHangupTracker(consecutive_hostile_threshold=-1)

    def test_custom_farewell_used_in_decision(self) -> None:
        tracker = HostilityHangupTracker(farewell_text="Kal baat karte hain.")
        for _ in range(2):
            tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert decision.farewell_text == "Kal baat karte hain."

    def test_default_farewell_is_apologetic_and_scripted(self) -> None:
        """Regression: the default farewell must remain a polite scripted
        line. A refactor that lets an LLM improvise the farewell would
        create a documented regulatory exposure — everything Kavya says
        on a hostile call is going to be quoted in a complaint."""
        tracker = HostilityHangupTracker()
        for _ in range(2):
            tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        decision = tracker.observe(CALL, _signal(Sentiment.HOSTILE))
        assert "call end" in decision.farewell_text.lower()
        assert "dhanyawaad" in decision.farewell_text.lower()

    def test_decision_is_frozen(self) -> None:
        """HangupDecision must be immutable so callers can safely publish
        it to the event bus without worrying about later mutation."""
        d = HangupDecision(should_hangup=True, farewell_text="x", reason="y")
        with pytest.raises((AttributeError, Exception)):
            d.should_hangup = False  # type: ignore[misc]

    def test_consecutive_hostile_query_before_any_observe(self) -> None:
        tracker = HostilityHangupTracker()
        assert tracker.consecutive_hostile("never-observed") == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
