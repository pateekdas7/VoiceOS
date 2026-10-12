"""Unit tests for ConversationSessionState (Sprint-015 Recoverable wiring)."""

from __future__ import annotations

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import CallId, TenantId
from src.services.conversation_engine.session_state import ConversationSessionState


class TestConversationSessionState:
    def test_initial_state(self) -> None:
        session = ConversationSessionState(CallId("call-1"))

        assert session.call_id == "call-1"
        assert session.turn_count == 0
        assert session.intent_history == []

    def test_record_turn_increments_count_and_tracks_intent(self) -> None:
        session = ConversationSessionState(CallId("call-1"))

        session.record_turn(intent_label="PAYMENT", event_offset="env-1")
        session.record_turn(intent_label="DISPUTE", event_offset="env-2")
        session.record_turn(intent_label=None, event_offset="env-3")

        assert session.turn_count == 3
        assert session.intent_history == ["PAYMENT", "DISPUTE"]

    def test_intent_history_bounded_to_max_window(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        for i in range(15):
            session.record_turn(intent_label=f"INTENT_{i}", event_offset=str(i))

        assert len(session.intent_history) == 10
        assert session.intent_history[0] == "INTENT_5"
        assert session.intent_history[-1] == "INTENT_14"

    def test_snapshot_roundtrip(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        session.record_turn(intent_label="PAYMENT", event_offset="env-1")
        session.record_turn(intent_label="DISPUTE", event_offset="env-2")

        snap = session.snapshot()
        assert snap.version == 1
        assert snap.state["turn_count"] == 2
        assert snap.state["intent_history"] == ["PAYMENT", "DISPUTE"]
        assert snap.last_event_offset == "env-2"

        restored = ConversationSessionState(CallId("call-1"))
        restored.restore(snap)

        assert restored.turn_count == 2
        assert restored.intent_history == ["PAYMENT", "DISPUTE"]

    def test_snapshot_roundtrip_includes_dialogue_response_state(self) -> None:
        """Path-A Phase 6: the scripted-response engine's fields survive a
        snapshot/restore cycle too, not just turn_count/intent_history."""
        session = ConversationSessionState(CallId("call-1"))
        session.set_dialogue_state_name("CONVERSATION")
        session.set_identity_verified(True)
        session.set_last_ask("confirm_date")
        session.bump_hallucination_hits()
        session.update_commitment(amount_minor=400_000, months=13, date_text="30 August", cadence="monthly")
        session.record_assistant_reply("Perfect sir, kab tak clear ho jaayega?")
        session.set_last_empathy_state("HARDSHIP_FINANCIAL")

        snap = session.snapshot()
        restored = ConversationSessionState(CallId("call-1"))
        restored.restore(snap)

        assert restored.dialogue_state_name == "CONVERSATION"
        assert restored.identity_verified is True
        assert restored.last_ask == "confirm_date"
        assert restored.hallucination_hits == 1
        assert restored.commitment == {
            "amount_minor": 400_000,
            "months": 13,
            "date": "30 August",
            "cadence": "monthly",
        }
        assert restored.assistant_replies == ["Perfect sir, kab tak clear ho jaayega?"]
        assert restored.last_empathy_state == "HARDSHIP_FINANCIAL"

    def test_snapshot_roundtrip_includes_consecutive_else_count(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        session.bump_consecutive_else_count()
        session.bump_consecutive_else_count()

        snap = session.snapshot()
        restored = ConversationSessionState(CallId("call-1"))
        restored.restore(snap)

        assert restored.consecutive_else_count == 2

    def test_bump_consecutive_else_count_increments_and_returns_new_value(self) -> None:
        session = ConversationSessionState(CallId("call-1"))

        assert session.bump_consecutive_else_count() == 1
        assert session.bump_consecutive_else_count() == 2
        assert session.consecutive_else_count == 2

    def test_reset_consecutive_else_count_zeroes_it(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        session.bump_consecutive_else_count()
        session.bump_consecutive_else_count()

        session.reset_consecutive_else_count()

        assert session.consecutive_else_count == 0

    def test_update_commitment_only_overwrites_supplied_fields(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        session.update_commitment(amount_minor=400_000, cadence="monthly")
        session.update_commitment(months=13)  # a later turn resolves months, doesn't touch amount

        assert session.commitment == {
            "amount_minor": 400_000,
            "months": 13,
            "date": None,
            "cadence": "monthly",
        }

    def test_assistant_reply_history_bounded(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        for i in range(25):
            session.record_assistant_reply(f"reply-{i}")

        assert len(session.assistant_replies) == 20
        assert session.assistant_replies[0] == "reply-5"
        assert session.assistant_replies[-1] == "reply-24"

    def test_apply_event_advances_turn_count_for_decision_made(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        event = EventEnvelope(
            event_type="decision.made",
            tenant_id=TenantId("tenant-a"),
            correlation_id="corr-1",
            trace_id="trace-1",
            payload={"call_id": "call-1"},
        )

        session.apply_event(event)

        assert session.turn_count == 1

    def test_apply_event_ignores_unrelated_event_types(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        event = EventEnvelope(
            event_type="call.ended",
            tenant_id=TenantId("tenant-a"),
            correlation_id="corr-1",
            trace_id="trace-1",
            payload={"call_id": "call-1"},
        )

        session.apply_event(event)

        assert session.turn_count == 0

    def test_concession_round_starts_at_zero(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        assert session.concession_round == 0

    def test_increment_concession_round_returns_new_value(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        assert session.increment_concession_round() == 1
        assert session.increment_concession_round() == 2
        assert session.concession_round == 2

    def test_concession_round_survives_snapshot_restore(self) -> None:
        session = ConversationSessionState(CallId("call-1"))
        session.increment_concession_round()
        session.increment_concession_round()

        snap = session.snapshot()
        restored = ConversationSessionState(CallId("call-1"))
        restored.restore(snap)

        assert restored.concession_round == 2

    def test_concession_round_defaults_to_zero_in_old_snapshots(self) -> None:
        """Snapshots taken before concession_round was added must restore cleanly."""
        session = ConversationSessionState(CallId("call-1"))
        snap = session.snapshot()
        # Simulate an old snapshot that lacks the key (StateSnapshot is frozen,
        # so rebuild via model_copy with updated state dict).
        old_state = {k: v for k, v in snap.state.items() if k != "concession_round"}
        old_snap = snap.model_copy(update={"state": old_state})

        restored = ConversationSessionState(CallId("call-1"))
        restored.restore(old_snap)

        assert restored.concession_round == 0
