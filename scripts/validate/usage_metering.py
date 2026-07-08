#!/usr/bin/env python3
"""Sprint-024 usage-metering validation — real Postgres + Redis.

Referenced by implementation/sprints/Sprint-024.md's DR Validation section:

    python3 scripts/validate/usage_metering.py
    # Expected: usage_event in Postgres; Redis counter incremented

Emits a test ``saas.call.dispositioned`` event (the real call-completion
signal this codebase uses — see CHANGELOG.md Sprint-024 deviations) through
a real EventBus/Consumer pipeline into ``UsageCollector``, verifies the
resulting ``usage_event`` row in Postgres, then drives
``UsageLimitEnforcer.check_and_allow()`` against real Redis to show the
fast-path counter incrementing.

    export POSTGRES_DSN='postgresql://user:pass@host:5432/voiceos'
    export REDIS_URL='redis://localhost:6379/0'
    python3 scripts/validate/usage_metering.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import psycopg2
import redis as redis_lib

from src.libs.contracts.models.billing import SubscriptionTier, UsageType
from src.libs.contracts.models.tenant import Tenant
from src.libs.contracts.primitives import TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.consumer import Consumer
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.billing import UsageRepository
from src.libs.repositories.tenant import TenantRepository
from src.services.billing.entitlement import EntitlementEngine
from src.services.billing.rate_card import DEFAULT_RATE_CARD
from src.services.metering.collector import CALL_DISPOSITIONED_EVENT_TYPE, UsageCollector
from src.services.metering.enforcer import UsageLimitEnforcer
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService

POSTGRES_DSN = os.environ["POSTGRES_DSN"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def main() -> int:
    conn = psycopg2.connect(POSTGRES_DSN)
    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"[usage_metering] connected to Postgres and Redis — PING: {redis_conn.ping()}")

    tenant_id = TenantId(str(uuid.uuid4()))
    call_id = f"call-{uuid.uuid4()}"
    now = datetime.now(UTC)
    TenantRepository(conn).create(
        Tenant(
            tenant_id=tenant_id,
            slug=f"validate-usage-metering-{uuid.uuid4().hex[:8]}",
            display_name="usage_metering.py validation tenant",
            subscription_tier="GROWTH",
            created_at=now,
            updated_at=now,
        )
    )

    usage_repo = UsageRepository(conn)
    collector = UsageCollector(usage_repo, DEFAULT_RATE_CARD)
    bus = EventBus(redis_conn, stream="voiceos-events")
    publisher = Publisher(bus)
    consumer = Consumer(redis_conn, bus, group="usage-metering-validation", consumer_name="worker-1")
    collector.register(consumer)

    publisher.publish(
        event_type=CALL_DISPOSITIONED_EVENT_TYPE,
        tenant_id=tenant_id,
        payload={
            "event_id": str(uuid.uuid4()),
            "occurred_at": datetime.now(UTC).isoformat(),
            "tenant_id": str(tenant_id),
            "call_id": call_id,
            "duration_ms": 90_000,
        },
        correlation_id=call_id,
    )
    processed = consumer.poll_once()
    print(f"[usage_metering] events processed: {processed}")

    events = [e for e in usage_repo.find_uninvoiced(tenant_id) if e.resource_id == call_id]
    print(f"[usage_metering] usage_event rows in Postgres for {call_id}: {len(events)}")
    if events:
        print(f"[usage_metering]   usage_type={events[0].usage_type.value} quantity={events[0].quantity}")

    entitlement_engine = EntitlementEngine(PolicyEngineService(PolicyEngine()))
    enforcer = UsageLimitEnforcer(redis_conn, entitlement_engine)
    period = f"validate-{uuid.uuid4()}"
    before = enforcer.current_usage(tenant_id, UsageType.CALL_MINUTE, period)
    enforcer.check_and_allow(tenant_id, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, 2, period)
    after = enforcer.current_usage(tenant_id, UsageType.CALL_MINUTE, period)
    print(f"[usage_metering] Redis counter before={before} after={after}")

    cur = conn.cursor()
    cur.execute("DELETE FROM usage_events WHERE tenant_id = %s", (tenant_id,))
    cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
    conn.commit()
    conn.close()

    ok = len(events) == 1 and after == before + 2
    print(f"[usage_metering] RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
