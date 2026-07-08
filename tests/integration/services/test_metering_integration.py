"""Integration tests for the Usage Metering Platform (Sprint-024, V5 Ch10).

Required named tests (Sprint-024.md):
    test_metering_event_consumption — CallCompleted event -> usage_event in Postgres
    test_usage_limit_enforcer — real Redis + real metering -> enforcer blocks at limit

``saas.call.dispositioned`` (not a new ``CallCompleted`` event) is the real
call-completion signal in this codebase — see CHANGELOG.md Sprint-024
deviations and ``src/services/metering/collector.py``'s module docstring.
The event pipeline (EventBus/Publisher/Consumer) runs over ``FakeRedisClient``
(it fully emulates Redis Streams — same precedent as
``test_conversation_engine_event_bus_integration.py``); only the Postgres
side needs to be real for ``test_metering_event_consumption``, and only the
Redis side needs to be real for ``test_usage_limit_enforcer``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from src.libs.contracts.models.billing import SubscriptionTier, UsageType
from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.consumer import Consumer
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.billing import UsageRepository
from src.libs.repositories.tenant import TenantRepository
from src.services.billing.entitlement import EntitlementEngine
from src.services.billing.rate_card import DEFAULT_RATE_CARD, TIER_USAGE_LIMITS
from src.services.metering.collector import CALL_DISPOSITIONED_EVENT_TYPE, UsageCollector
from src.services.metering.enforcer import UsageLimitEnforcer
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService
from tests.fixtures.redis import FakeRedisClient, TestRedis
from tests.integration.conftest import requires_postgres, requires_redis

# `pg_conn` is a fixture from tests/integration/services/conftest.py — pytest
# auto-discovers it from the same-directory conftest.py, no import needed.


@pytest.fixture
def tenant_id(pg_conn: object) -> Iterator[TenantId]:
    """A real ``tenants`` row — ``usage_events.tenant_id`` is a real UUID FK to it."""
    now = datetime.now(UTC)
    new_tenant_id = TenantId(str(uuid.uuid4()))
    TenantRepository(pg_conn).create(
        Tenant(
            tenant_id=new_tenant_id,
            slug=f"metering-it-{uuid.uuid4().hex[:8]}",
            display_name="Metering Integration Test Tenant",
            subscription_tier="GROWTH",
            created_at=now,
            updated_at=now,
        )
    )
    yield new_tenant_id
    cur = pg_conn.cursor()  # type: ignore[attr-defined]
    cur.execute("DELETE FROM usage_events WHERE tenant_id = %s", (new_tenant_id,))
    cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (new_tenant_id,))
    pg_conn.commit()  # type: ignore[attr-defined]


@requires_postgres
class TestMeteringEventConsumption:
    def test_metering_event_consumption(self, pg_conn: object, tenant_id: TenantId) -> None:
        """A call-dispositioned event, published and consumed through a real EventBus
        pipeline, produces exactly one usage_event row in real Postgres."""
        usage_repo = UsageRepository(pg_conn)
        collector = UsageCollector(usage_repo, DEFAULT_RATE_CARD)

        redis = FakeRedisClient()
        bus = EventBus(redis)
        publisher = Publisher(bus)
        consumer = Consumer(redis, bus, group="metering-it-group", consumer_name="worker-1", sleep_fn=lambda _s: None)
        collector.register(consumer)

        call_id = f"call-{uuid.uuid4()}"
        publisher.publish(
            event_type=CALL_DISPOSITIONED_EVENT_TYPE,
            tenant_id=tenant_id,
            payload={
                "event_id": str(uuid.uuid4()),
                "occurred_at": datetime.now(UTC).isoformat(),
                "tenant_id": str(tenant_id),
                "call_id": call_id,
                "customer_id": "cust-1",
                "loan_account_id": "loan-1",
                "outcome_code": "PTP_MADE",
                "duration_ms": 90_000,
                "dispositioned_at": datetime.now(UTC).isoformat(),
            },
            correlation_id=call_id,
        )

        processed = consumer.poll_once()
        assert processed == 1

        events = usage_repo.find_uninvoiced(tenant_id)
        matching = [e for e in events if e.resource_id == call_id]
        assert len(matching) == 1
        assert matching[0].usage_type == UsageType.CALL_MINUTE
        assert matching[0].quantity == 2  # 90s -> ceil to 2 minutes

    def test_metering_event_consumption_is_idempotent_on_redelivery(self, pg_conn: object, tenant_id: TenantId) -> None:
        """The same event delivered twice (e.g. consumer-group redelivery after a crash
        before ACK) still produces exactly one usage_event row."""
        usage_repo = UsageRepository(pg_conn)
        collector = UsageCollector(usage_repo, DEFAULT_RATE_CARD)

        event_id = str(uuid.uuid4())
        call_id = f"call-{uuid.uuid4()}"
        payload = {
            "event_id": event_id,
            "occurred_at": datetime.now(UTC).isoformat(),
            "tenant_id": str(tenant_id),
            "call_id": call_id,
            "duration_ms": 60_000,
        }

        collector.handle_call_dispositioned(payload)
        collector.handle_call_dispositioned(payload)

        events = [e for e in usage_repo.find_uninvoiced(tenant_id) if e.resource_id == call_id]
        assert len(events) == 1


@requires_redis
class TestUsageLimitEnforcerRealRedis:
    def test_usage_limit_enforcer(self) -> None:
        """Real Redis + real metering -> enforcer blocks at the GROWTH call-minute limit."""
        entitlement_engine = EntitlementEngine(PolicyEngineService(PolicyEngine()))
        limit = TIER_USAGE_LIMITS[SubscriptionTier.GROWTH][UsageType.CALL_MINUTE]
        assert limit is not None

        with TestRedis() as r:
            r.flush()
            enforcer = UsageLimitEnforcer(r.client, entitlement_engine)
            redis_tenant_id = str(uuid.uuid4())
            period = f"test-{uuid.uuid4()}"

            under_limit = enforcer.check_and_allow(
                redis_tenant_id, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, limit - 1, period
            )
            at_limit = enforcer.check_and_allow(
                redis_tenant_id, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, 5, period
            )

            assert under_limit is True
            assert at_limit is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
