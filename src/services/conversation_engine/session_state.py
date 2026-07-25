"""ConversationSessionState — the per-call Recoverable state ConversationEngine tracks.

Sprint-012's ConversationEngine takes ``intent_history`` as a caller-supplied
argument on every ``handle_turn()`` call rather than owning any internal
per-call state — there was nothing to snapshot. Sprint-015 made it a real
``Recoverable``: turn count and recent intent history, enough for
``CPURestartStrategy`` to demonstrably restore a call to its pre-crash state
via snapshot + event-tail replay.

Path-A Phase 6 extends this with the scripted-response engine's per-call
state (``src/engines/dialogue_response/``, ported from
``evaluation/founder-validation/conv_server.py``'s proven session dict):
the AWAIT_IDENTITY/CONVERSATION/CLOSE bucket-router state, a commitment
ledger, and reply-dedup history. All fields here are plain str/int/bool/
dict — this file is in ``src/services/`` and must never import a concrete
``src/engines/`` type (check_boundaries.py Rule 1); the dialogue_response
engine reads/writes these fields through the plain accessors below rather
than this class depending on the engine's own types.

Unlike conv_server.py's session dict, the commitment ledger here is never
populated by this file's own regex parsing — ``update_commitment()`` is
called by the caller (ConversationEngine) with values already resolved by
EntityExtractor/NegotiationEngine (the real, already-wired engines), so
there is exactly one place date/amount parsing happens, not two.

Architecture: V3 Ch6 (Recoverable protocol); V2 Ch13 (dialogue state).
"""

from __future__ import annotations

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import CallId
from src.libs.state.recoverable import StateSnapshot

_MAX_INTENT_HISTORY = 10
"""Matches AdaptiveConversationEngine's loop-detection window (Sprint-012)."""

_MAX_REPLY_HISTORY = 20
"""Bounded reply-dedup window — mirrors conv_server.py's unbounded
assistant_replies list, capped here per RI-3 (bounded buffers everywhere)."""


