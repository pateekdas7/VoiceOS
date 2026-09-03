"""Hostility-driven graceful hangup trigger (V2 Ch10 · V4 Ch2 abuse policy).

The EmotionIntelligenceEngine already classifies each turn's sentiment and
stress level, but nothing was consuming those signals to end an out-of-
control call. In production collections, when a customer sustains
abuse/threats across several turns, keeping the agent on-line escalates
the encounter and creates a documented regulatory risk under RBI FPC's
"harassment" clause — the correct behavior is a polite, scripted
disengagement.

This module is the single-purpose tracker for that decision:

- ``HostilityHangupTracker.observe(call_id, emotion) -> HangupDecision``
- State is per-call (``call_id``-keyed) and reset by ``release(call_id)``
  when the call ends so a repeat caller doesn't inherit a warm buffer.
- The trigger is *consecutive* HOSTILE turns (not a total count), because
  one hostile turn followed by cooperation is not a hang-up scenario —
  it's exactly the case where the agent's de-escalation succeeded and
  the call should continue.

The default threshold (3) is chosen deliberately: two hostile turns can
be a heat-of-the-moment vent that Kavya's empathetic reply defuses; a
third sustained hostile turn is the compliance-relevant signal that
de-escalation has failed and the encounter is escalating. Callers can
override via ``consecutive_hostile_threshold`` for A/B tests.

Wiring: the ConversationEngine's turn handler is the intended caller —
after ``EmotionIntelligenceEngine.analyze()`` returns, feed the result
into ``HostilityHangupTracker.observe()``; if the decision says hang up,
speak the returned farewell via ``speak_scripted_text()`` and then
``end_call(outcome="hangup_hostility", sentiment="hostile")``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.libs.contracts.streaming import Sentiment

from .result import EmotionSignal

_log = logging.getLogger("voiceos.emotion.hangup")

# Chosen at 3 based on the analysis in the module docstring: 1 = normal
# venting, 2 = de-escalation window, 3+ = sustained hostility → disengage.
# The number lives here (not in a config file) because it's a
# compliance-relevant default that should require a code review + PR to
# change, not an env-var flip.
_DEFAULT_HOSTILE_THRESHOLD = 3

# Kavya's scripted disengagement phrase — deliberately short, apologetic,
# and non-confrontational so the recording never suggests the agent
# escalated. Hinglish (Delhi register) matches Kavya's baseline persona;
# a Marathi/Tamil/etc. pack can be added when those tenants go live.
_HANGUP_PHRASE_HINGLISH = (
    "Sir/Madam, main aapki baat samajh sakti hoon lekin ab main call end "
    "kar rahi hoon. Aap humein " "1800-XXX-XXXX par call kar sakte hain jab "
    "convenient ho. Dhanyawaad."
)


@dataclass(frozen=True)
class HangupDecision:
    """Result of a single ``observe()`` call.

    Immutable so the caller can safely log/publish the decision without
    worrying about later mutations.
    """

    should_hangup: bool
    farewell_text: str = ""
    reason: str = ""
    consecutive_hostile_turns: int = 0


@dataclass
class _CallState:
    consecutive_hostile: int = 0
    already_triggered: bool = False


class HostilityHangupTracker:
    """Per-call tracker that decides when sustained hostility warrants a
    graceful hangup.

    Not thread-safe; VoiceOS runs one async event loop per process and
    each call is handled on that loop, so all ``observe()`` calls for a
    given ``call_id`` happen on a single thread. If multi-threaded usage
    is ever required, guard ``_state`` with a lock.

    Args:
        consecutive_hostile_threshold: Number of consecutive HOSTILE turns
            after which ``should_hangup`` will be True. Default 3 — see
            module docstring for the reasoning.
        farewell_text: Override for Kavya's scripted disengagement line.
            Default is a Hinglish phrase suitable for Delhi NCR tenants.
    """

    def __init__(
        self,
        *,
        consecutive_hostile_threshold: int = _DEFAULT_HOSTILE_THRESHOLD,
        farewell_text: str = _HANGUP_PHRASE_HINGLISH,
    ) -> None:
        if consecutive_hostile_threshold < 1:
            raise ValueError(
                "consecutive_hostile_threshold must be >= 1 "
                f"(got {consecutive_hostile_threshold})"
            )
        self._threshold = consecutive_hostile_threshold
        self._farewell = farewell_text
        self._state: dict[str, _CallState] = {}

    def observe(self, call_id: str, emotion: EmotionSignal) -> HangupDecision:
        """Feed a per-turn emotion signal and get back a hangup decision.

        A HOSTILE sentiment increments the consecutive counter; any other
        sentiment resets it to zero. Once the threshold is crossed, the
        first ``observe()`` call at/above the threshold returns
        ``should_hangup=True`` and marks the call as "already triggered"
        so a second immediately-following observe (which the caller might
        make while it's still emitting the farewell) does not re-trigger.

        Returns:
            HangupDecision with ``should_hangup=True`` exactly once per
            call — subsequent calls after the trigger return
            ``should_hangup=False`` but preserve ``consecutive_hostile_turns``
            so log analysis can see the count kept climbing.
        """
        state = self._state.setdefault(call_id, _CallState())

        if emotion.sentiment == Sentiment.HOSTILE:
            state.consecutive_hostile += 1
        else:
            state.consecutive_hostile = 0

        if (
            state.consecutive_hostile >= self._threshold
            and not state.already_triggered
        ):
            state.already_triggered = True
            _log.warning(
                "graceful hangup triggered call_id=%s consecutive_hostile=%d threshold=%d",
                call_id, state.consecutive_hostile, self._threshold,
            )
            return HangupDecision(
                should_hangup=True,
                farewell_text=self._farewell,
                reason="sustained_hostility",
                consecutive_hostile_turns=state.consecutive_hostile,
            )

        return HangupDecision(
            should_hangup=False,
            consecutive_hostile_turns=state.consecutive_hostile,
        )

    def release(self, call_id: str) -> None:
        """Drop tracked state for ``call_id`` when the call ends.

        Idempotent — a release for an unknown call_id is a no-op. Callers
        should always invoke this on ``end_call()`` regardless of the
        hangup reason so a repeat caller doesn't inherit a warm buffer
        of prior-call hostility state.
        """
        self._state.pop(call_id, None)

    def consecutive_hostile(self, call_id: str) -> int:
        """Expose the current running count for observability/debug."""
        state = self._state.get(call_id)
        return 0 if state is None else state.consecutive_hostile
