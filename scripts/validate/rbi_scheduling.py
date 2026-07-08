#!/usr/bin/env python3
"""Sprint-023 RBI scheduling validation — real Policy Engine (in-process).

Referenced by implementation/sprints/Sprint-023.md's DR Validation section:

    python3 scripts/validate/rbi_scheduling.py --hour 21
    # Expected: schedule returns None (DENY from PolicyEngine)

Runs ``ScheduleEngine.schedule_next_call()`` against the real, unwrapped
``PolicyEngine``/``RBIPolicyPack`` (no Redis/Postgres dependency needed —
``PolicyEngine()`` with no backends wired evaluates every built-in rule
globally, per its own docstring). This is the campaign-scheduling analogue
of Sprint-017's ``rbi_calling_hours.py`` DR check.

    python3 scripts/validate/rbi_scheduling.py --hour 21   # -> None (DENY)
    python3 scripts/validate/rbi_scheduling.py --hour 10   # -> a datetime (PERMIT)
    python3 scripts/validate/rbi_scheduling.py --hour 10 --calls-today 3   # -> None (frequency cap)
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.libs.contracts.primitives import TenantId
from src.services.campaign_management.scheduler import ScheduleEngine
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.service import PolicyEngineService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hour", type=int, required=True, help="Simulated local hour of the call attempt (0-23).")
    parser.add_argument("--calls-today", type=int, default=0, help="Calls already placed to this customer today.")
    args = parser.parse_args()

    engine = ScheduleEngine(PolicyEngineService(PolicyEngine()))
    tenant_id = TenantId(str(uuid.uuid4()))
    customer_id = f"validation-customer-{uuid.uuid4()}"
    campaign_id = f"validation-campaign-{uuid.uuid4()}"

    result = engine.schedule_next_call(
        tenant_id, customer_id, campaign_id, hour=args.hour, calls_today_count=args.calls_today
    )

    print(f"[rbi_scheduling] hour={args.hour} calls_today={args.calls_today} -> schedule_next_call()={result!r}")
    print("[rbi_scheduling] " + ("DENIED (None)" if result is None else "PERMITTED"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