class ConversationSessionState:
    """Recoverable per-call conversation state."""

    def __init__(self, call_id: CallId) -> None:
        self._call_id = call_id
        self._turn_count = 0
        self._intent_history: list[str] = []
        self._version = 0
        self._last_event_offset = "-"

        # Path-A Phase 6: scripted-response engine state (dialogue_response).
        self._dialogue_state_name: str = "AWAIT_IDENTITY"
        self._identity_verified: bool = False
        self._identity_reprompted: bool = False
        self._last_ask: str = ""
        self._hallucination_hits: int = 0
        self._farewell_requested: bool = False
        self._commitment: dict[str, str | int | None] = {
            "amount_minor": None,
            "months": None,
            "date": None,
            "cadence": None,
        }
        self._assistant_replies: list[str] = []
        self._last_empathy_state: str = ""

        # Path-A Call-002 readiness: real LLM-fallback trigger condition.
        # DialogueResponseEngine bumps this when the scripted golden path
        # can't classify the customer's utterance (Bucket.ELSE); resets it
        # on any successful classification. ConversationEngine routes the
        # turn to the real LLM/TTS streaming path once this crosses the
        # threshold, instead of the deterministic "anchor" re-ask —
        # matching the approved consolidation plan's "LLM as fallback"
        # intent, which the initial Phase 6g wiring never actually
        # implemented as a live condition (see CHANGELOG.md's Call-002
        # readiness entry).
        self._consecutive_else_count: int = 0

    @property
    def call_id(self) -> CallId:
        return self._call_id

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def intent_history(self) -> list[str]:
        return list(self._intent_history)

    def record_turn(self, intent_label: str | None, event_offset: str) -> None:
        """Advance state after one processed turn.

        Args:
            intent_label: The turn's classified intent label, if any.
            event_offset: The EventBus entry ID this turn's DecisionEnvelope
                was published under — becomes the snapshot's resume point.
        """
        self._turn_count += 1
        if intent_label is not None:
            self._intent_history = [*self._intent_history, intent_label][-_MAX_INTENT_HISTORY:]
        self._last_event_offset = event_offset

    # ------------------------------------------------------------------
    # Path-A Phase 6: scripted-response engine state accessors
    # ------------------------------------------------------------------

    @property
    def dialogue_state_name(self) -> str:
        """One of AWAIT_IDENTITY / CONVERSATION / CLOSE."""
        return self._dialogue_state_name

    def set_dialogue_state_name(self, value: str) -> None:
        self._dialogue_state_name = value

    @property
    def identity_verified(self) -> bool:
        return self._identity_verified

    def set_identity_verified(self, value: bool) -> None:
        self._identity_verified = value

    @property
    def identity_reprompted(self) -> bool:
        return self._identity_reprompted

    def set_identity_reprompted(self, value: bool) -> None:
        self._identity_reprompted = value

    @property
    def last_ask(self) -> str:
        """What the agent last asked for: when_pay/confirm_date/confirm_plan/part_payment."""
        return self._last_ask

    def set_last_ask(self, value: str) -> None:
        self._last_ask = value

    @property
    def hallucination_hits(self) -> int:
        return self._hallucination_hits

    def bump_hallucination_hits(self) -> int:
        self._hallucination_hits += 1
        return self._hallucination_hits

    @property
    def farewell_requested(self) -> bool:
        return self._farewell_requested

    def set_farewell_requested(self, value: bool) -> None:
        self._farewell_requested = value

    @property
    def commitment(self) -> dict[str, str | int | None]:
        """The accumulated commitment ledger: amount_minor/months/date/cadence.

        Never written by this class's own parsing — see update_commitment().
        """
        return dict(self._commitment)

    def update_commitment(
        self,
        amount_minor: int | None = None,
        months: int | None = None,
        date_text: str | None = None,
        cadence: str | None = None,
    ) -> None:
        """Merge newly-resolved commitment facts (from EntityExtractor/
        NegotiationEngine output) into the ledger. Only non-None arguments
        overwrite existing values — a turn that doesn't mention an amount
        doesn't erase a previously-confirmed one."""
        if amount_minor is not None:
            self._commitment["amount_minor"] = amount_minor
        if months is not None:
            self._commitment["months"] = months
        if date_text is not None:
            self._commitment["date"] = date_text
        if cadence is not None:
            self._commitment["cadence"] = cadence

    @property
    def assistant_replies(self) -> list[str]:
        return list(self._assistant_replies)

    def record_assistant_reply(self, reply: str) -> None:
        self._assistant_replies = [*self._assistant_replies, reply][-_MAX_REPLY_HISTORY:]

    @property
    def last_empathy_state(self) -> str:
        return self._last_empathy_state

    def set_last_empathy_state(self, value: str) -> None:
        self._last_empathy_state = value

    @property
    def consecutive_else_count(self) -> int:
        return self._consecutive_else_count

    def bump_consecutive_else_count(self) -> int:
        self._consecutive_else_count += 1
        return self._consecutive_else_count

    def reset_consecutive_else_count(self) -> None:
        self._consecutive_else_count = 0

    # ------------------------------------------------------------------
    # Recoverable protocol
    # ------------------------------------------------------------------

    def snapshot(self) -> StateSnapshot:
        self._version += 1
        return StateSnapshot(
            call_id=self._call_id,
            version=self._version,
            state={
                "turn_count": self._turn_count,
                "intent_history": self._intent_history,
                "dialogue_state_name": self._dialogue_state_name,
                "identity_verified": self._identity_verified,
                "identity_reprompted": self._identity_reprompted,
                "last_ask": self._last_ask,
                "hallucination_hits": self._hallucination_hits,
                "farewell_requested": self._farewell_requested,
                "commitment": self._commitment,
                "assistant_replies": self._assistant_replies,
                "last_empathy_state": self._last_empathy_state,
                "consecutive_else_count": self._consecutive_else_count,
            },
            last_event_offset=self._last_event_offset,
        )

    def restore(self, snapshot: StateSnapshot) -> None:
        self._turn_count = int(snapshot.state.get("turn_count", 0))
        self._intent_history = list(snapshot.state.get("intent_history", []))
        self._dialogue_state_name = str(snapshot.state.get("dialogue_state_name", "AWAIT_IDENTITY"))
        self._identity_verified = bool(snapshot.state.get("identity_verified", False))
        self._identity_reprompted = bool(snapshot.state.get("identity_reprompted", False))
        self._last_ask = str(snapshot.state.get("last_ask", ""))
        self._hallucination_hits = int(snapshot.state.get("hallucination_hits", 0))
        self._farewell_requested = bool(snapshot.state.get("farewell_requested", False))
        self._commitment = dict(
            snapshot.state.get(
                "commitment", {"amount_minor": None, "months": None, "date": None, "cadence": None}
            )
        )
        self._assistant_replies = list(snapshot.state.get("assistant_replies", []))
        self._last_empathy_state = str(snapshot.state.get("last_empathy_state", ""))
        self._consecutive_else_count = int(snapshot.state.get("consecutive_else_count", 0))
        self._version = snapshot.version
        self._last_event_offset = snapshot.last_event_offset

    def apply_event(self, event: EventEnvelope) -> None:
        """Advance state by one replayed ``decision.made`` event."""
        if event.event_type == "decision.made":
            self._turn_count += 1
