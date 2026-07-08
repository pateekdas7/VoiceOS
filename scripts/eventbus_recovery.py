#!/usr/bin/env python3
"""EventBus consumer-group recovery — TT-002.

Root cause (see implementation/BACKLOG.md TT-002 / CPU_NODE_STATE.md §18):
Redis on the CPU node had no persistence (``appendonly no``), and no
long-running VoiceOS consumer process exists yet (services remain library
classes until Sprint-026 K8s/Helm) to invoke ``Consumer.__init__()`` — which
already self-heals the consumer group via ``EventBus.ensure_consumer_group()``
on every construction. Without a live process, recovery depended entirely on
a human re-running a one-shot ``redis-cli XGROUP CREATE`` command by hand
after every Redis restart.

This script is the single, canonical, idempotent "detect + recreate"
mechanism — the same underlying call every real Consumer already makes at
startup — invokable standalone, from `restore.sh`, and from `healthcheck.sh`.
Safe to run any number of times: ``XGROUP CREATE ... MKSTREAM`` is BUSYGROUP-safe
(a pre-existing group raises BUSYGROUP, swallowed — see
``EventBus.ensure_consumer_group``), so there is no race condition or
duplicate-processing risk from calling this repeatedly or concurrently.

Usage:
    REDIS_URL=redis://localhost:6379/0 python scripts/eventbus_recovery.py
    python scripts/eventbus_recovery.py --check-only   # detect only, exit 1 if unhealthy

Architecture: V3 Ch3 (EventBus); V3 Ch4 §4.13 (Redis persistence — TT-002).
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.libs.event_bus.bus import DEFAULT_STREAM, EventBus

DEFAULT_GROUP = "main-group"


def _connect(redis_url: str) -> object:
    import redis

    return redis.Redis.from_url(redis_url, protocol=2)


def consumer_group_exists(client: object, stream: str, group: str) -> bool:
    """Return True iff ``group`` is present on ``stream`` (stream itself must exist)."""
    try:
        groups = client.xinfo_groups(stream)  # type: ignore[attr-defined]
    except Exception as exc:
        if "no such key" in str(exc).lower():
            return False
        raise
    return any((g.get(b"name") or g.get("name")) in (group, group.encode()) for g in groups)


def recover(client: object, stream: str, group: str, dlq_group: str) -> bool:
    """Ensure the primary consumer group and DLQ stream exist. Returns True if healthy after the call.

    Args:
        client: A connected Redis-compatible client (``redis.Redis`` or
            ``FakeRedisClient``) — injected so this function is unit-testable
            without a real Redis connection.
        stream: Primary EventBus stream name.
        group: Primary consumer group name.
        dlq_group: Consumer group name to pre-create on the DLQ stream.
    """
    bus = EventBus(client, stream=stream)

    was_present = consumer_group_exists(client, stream, group)
    bus.ensure_consumer_group(group)  # idempotent — BUSYGROUP swallowed

    # Pre-create the DLQ stream too (idempotent) so "DLQ exists" holds even
    # before any event has ever failed processing.
    try:
        client.xgroup_create(bus.dlq_stream_name, dlq_group, id="0", mkstream=True)  # type: ignore[attr-defined]
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    now_present = consumer_group_exists(client, stream, group)
    if not was_present and now_present:
        print(f"RECOVERED: consumer group '{group}' was missing on '{stream}' — recreated.")
    elif now_present:
        print(f"OK: consumer group '{group}' already present on '{stream}' — no action needed.")
    else:
        print(f"FAIL: consumer group '{group}' still absent on '{stream}' after recovery attempt.", file=sys.stderr)
    return now_present


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Detect only; do not recreate. Exit 1 if unhealthy.")
    parser.add_argument("--stream", default=os.environ.get("EVENT_BUS_STREAM", DEFAULT_STREAM))
    parser.add_argument("--group", default=os.environ.get("EVENT_BUS_CONSUMER_GROUP", DEFAULT_GROUP))
    args = parser.parse_args()

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    dlq_group = f"{args.group}-dlq"

    if args.check_only:
        client = _connect(redis_url)
        healthy = consumer_group_exists(client, args.stream, args.group)
        print(f"{'OK' if healthy else 'MISSING'}: consumer group '{args.group}' on '{args.stream}'")
        return 0 if healthy else 1

    client = _connect(redis_url)
    healthy = recover(client, args.stream, args.group, dlq_group)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
