#!/usr/bin/env python3
"""Sprint-017 RBI calling-hours validation — real Redis + EventBus.

Referenced by implementation/sprints/Sprint-017.md's DR Validation section:

    python3 scripts/validate/rbi_calling_hours.py --hour 21
    # Expected: PolicyDecisionMade event with DENY verdict emitted

Evaluates a real PolicyRequest at ``--hour`` through a PolicyEngine backed
by a real Redis client + EventBus/Publisher, and prints the resulting
PolicyDecision plus the ``PolicyDecisionMade`` audit event emitted for any
non-PERMIT outcome (V4 Ch4 §4.9).

    export REDIS_URL='redis://localhost:6379/0'
    python3 scripts/validate/rbi_calling_hours.py --hour 21   # -> DENY
    python3 scripts/validate/rbi_calling_hours.py --hour 10   # -> PERMIT
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import redis as redis_lib

from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.rule import PolicyRequest

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hour", type=int, required=True, help="Simulated local hour of the call attempt (0-23).")
    args = parser.parse_args()

    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"[rbi_calling_hours] connected to Redis — PING: {redis_conn.ping()}")

    bus = EventBus(redis_conn, stream="voiceos-events")
    publisher = Publisher(bus)
    engine = PolicyEngine(redis=redis_conn, publisher=publisher)

    call_id = f"validation-{uuid.uuid4()}"
    request = PolicyRequest(
        domain="rbi",
        action="admit_call",
        subject="rbi_calling_hours_validation",
        resource=call_id,
        tenant_id=str(uuid.uuid4()),
        context={"hour": args.hour, "call_id": call_id},
    )

    decision = engine.evaluate(request)
    print(f"[rbi_calling_hours] hour={args.hour} -> outcome={decision.outcome.value} reason={decision.reason!r}")

    replayed = [envelope for _entry_id, envelope in bus.replay_from() if envelope.payload.get("call_id") == call_id]
    if replayed:
        envelope = replayed[-1]
        print(
            f"[rbi_calling_hours] PolicyDecisionMade emitted: "
            f"decision={envelope.payload['decision']} rule_id={envelope.payload['rule_id']}"
        )
    else:
        print("[rbi_calling_hours] no PolicyDecisionMade event emitted (outcome was PERMIT)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
