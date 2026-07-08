#!/usr/bin/env python3
"""Sprint-024 usage-limit-enforcement validation — real Redis.

Referenced by implementation/sprints/Sprint-024.md's DR Validation section:

    python3 scripts/validate/usage_limit.py --exceed-limit
    # Expected: 429 response when GROWTH tier limit exceeded

Drives ``UsageLimitEnforcer.check_and_allow()`` against a real Redis client
and the real ``PolicyEngine``/``BillingPolicyPack`` for the GROWTH tier's
call-minute limit. Without ``--exceed-limit`` it stays comfortably under the
limit and expects PERMIT (HTTP 200-equivalent); with it, it requests
exactly the tier limit and expects the enforcer to block (HTTP 429-equivalent
— this repo's services have no bound HTTP listener until Sprint-026, so
"429 response" here means ``check_and_allow() -> False``, the exact signal
the future HTTP layer would translate into a 429).

    export REDIS_URL='redis://localhost:6379/0'
    python3 scripts/validate/usage_limit.py                  # -> PERMIT (200-equivalent)
    python3 scripts/validate/usage_limit.py --exceed-limit    # -> DENY (429-equivalent)
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import redis as redis_lib

from src.libs.contracts.models.billing import SubscriptionTier, UsageType
from src.libs.contracts.primitives import TenantId
from src.services.billing.entitlement import EntitlementEngine
from src.services.billing.rate_card import TIER_USAGE_LIMITS
from src.services.metering.enforcer import UsageLimitEnforcer
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exceed-limit", action="store_true", help="Request exactly the GROWTH tier's call-minute limit."
    )
    args = parser.parse_args()

    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"[usage_limit] connected to Redis — PING: {redis_conn.ping()}")

    limit = TIER_USAGE_LIMITS[SubscriptionTier.GROWTH][UsageType.CALL_MINUTE]
    assert limit is not None
    quantity = limit if args.exceed_limit else 1

    entitlement_engine = EntitlementEngine(PolicyEngineService(PolicyEngine()))
    enforcer = UsageLimitEnforcer(redis_conn, entitlement_engine)
    tenant_id = TenantId(f"validate-usage-limit-{uuid.uuid4()}")
    period = f"validate-{uuid.uuid4()}"

    allowed = enforcer.check_and_allow(tenant_id, SubscriptionTier.GROWTH, UsageType.CALL_MINUTE, quantity, period)
    response = "200 (PERMIT)" if allowed else "429 (DENY — usage limit exceeded)"
    print(f"[usage_limit] tier=GROWTH limit={limit} requested_quantity={quantity} -> {response}")

    expected_allowed = not args.exceed_limit
    ok = allowed == expected_allowed
    print(f"[usage_limit] RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
