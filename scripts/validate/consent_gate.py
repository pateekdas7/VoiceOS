#!/usr/bin/env python3
"""Sprint-020 consent-gate DR validation — real Redis + EventBus.

Referenced by implementation/sprints/Sprint-020.md's DR Validation section:

    python3 scripts/validate/consent_gate.py --customer-id test-no-consent
    # Expected: call blocked; ConsentRequired event emitted

Evaluates a real ``process_customer_data`` PolicyRequest with
``has_consent=False`` through a PolicyEngine backed by a real Redis client +
EventBus/Publisher (same pattern as ``rbi_calling_hours.py``, Sprint-017),
and prints the resulting DENY decision plus the emitted
``compliance.policy.decision_made`` audit event for rule ``DPDP-CONSENT-
REQUIRED`` (V4 Ch2 §"DPDP", V4 Ch4 §4.9) — the concrete event VoiceOS emits
for what Sprint-020.md's illustrative text calls "ConsentRequired".
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
    parser.add_argument("--customer-id", required=True, help="Customer id to evaluate the consent gate for.")
    args = parser.parse_args()

    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"[consent_gate] connected to Redis — PING: {redis_conn.ping()}")

    bus = EventBus(redis_conn, stream="voiceos-events")
    publisher = Publisher(bus)
    engine = PolicyEngine(redis=redis_conn, publisher=publisher)

    call_id = f"validation-{uuid.uuid4()}"
    request = PolicyRequest(
        domain="dpdp",
        action="process_customer_data",
        subject="consent_gate_validation",
        resource=args.customer_id,
        tenant_id=str(uuid.uuid4()),
        context={"has_consent": False, "call_id": call_id, "customer_id": args.customer_id},
    )

    decision = engine.evaluate(request)
    print(
        f"[consent_gate] customer_id={args.customer_id} -> outcome={decision.outcome.value} reason={decision.reason!r}"
    )

    if decision.outcome.value not in ("DENY", "FORBID"):
        print("[consent_gate] FAIL: expected the call to be blocked (DENY/FORBID) for a customer with no consent")
        return 1

    replayed = [envelope for _entry_id, envelope in bus.replay_from() if envelope.payload.get("call_id") == call_id]
    if not replayed:
        print("[consent_gate] FAIL: no PolicyDecisionMade event was emitted for this denial")
        return 1

    envelope = replayed[-1]
    print(
        f"[consent_gate] PASS: call blocked; PolicyDecisionMade emitted "
        f"decision={envelope.payload['decision']} rule_id={envelope.payload['rule_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
