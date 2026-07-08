"""Integration tests: EventBus, Publisher, Consumer against real Redis Streams.

Requires REDIS_URL and Redis >= 6.2 (Streams support). Skipped automatically
when REDIS_URL is absent (see requires_redis).

Run:
    REDIS_URL=redis://localhost:6379/0 \\
    pytest tests/integration/libs/test_event_bus_integration.py -v

Architecture: V3 Ch3 (Event Bus); Sprint-013.
"""

from __future__ import annotations

import uuid
from typing import Any

from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.consumer import Consumer
from src.libs.event_bus.publisher import Publisher
from tests.fixtures.redis import TestRedis
from tests.integration.conftest import requires_redis


def _no_sleep(_seconds: float) -> None:
    pass


def _unique_stream(name: str) -> str:
    return f"voiceos:test:sprint013:{name}:{uuid.uuid4().hex[:8]}"


@requires_redis
class TestEventBusPublishConsumeIntegration:
    def test_event_bus_publish_consume(self) -> None:
        """publish -> consume -> ACK against real Redis Streams (Sprint-013 required test)."""
        with TestRedis() as r:
            stream = _unique_stream("publish-consume")
            bus = EventBus(r.client, stream=stream)
            publisher = Publisher(bus)
            consumer = Consumer(r.client, bus, group="main-group", consumer_name="worker-1", sleep_fn=_no_sleep)

            received: list[dict[str, Any]] = []
            consumer.subscribe("call.started", received.append)

            publisher.publish(
                event_type="call.started",
                tenant_id="tenant-integration-1",
                payload={"call_id": "c-integration-1"},
                correlation_id="corr-1",
            )

            processed = consumer.poll_once()

            assert processed == 1
            assert received == [{"call_id": "c-integration-1"}]

            pending = r.client.xpending(stream, "main-group")
            assert pending["pending"] == 0

            r.client.delete(stream)

    def test_replay_reconstructs_full_history(self) -> None:
        with TestRedis() as r:
            stream = _unique_stream("replay")
            bus = EventBus(r.client, stream=stream)
            publisher = Publisher(bus)

            for i in range(3):
                publisher.publish(
                    event_type="call.started",
                    tenant_id="tenant-integration-1",
                    payload={"n": i},
                    correlation_id="corr-1",
                )

            replayed = bus.replay_from()
            assert [env.payload["n"] for _id, env in replayed] == [0, 1, 2]

            r.client.delete(stream)


@requires_redis
class TestEventBusDLQIntegration:
    def test_event_bus_dlq_routing(self) -> None:
        """Consumer fails 3 times -> event in DLQ stream (Sprint-013 required test)."""
        with TestRedis() as r:
            stream = _unique_stream("dlq")
            bus = EventBus(r.client, stream=stream, max_retries=3)
            publisher = Publisher(bus)
            consumer = Consumer(
                r.client,
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
                raise RuntimeError("simulated integration failure")

            consumer.subscribe("call.started", always_fails)
            publisher.publish(
                event_type="call.started",
                tenant_id="tenant-integration-1",
                payload={},
                correlation_id="corr-1",
            )

            consumer.poll_once()

            assert attempts == 3
            assert r.client.xlen(bus.dlq_stream_name) == 1

            r.client.delete(stream)
            r.client.delete(bus.dlq_stream_name)
