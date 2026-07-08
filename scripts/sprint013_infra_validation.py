#!/usr/bin/env python3
"""Sprint-013 Phase 2 infrastructure validation — run against real Redis.

Exercises the exact scenarios listed in implementation/sprints/Sprint-013.md
Phase 2 "Integration validation" and "Infrastructure Validation" sections
against a live Redis instance, and prints a report. Not part of the pytest
suite (pytest coverage of the same behaviors lives in tests/unit/libs and
tests/integration/libs) — this is an operational smoke-test / evidence
script for the sprint completion report.

Usage:
    REDIS_URL=redis://localhost:6379/0 python scripts/sprint013_infra_validation.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis as redis_lib

from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.consumer import Consumer
from src.libs.event_bus.dedup import EventDeduplicator
from src.libs.event_bus.publisher import Publisher
from src.libs.redis_client.lock import DistributedLock
from src.libs.redis_client.rate_limiter import RateLimiter
from src.libs.redis_client.ttl_guard import MissingTTLError, TTLGuard

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def main() -> int:
    client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"Connected to Redis — PING: {client.ping()}")
    print(f"Redis version: {client.info('server')['redis_version']}")
    print()

    results: list[tuple[str, bool, str]] = []

    # 1. Publish -> consume round trip (production stream/group names).
    stream = "voiceos-events"
    bus = EventBus(client, stream=stream, max_retries=3)
    bus.ensure_consumer_group("main-group")
    publisher = Publisher(bus)
    consumer = Consumer(client, bus, group="main-group", consumer_name="validation-worker", sleep_fn=lambda _s: None)

    received = []
    consumer.subscribe("call.started", received.append)

    t0 = time.perf_counter()
    publisher.publish(
        event_type="call.started",
        tenant_id="tenant-validation",
        payload={"call_id": "call-validation-001"},
        correlation_id="corr-validation-001",
    )
    processed = consumer.poll_once()
    publish_consume_ms = (time.perf_counter() - t0) * 1000

    ok = processed == 1 and len(received) == 1
    results.append(("publish -> consume round trip", ok, f"{publish_consume_ms:.2f}ms, processed={processed}"))

    # 2. Dedup: same event_id twice -> handler called once.
    dedup = EventDeduplicator(client, stream="voiceos-events-dedup-check")
    first = dedup.is_duplicate("evt-validation-1")
    second = dedup.is_duplicate("evt-validation-1")
    results.append(
        ("dedup: same event_id twice", (first is False and second is True), f"first={first} second={second}")
    )

    # 3. DLQ routing: handler fails 3x -> event in DLQ, DLQ depth == 1.
    dlq_bus = EventBus(client, stream="voiceos-events-dlq-check", max_retries=3)
    dlq_bus.ensure_consumer_group("main-group")
    dlq_publisher = Publisher(dlq_bus)
    dlq_consumer = Consumer(
        client, dlq_bus, group="main-group", consumer_name="validation-worker", max_retries=3, sleep_fn=lambda _s: None
    )
    attempts = {"n": 0}

    def always_fails(_payload: object) -> None:
        attempts["n"] += 1
        raise RuntimeError("validation-induced failure")

    dlq_consumer.subscribe("call.started", always_fails)
    dlq_publisher.publish(event_type="call.started", tenant_id="tenant-validation", payload={}, correlation_id="corr-2")
    dlq_consumer.poll_once()
    dlq_depth = dlq_bus.dlq_depth()
    results.append(
        (
            "DLQ routing after 3 failures",
            (attempts["n"] == 3 and dlq_depth == 1),
            f"attempts={attempts['n']} dlq_depth={dlq_depth}",
        )
    )
    client.delete("voiceos-events-dlq-check", "dlq:voiceos-events-dlq-check")

    # 4. Distributed lock: two workers contend, one wins, fencing token increments on takeover.
    lock = DistributedLock(client, ttl_ms=5000)
    resource = "voiceos:test:validation:call-lock-1"
    token_a = lock.acquire(resource, owner_id="worker-A")
    token_b_blocked = lock.acquire(resource, owner_id="worker-B")
    released = lock.release(token_a) if token_a else False
    token_b = lock.acquire(resource, owner_id="worker-B") if released else None
    ok = (
        bool(token_a)
        and token_b_blocked is None
        and released
        and bool(token_b)
        and token_b.fencing_token > token_a.fencing_token
    )
    results.append(
        (
            "DistributedLock: contend, release, fencing token increments",
            ok,
            f"a={token_a} blocked={token_b_blocked} b={token_b}",
        )
    )
    if token_b:
        lock.release(token_b)

    # 5. RateLimiter: 15 rapid calls, limit=10 -> 5 blocked.
    limiter = RateLimiter(client)
    key = "voiceos:test:validation:tenant-ratelimit"
    outcomes = [limiter.check(key, limit=10, window_seconds=5).allowed for _ in range(15)]
    allowed_count = sum(outcomes)
    blocked_count = len(outcomes) - allowed_count
    results.append(
        (
            "RateLimiter: 15 calls, limit=10",
            (allowed_count == 10 and blocked_count == 5),
            f"allowed={allowed_count} blocked={blocked_count}",
        )
    )
    client.delete(f"voiceos:ratelimit:{key}")

    # 6. TTLGuard: every write carries a TTL; bare write without TTL raises.
    guard = TTLGuard(client)
    ttl_key = "voiceos:test:validation:ttl-check"
    guard.set(ttl_key, b"1", ex=60)
    ttl_value = client.ttl(ttl_key)
    ttl_ok = ttl_value > 0
    try:
        guard.set(ttl_key, b"1")
        missing_ttl_raised = False
    except MissingTTLError:
        missing_ttl_raised = True
    results.append(
        (
            "TTLGuard: TTL present + MissingTTLError enforced",
            (ttl_ok and missing_ttl_raised),
            f"ttl={ttl_value} raised={missing_ttl_raised}",
        )
    )
    client.delete(ttl_key)

    # Cleanup validation streams (keep production 'voiceos-events' stream/group).
    client.delete("voiceos-events-dedup-check:__unused__")

    print(f"{'CHECK':<55} {'RESULT':<8} DETAIL")
    print("-" * 100)
    all_ok = True
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<55} {status:<8} {detail}")

    print()
    print(f"voiceos-events XLEN: {client.xlen('voiceos-events')}")
    print(f"voiceos-events main-group XPENDING: {client.xpending('voiceos-events', 'main-group')}")
    print(f"eventbus_dlq_depth (dlq:voiceos-events): {client.xlen('dlq:voiceos-events')}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
