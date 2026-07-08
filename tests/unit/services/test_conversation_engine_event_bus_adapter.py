"""Unit tests for RedisEventBusAdapter — ConversationEngine.EventBusPort wiring.

Architecture: V3 Ch3 (Event Bus); V6 Ch6 EV-6; Sprint-013.
"""

from __future__ import annotations

from datetime import datetime

from src.libs.contracts.decision import DecisionEnvelope, GovernanceStatus, GovernanceVerdict
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.services.conversation_engine.engine import EventBusPort
from src.services.conversation_engine.event_bus_adapter import (
    DECISION_MADE_EVENT_TYPE,
    RedisEventBusAdapter,
)
from tests.fixtures.redis import FakeRedisClient


def _make_envelope() -> DecisionEnvelope:
    return DecisionEnvelope(
        envelope_id="envelope-1",
        call_id="call-1",
        tenant_id="tenant-1",
        timestamp=datetime.utcnow(),
        response_plan_id="plan-1",
        governance_verdict=GovernanceVerdict(status=GovernanceStatus.APPROVE),
    )


class TestRedisEventBusAdapter:
    def test_conforms_to_event_bus_port_protocol(self, fake_redis: FakeRedisClient) -> None:
        bus = EventBus(fake_redis, stream="voiceos-events")
        adapter = RedisEventBusAdapter(Publisher(bus))
        assert isinstance(adapter, EventBusPort)

    async def test_publish_appends_decision_made_event_to_stream(self, fake_redis: FakeRedisClient) -> None:
        bus = EventBus(fake_redis, stream="voiceos-events")
        adapter = RedisEventBusAdapter(Publisher(bus))

        await adapter.publish(_make_envelope())

        assert fake_redis.xlen("voiceos-events") == 1
        replayed = bus.replay_from()
        assert len(replayed) == 1
        _entry_id, envelope = replayed[0]
        assert envelope.event_type == DECISION_MADE_EVENT_TYPE
        assert envelope.tenant_id == "tenant-1"
        assert envelope.payload["envelope_id"] == "envelope-1"
        assert envelope.payload["call_id"] == "call-1"
        assert envelope.payload["response_plan_id"] == "plan-1"
        assert envelope.payload["governance_status"] == "APPROVE"
        assert envelope.payload["decision_count"] == 0

    async def test_consumer_receives_decision_made_event(self, fake_redis: FakeRedisClient) -> None:
        from src.libs.event_bus.consumer import Consumer

        bus = EventBus(fake_redis, stream="voiceos-events")
        adapter = RedisEventBusAdapter(Publisher(bus))
        consumer = Consumer(fake_redis, bus, group="main-group", consumer_name="worker-1", sleep_fn=lambda _s: None)

        received: list[dict[str, object]] = []
        consumer.subscribe(DECISION_MADE_EVENT_TYPE, received.append)

        await adapter.publish(_make_envelope())
        processed = consumer.poll_once()

        assert processed == 1
        assert received[0]["call_id"] == "call-1"
