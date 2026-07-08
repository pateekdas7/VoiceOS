"""Unit tests: EventBus, Publisher, Consumer, EventDeduplicator, DLQHandler, EventRouter.

Uses FakeRedisClient's Streams emulation (tests/fixtures/redis.py) so these
tests run with no real I/O. Real-Redis round-trip behaviour is covered by
tests/integration/libs/test_event_bus_integration.py.

Architecture: V3 Ch3 (Event Bus); V6 Ch6 (Event Standards); Sprint-013.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.libs.contracts.events.envelope import EventEnvelope
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.consumer import Consumer
from src.libs.event_bus.dedup import EventDeduplicator
from src.libs.event_bus.dlq import DLQHandler
from src.libs.event_bus.publisher import Publisher
from src.libs.event_bus.router import EventRouter
from tests.fixtures.redis import FakeRedisClient


def _no_sleep(_seconds: float) -> None:
    """Injectable no-op sleep so retry-backoff tests run instantly."""


class TestEventRouter:
    def test_register_and_lookup(self) -> None:
        router = EventRouter()
        calls: list[dict[str, Any]] = []
        router.register("call.started", calls.append)
        handlers = router.handlers_for("call.started")
        assert len(handlers) == 1
        handlers[0]({"call_id": "c1"})
        assert calls == [{"call_id": "c1"}]

    def test_unregistered_event_type_returns_empty(self) -> None:
        router = EventRouter()
        assert router.handlers_for("nothing.here") == []

    def test_multiple_handlers_all_invoked(self) -> None:
        router = EventRouter()
        seen: list[str] = []
        router.register("call.started", lambda _p: seen.append("a"))
        router.register("call.started", lambda _p: seen.append("b"))
        for handler in router.handlers_for("call.started"):
            handler({})
        assert seen == ["a", "b"]

    def test_event_types_lists_registered_types(self) -> None:
        router = EventRouter()
        router.register("call.started", lambda _p: None)
        router.register("call.ended", lambda _p: None)
        assert set(router.event_types()) == {"call.started", "call.ended"}


class TestPublisher:
    def test_publish_round_trip(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        publisher = Publisher(bus)
        envelope = publisher.publish(
            event_type="call.started",
            tenant_id=tenant_id,
            payload={"call_id": "c1"},
            correlation_id="corr-1",
        )
        assert envelope.event_type == "call.started"
        assert envelope.tenant_id == tenant_id
        assert bus_stream_length(fake_redis, "test-events") == 1

    def test_event_envelope_schema_enforcement(self, fake_redis: FakeRedisClient) -> None:
        """Missing tenant_id -> Publisher raises (Sprint-013 AC)."""
        bus = EventBus(fake_redis, stream="test-events")
        publisher = Publisher(bus)
        with pytest.raises(ValidationError):
            publisher.publish(
                event_type="call.started",
                tenant_id=None,
                payload={},
                correlation_id="corr-1",
            )

    def test_each_publish_gets_a_unique_event_id(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        publisher = Publisher(bus)
        e1 = publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={}, correlation_id="c1")
        e2 = publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={}, correlation_id="c1")
        assert e1.event_id != e2.event_id

    def test_auto_generates_trace_id_when_absent(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        publisher = Publisher(bus)
        envelope = publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={}, correlation_id="c1")
        assert envelope.trace_id


def bus_stream_length(redis: FakeRedisClient, stream: str) -> int:
    return redis.xlen(stream)


class TestEventBusPublishConsume:
    def test_publish_then_replay(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        publisher = Publisher(bus)
        publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={"n": 1}, correlation_id="c1")
        publisher.publish(event_type="call.ended", tenant_id=tenant_id, payload={"n": 2}, correlation_id="c1")

        replayed = bus.replay_from()
        assert [env.event_type for _id, env in replayed] == ["call.started", "call.ended"]

    def test_consumer_group_receives_published_event(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        publisher = Publisher(bus)
        consumer = Consumer(fake_redis, bus, group="main-group", consumer_name="worker-1", sleep_fn=_no_sleep)

        received: list[dict[str, Any]] = []
        consumer.subscribe("call.started", received.append)

        publisher.publish(
            event_type="call.started", tenant_id=tenant_id, payload={"call_id": "c1"}, correlation_id="c1"
        )
        processed = consumer.poll_once()

        assert processed == 1
        assert received == [{"call_id": "c1"}]

    def test_unsubscribed_event_type_is_acked_without_handler_call(
        self, fake_redis: FakeRedisClient, tenant_id: TenantId
    ) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        publisher = Publisher(bus)
        consumer = Consumer(fake_redis, bus, group="main-group", consumer_name="worker-1", sleep_fn=_no_sleep)

        publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={}, correlation_id="c1")
        processed = consumer.poll_once()

        assert processed == 1  # entry was read and ACKed even with no handler registered
        pending = fake_redis.xpending("test-events", "main-group")
        assert pending["pending"] == 0

    def test_start_processes_events_across_bounded_iterations(
        self, fake_redis: FakeRedisClient, tenant_id: TenantId
    ) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        publisher = Publisher(bus)
        consumer = Consumer(fake_redis, bus, group="main-group", consumer_name="worker-1", sleep_fn=_no_sleep)

        received: list[dict[str, Any]] = []
        consumer.subscribe("call.started", received.append)
        publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={"n": 1}, correlation_id="c1")

        consumer.start(max_iterations=3)

        assert received == [{"n": 1}]

    def test_start_idle_sleep_invoked_when_stream_is_empty(
        self, fake_redis: FakeRedisClient, tenant_id: TenantId
    ) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        sleep_calls: list[float] = []
        consumer = Consumer(
            fake_redis,
            bus,
            group="main-group",
            consumer_name="worker-1",
            sleep_fn=sleep_calls.append,
        )

        consumer.start(max_iterations=2, idle_sleep_s=0.01)

        assert sleep_calls == [0.01, 0.01]

    def test_stop_halts_the_polling_loop(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        consumer = Consumer(fake_redis, bus, group="main-group", consumer_name="worker-1", sleep_fn=_no_sleep)

        iterations_seen = 0
        original_poll_once = consumer.poll_once

        def counting_poll_once(count: int = 10) -> int:
            nonlocal iterations_seen
            iterations_seen += 1
            if iterations_seen >= 2:
                consumer.stop()
            return original_poll_once(count=count)

        consumer.poll_once = counting_poll_once  # type: ignore[method-assign]
        consumer.start(max_iterations=None)

        assert iterations_seen == 2


class TestConsumerDedup:
    def test_duplicate_event_id_processed_once(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        """Dedup: same event_id delivered twice -> handler called exactly once."""
        bus = EventBus(fake_redis, stream="test-events")
        consumer = Consumer(fake_redis, bus, group="main-group", consumer_name="worker-1", sleep_fn=_no_sleep)

        call_count = 0

        def handler(_payload: dict[str, Any]) -> None:
            nonlocal call_count
            call_count += 1

        consumer.subscribe("call.started", handler)

        envelope = EventEnvelope(
            event_type="call.started",
            tenant_id=tenant_id,
            correlation_id="c1",
            trace_id="t1",
            payload={},
        )
        # Publish the *same* envelope (same event_id) twice.
        bus.publish(envelope)
        bus.publish(envelope)

        consumer.poll_once()
        consumer.poll_once()

        assert call_count == 1


class TestEventDeduplicator:
    def test_event_dedup_same_id(self, fake_redis: FakeRedisClient) -> None:
        """Same event_id submitted twice -> second call returns is_duplicate=True (Sprint-013 required test)."""
        dedup = EventDeduplicator(fake_redis, stream="test-events")
        first = dedup.is_duplicate("evt-1")
        second = dedup.is_duplicate("evt-1")
        assert first is False
        assert second is True

    def test_different_event_ids_are_independent(self, fake_redis: FakeRedisClient) -> None:
        dedup = EventDeduplicator(fake_redis, stream="test-events")
        assert dedup.is_duplicate("evt-1") is False
        assert dedup.is_duplicate("evt-2") is False

    def test_dedup_is_scoped_per_stream(self, fake_redis: FakeRedisClient) -> None:
        dedup_a = EventDeduplicator(fake_redis, stream="stream-a")
        dedup_b = EventDeduplicator(fake_redis, stream="stream-b")
        assert dedup_a.is_duplicate("evt-shared") is False
        assert dedup_b.is_duplicate("evt-shared") is False


class TestDLQRouting:
    def test_events_failing_all_retries_move_to_dlq(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        """Consumer fails N times -> event moved to dlq:{stream} (Sprint-013 AC)."""
        bus = EventBus(fake_redis, stream="test-events", max_retries=3)
        publisher = Publisher(bus)
        consumer = Consumer(
            fake_redis,
            bus,
            group="main-group",
            consumer_name="worker-1",
            max_retries=3,
            sleep_fn=_no_sleep,
        )

        attempts = 0

        def always_fails(_payload: dict[str, Any]) -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("simulated handler failure")

        consumer.subscribe("call.started", always_fails)
        publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={}, correlation_id="c1")

        consumer.poll_once()

        assert attempts == 3
        assert bus.dlq_depth() == 1
        pending = fake_redis.xpending("test-events", "main-group")
        assert pending["pending"] == 0  # ACKed after DLQ routing — not stuck in PEL

    def test_dlq_entry_carries_failure_reason(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        bus = EventBus(fake_redis, stream="test-events", max_retries=1)
        publisher = Publisher(bus)
        consumer = Consumer(
            fake_redis, bus, group="main-group", consumer_name="worker-1", max_retries=1, sleep_fn=_no_sleep
        )
        consumer.subscribe("call.started", lambda _p: (_ for _ in ()).throw(RuntimeError("boom")))
        publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={}, correlation_id="c1")

        consumer.poll_once()

        dlq_entries = fake_redis.xrange(bus.dlq_stream_name)
        assert len(dlq_entries) == 1
        _entry_id, fields = dlq_entries[0]
        assert b"boom" in fields[b"_dlq_reason"]

    def test_successful_handler_does_not_reach_dlq(self, fake_redis: FakeRedisClient, tenant_id: TenantId) -> None:
        bus = EventBus(fake_redis, stream="test-events", max_retries=3)
        publisher = Publisher(bus)
        consumer = Consumer(fake_redis, bus, group="main-group", consumer_name="worker-1", sleep_fn=_no_sleep)
        consumer.subscribe("call.started", lambda _p: None)
        publisher.publish(event_type="call.started", tenant_id=tenant_id, payload={}, correlation_id="c1")

        consumer.poll_once()

        assert bus.dlq_depth() == 0


class TestDLQHandler:
    def test_route_to_dlq_increments_depth(self, fake_redis: FakeRedisClient) -> None:
        dlq = DLQHandler(fake_redis, stream="test-events")
        assert dlq.depth() == 0
        dlq.route_to_dlq("1-0", {b"envelope": b"{}"}, reason="boom", attempt_count=3)
        assert dlq.depth() == 1

    def test_dlq_stream_name_is_prefixed(self, fake_redis: FakeRedisClient) -> None:
        dlq = DLQHandler(fake_redis, stream="voiceos-events")
        assert dlq.dlq_stream_name == "dlq:voiceos-events"


class TestEventBusConsumerGroups:
    def test_ensure_consumer_group_is_idempotent(self, fake_redis: FakeRedisClient) -> None:
        bus = EventBus(fake_redis, stream="test-events")
        bus.ensure_consumer_group("main-group")
        bus.ensure_consumer_group("main-group")  # must not raise (BUSYGROUP swallowed)

    def test_ensure_consumer_group_reraises_non_busygroup_errors(self, fake_redis: FakeRedisClient) -> None:
        bus = EventBus(fake_redis, stream="test-events")

        def broken_xgroup_create(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("connection refused")

        fake_redis.xgroup_create = broken_xgroup_create  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="connection refused"):
            bus.ensure_consumer_group("main-group")


class TestDecodeEnvelope:
    def test_decode_envelope_raises_on_missing_field(self) -> None:
        with pytest.raises(ValueError, match="missing the 'envelope' field"):
            EventBus.decode_envelope({b"other": b"data"})
