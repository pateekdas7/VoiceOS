"""Unit tests for DialogueManager — state machine and TurnInput assembly."""

from __future__ import annotations

from collections.abc import AsyncIterator

from src.libs.contracts.streaming import WordHypothesis
from src.services.dialogue_manager import DialogueManager, DialogueState


def _make_word(word: str, is_final: bool = True, start_ms: int = 0, end_ms: int = 100) -> WordHypothesis:
    return WordHypothesis(word=word, confidence=0.95, start_ms=start_ms, end_ms=end_ms, is_final=is_final)


async def _words_stream(*words: WordHypothesis) -> AsyncIterator[WordHypothesis]:
    for w in words:
        yield w


class TestDialogueManager:
    def test_initial_state_is_idle(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        assert dm.state == DialogueState.IDLE

    def test_turn_index_starts_at_zero(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        assert dm.turn_index == 0

    async def test_ingest_stream_builds_turn_input(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        stream = _words_stream(
            _make_word("aapka", start_ms=0, end_ms=200),
            _make_word("balance", start_ms=210, end_ms=450),
        )
        turn = await dm.ingest_stream(stream)
        assert turn.call_id == "call-1"
        assert turn.tenant_id == "tenant-1"
        assert turn.transcript == "aapka balance"
        assert len(turn.segments) == 2
        assert turn.turn_id != ""
        assert turn.turn_index == 0

    async def test_ingest_stream_state_transitions(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        stream = _words_stream(_make_word("hello"))
        await dm.ingest_stream(stream)
        # After ingest_stream returns, state should be PROCESSING.
        assert dm.state == DialogueState.PROCESSING

    def test_notify_agent_speaking_changes_state(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        dm.notify_agent_speaking()
        assert dm.state == DialogueState.AGENT_SPEAKING

    def test_notify_agent_done_returns_to_idle(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        dm.notify_agent_speaking()
        dm.notify_agent_done()
        assert dm.state == DialogueState.IDLE

    async def test_turn_index_increments_per_ingest(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        for i in range(3):
            stream = _words_stream(_make_word("word"))
            turn = await dm.ingest_stream(stream)
            assert turn.turn_index == i

        assert dm.turn_index == 3

    async def test_partial_hypotheses_excluded_from_transcript(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        stream = _words_stream(
            _make_word("partial", is_final=False),
            _make_word("final", is_final=True),
        )
        turn = await dm.ingest_stream(stream)
        assert turn.transcript == "final"
        assert len(turn.segments) == 1

    async def test_empty_stream_yields_empty_transcript(self) -> None:
        dm = DialogueManager(call_id="call-1", tenant_id="tenant-1")
        stream = _words_stream()
        turn = await dm.ingest_stream(stream)
        assert turn.transcript == ""
        assert len(turn.segments) == 0

    async def test_correlation_and_trace_ids_propagated(self) -> None:
        dm = DialogueManager(
            call_id="call-1",
            tenant_id="tenant-1",
            correlation_id="corr-xyz",
            trace_id="trace-abc",
        )
        stream = _words_stream(_make_word("test"))
        turn = await dm.ingest_stream(stream)
        assert turn.correlation_id == "corr-xyz"
        assert turn.trace_id == "trace-abc"
