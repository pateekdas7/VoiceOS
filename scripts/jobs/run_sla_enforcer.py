#!/usr/bin/env python3
"""K8s CronJob runner — HITL SLA enforcement (Phase 6a).

Runs as a one-shot job every 60 seconds (CronJob schedule: */1 * * * *).
Connects to Postgres, polls all open HITL queue items across all tenants,
escalates any items that have crossed their priority-based SLA deadline.

Exit 0 on success, exit 1 on error (triggers CronJob backoffLimit retry).

Required environment variables:
    POSTGRES_DSN  -- e.g. postgresql://user:pass@host:5432/voiceos

Architecture: V4 Ch15 (Human Oversight — SLA enforcement), Phase 6a.
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
_log = logging.getLogger("voiceos.jobs.sla_enforcer")


def main() -> int:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        _log.error("POSTGRES_DSN is required")
        return 1

    try:
        import psycopg2

        from src.libs.repositories.hitl import HITLQueueRepository
        from src.services.hitl.sla_enforcer import SLAEnforcer

        conn = psycopg2.connect(dsn)
        try:
            repo = HITLQueueRepository(conn)
            enforcer = SLAEnforcer(repo)
            breached = enforcer.check_and_escalate()
            _log.info("SLA enforcement complete: %d items newly breached", len(breached))
            for item in breached:
                _log.info(
                    "SLA breached: hitl_item_id=%s tenant=%s priority=%s age_past_deadline=%s",
                    item.hitl_item_id,
                    item.tenant_id,
                    item.priority.value,
                    item.sla_deadline_at,
                )
        finally:
            conn.close()
        return 0
    except Exception:
        _log.exception("SLA enforcement job failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
