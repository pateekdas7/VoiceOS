"""Unit tests: ConversationEngine's Sprint-015 IdempotencyGuard/Snapshot wiring.

Reuses the walking-skeleton e2e fixture builder (real engines, mock AI) since
ConversationEngine has no lightweight standalone constructor — the CIL
pipeline is not itself under test here, only the reliability wiring added on
top of it.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import cast

from src.libs.concurrency.worker_pool import Priority, WorkerPool
from src.libs.contracts.primitives import CallId, TenantId
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.observability.logger import StructuredLogger
from src.libs.observability.tracer import OTelTracer
from src.libs.repositories.idempotency import IdempotencyRepository
from src.libs.state.snapshot import Snapshot
from src.services.playback.scheduler import PlaybackScheduler
from tests.e2e.test_walking_skeleton import _build_engine, _make_turn
from tests.fixtures.fake_pg import FakeConnection, FakeCursor


class TestIdempotencyGuardWiring:
    async def test_retried_turn_does_not_double_publish(self) -> None:
        """Same turn handled twice through IdempotencyGuard -> publish happens once."""
        published: list[str] = []

        class _CountingEventBus:
            async def publish(self, envelope: object) -> str:
                published.append("published")
                return "1720051234567-0"

        cursor = FakeCursor(
            fetchall_results=[[], [(json.dumps("1720051234567-0"),)]],
            fetchone_results=[("claimed",)],
        )
        repo = IdempotencyRepository(FakeConnection(cursor))
        guard = IdempotencyGuard(repo)

        engine = _build_engine(event_bus=_CountingEventBus(), idempotency_guard=guard)
        playback = PlaybackScheduler()
        turn = _make_turn(call_id="call-idem-1")

        await engine.handle_turn(turn=turn, playback=playback)
        await engine.handle_turn(turn=turn, playback=playback)  # retry: same turn_id

        assert published == ["published"]


class TestSnapshotWiring:
    async def test_session_state_tracked_and_snapshotted_every_n_turns(self) -> None:
        tenant_id = TenantId("tenant-snap-1")
        call_id = "call-snap-1"
        cursor = FakeCursor()
        snapshot_store = Snapshot(FakeConnection(cursor))

        engine = _build_engine(snapshot_store=snapshot_store)
        engine._snapshot_every_n_turns = 2  # test wiring, not public API
        playback = PlaybackScheduler()

        for i in range(2):
            turn = _make_turn(turn_index=i, call_id=call_id)
            turn = turn.model_copy(update={"tenant_id": tenant_id})
            await engine.handle_turn(turn=turn, playback=playback)

        session = engine.get_session_state(call_id)
        assert session is not None
        assert session.turn_count == 2

        insert_statements = [sql for sql, _params in cursor.executed if "INSERT INTO snapshots" in sql]
        assert len(insert_statements) == 1  # snapshot taken exactly once (on turn 2)

    async def test_get_session_state_returns_none_for_unknown_call(self) -> None:
        engine = _build_engine()

        assert engine.get_session_state(f"call-{uuid.uuid4()}") is None


class _FakeScore:
    coherence = 1.0
    policy_compliance = 1.0
    empathy = 1.0
    factual_accuracy = 1.0


class _RecordingEvaluator:
    def score(self, turn: object, llm_output: str, response_plan: object) -> _FakeScore:
        return _FakeScore()


class _FakeWorkerPool:
    """Duck-types WorkerPool.submit() to record dispatch without real scheduling."""

    def __init__(self) -> None:
        self.submitted: list[str] = []

    async def submit(self, task: Callable[[], Awaitable[None]], priority: Priority = Priority.NORMAL) -> None:
        self.submitted.append("submitted")
        await task()


class TestQualityScoringWorkerPool:
    """Sprint-016: background quality scoring runs through a bounded WorkerPool."""

    async def test_quality_scoring_dispatched_through_worker_pool(self) -> None:
        fake_pool = _FakeWorkerPool()
        engine = _build_engine(
            output_evaluator=_RecordingEvaluator(),
            quality_scoring_pool=cast(WorkerPool, fake_pool),
        )
        playback = PlaybackScheduler()
        turn = _make_turn(call_id="call-quality-pool-1")

        await engine.handle_turn(turn=turn, playback=playback)
        # Quality scoring is fire-and-forget — give the event loop a tick.
        await asyncio.sleep(0)

        assert fake_pool.submitted == ["submitted"]


class TestCallIdWrapping:
    async def test_handle_turn_wraps_call_id_as_call_id_newtype(self) -> None:
        """CallId(turn.call_id) wrapping must not raise for a plain str turn.call_id."""
        engine = _build_engine()
        playback = PlaybackScheduler()
        turn = _make_turn(call_id="call-plain-str")

        await engine.handle_turn(turn=turn, playback=playback)

        session = engine.get_session_state("call-plain-str")
        assert session is not None
        assert session.call_id == CallId("call-plain-str")


class TestObservabilityWiring:
    """Sprint-016: StructuredLogger/OTelTracer wiring on ConversationEngine.handle_turn."""

    async def test_handle_turn_emits_structured_log_with_trace_id(self) -> None:
        stream = io.StringIO()
        structured_logger = StructuredLogger("conversation-engine", stream=stream)
        tracer, exporter = OTelTracer.for_testing("conversation-engine")
        engine = _build_engine(structured_logger=structured_logger, tracer=tracer)
        playback = PlaybackScheduler()
        turn = _make_turn(call_id="call-observability-1")

        await engine.handle_turn(turn=turn, playback=playback)

        record = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert record["message"] == "turn complete"
        assert record["call_id"] == "call-observability-1"
        assert record["correlation_id"] == turn.turn_id

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "conversation_engine.handle_turn"
        expected_trace_id = format(spans[0].context.trace_id, "032x")
        assert record["trace_id"] == expected_trace_id

    async def test_handle_turn_works_without_observability_wiring(self) -> None:
        """No tracer/logger supplied — pre-Sprint-016 behavior unchanged."""
        engine = _build_engine()
        playback = PlaybackScheduler()
        turn = _make_turn(call_id="call-no-observability")

        clauses = await engine.handle_turn(turn=turn, playback=playback)

        assert len(clauses) > 0
