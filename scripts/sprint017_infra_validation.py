#!/usr/bin/env python3
"""Sprint-017 Phase 2 infrastructure validation — run against real Redis + Postgres.

Exercises the scenarios listed in implementation/sprints/Sprint-017.md Phase 2
"Integration validation" / "Infrastructure Validation" sections against live
Redis and Postgres, and prints a report. Not part of the pytest suite (pytest
coverage of the same behaviors lives in tests/unit/services/test_policy_engine.py
and tests/integration/services/test_policy_engine_integration.py) — this is an
operational smoke-test / evidence script for the sprint completion report,
following the Sprint-013/015/016 precedent.

No GPU node dependency — the Policy Engine has no GPU-adapter surface.

Usage:
    POSTGRES_DSN=postgresql://voiceos:voiceos_pw@localhost:5432/voiceos \\
    REDIS_URL=redis://localhost:6379/0 \\
    python scripts/sprint017_infra_validation.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import redis as redis_lib

from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.libs.repositories.policy import PolicyRepository
from src.services.policy_engine.break_glass import BreakGlassDirective
from src.services.policy_engine.decision import PolicyOutcome
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.packs import RBIPolicyPack
from src.services.policy_engine.rule import PolicyCondition, PolicyEffect, PolicyRequest, PolicyRule

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "postgresql://voiceos:voiceos_pw@localhost:5432/voiceos")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class _CountingPolicyRepository:
    """Wraps a real PolicyRepository, counting every Postgres load — proves
    the Redis cache tier serves repeat evaluations without hitting Postgres."""

    def __init__(self, repository: PolicyRepository) -> None:
        self._repository = repository
        self.load_count = 0

    def load_active_rule_ids(self, scope: str, scope_id: str | None) -> tuple[str, ...]:
        self.load_count += 1
        return self._repository.load_active_rule_ids(scope, scope_id)


class _StaticRuleRepository:
    """In-memory scope -> rule_ids table, for the deny-override scenario."""

    def __init__(self, table: dict[tuple[str, str | None], tuple[str, ...]]) -> None:
        self._table = table

    def load_active_rule_ids(self, scope: str, scope_id: str | None) -> tuple[str, ...]:
        return self._table.get((scope, scope_id), ())


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"Connected to Redis — PING: {redis_conn.ping()}")
    pg_conn = psycopg2.connect(POSTGRES_DSN)
    print(f"Connected to Postgres {POSTGRES_DSN.split('@')[-1]}")
    print()

    real_repository = PolicyRepository(pg_conn)
    for rule in RBIPolicyPack.rules():
        real_repository.upsert_policy(rule.rule_id, rule.pack, scope="global")
    counting_repository = _CountingPolicyRepository(real_repository)

    bus = EventBus(redis_conn, stream="voiceos-events")
    publisher = Publisher(bus)
    engine = PolicyEngine(redis=redis_conn, policy_repository=counting_repository, publisher=publisher)

    tenant_id = str(uuid.uuid4())

    # 1. RBI calling hours: real API call at simulated 21:00 -> DENY.
    call_id_1 = f"validation-{uuid.uuid4()}"
    deny_request = PolicyRequest(
        domain="rbi",
        action="admit_call",
        subject="infra-validation",
        resource=call_id_1,
        tenant_id=tenant_id,
        context={"hour": 21, "call_id": call_id_1},
    )
    deny_decision = engine.evaluate(deny_request)
    results.append(
        (
            "RBI calling hours: 21:00 -> DENY (real Postgres + Redis)",
            deny_decision.outcome == PolicyOutcome.DENY,
            f"outcome={deny_decision.outcome.value}",
        )
    )

    # 2. Audit event: PolicyDecisionMade appears in EventBus for the DENY decision.
    replayed = [e for _id, e in bus.replay_from() if e.payload.get("call_id") == call_id_1]
    results.append(
        (
            "PolicyDecisionMade audit event emitted for DENY (real EventBus)",
            len(replayed) == 1 and replayed[0].payload["decision"] == "DENY",
            f"events_found={len(replayed)}",
        )
    )

    # 3. Redis rule caching: repeat evaluations don't re-query Postgres.
    loads_before = counting_repository.load_count
    for _ in range(5):
        engine.evaluate(deny_request)
    extra_loads = counting_repository.load_count - loads_before
    results.append(
        (
            "Policy rule-set served from Redis cache on repeat evaluation",
            extra_loads == 0,
            f"extra_postgres_loads={extra_loads}",
        )
    )

    # 4. Deny-override: tenant-level PERMIT + global-level DENY -> DENY (real Redis).
    #
    # The "global" scope's cache key (policy:ruleset:global:global) is fixed —
    # it does not vary by tenant_id or domain — so it was already warmed by
    # check #1 above with the *real* seeded RBI rule_ids. Reusing the same
    # redis_conn here without clearing that key would silently serve this
    # scenario's synthetic global rule from the stale real-rule cache entry
    # instead of ever calling override_repo (found running this script
    # against real Redis — invisible with FakeRedisClient in Phase 1, where
    # each unit test gets its own fresh fake instance).
    redis_conn.delete("policy:ruleset:global:global")

    always_true = PolicyCondition("always", lambda _r: True)
    tenant_permit = PolicyRule(
        "VALIDATION-TENANT-PERMIT", "test", "validation_domain", always_true, PolicyEffect(PolicyOutcome.PERMIT)
    )
    global_deny = PolicyRule(
        "VALIDATION-GLOBAL-DENY", "test", "validation_domain", always_true, PolicyEffect(PolicyOutcome.DENY)
    )
    override_repo = _StaticRuleRepository(
        {("global", None): (global_deny.rule_id,), ("tenant", tenant_id): (tenant_permit.rule_id,)}
    )
    override_engine = PolicyEngine(redis=redis_conn, policy_repository=override_repo)
    override_engine.registry[tenant_permit.rule_id] = tenant_permit
    override_engine.registry[global_deny.rule_id] = global_deny
    override_decision = override_engine.evaluate(
        PolicyRequest(domain="validation_domain", action="check", subject="s", resource="r", tenant_id=tenant_id)
    )
    results.append(
        (
            "Deny-override: tenant PERMIT + global DENY -> DENY (real Redis)",
            override_decision.outcome == PolicyOutcome.DENY,
            f"outcome={override_decision.outcome.value}",
        )
    )

    # 5. Break-glass: supervisor override -> PERMIT + mandatory audit event.
    directive = BreakGlassDirective(
        rule_id="RBI-CALLING-HOURS", reason="infra validation", requested_by="validator", approvers=("s1", "s2")
    )
    bg_request = PolicyRequest(
        domain="rbi", action="admit_call", subject="validator", resource="bg-call", tenant_id=tenant_id
    )
    bg_decision = engine.emergency_override(directive, bg_request)
    bg_events = [e for _id, e in bus.replay_from() if e.payload.get("rule_id", "").startswith("BREAK-GLASS")]
    results.append(
        (
            "Break-glass override -> PERMIT with mandatory audit event",
            bg_decision.outcome == PolicyOutcome.PERMIT and len(bg_events) >= 1,
            f"outcome={bg_decision.outcome.value} audit_events={len(bg_events)}",
        )
    )

    # 6. Policy evaluation latency: p99 < 10ms (cached rules).
    permit_request = PolicyRequest(
        domain="rbi",
        action="admit_call",
        subject="infra-validation",
        resource="latency-check",
        tenant_id=tenant_id,
        context={"hour": 10},
    )
    engine.evaluate(permit_request)  # warm
    durations_ms: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        engine.evaluate(permit_request)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    p99 = durations_ms[int(len(durations_ms) * 0.99) - 1]
    results.append(("Policy evaluation p99 < 10ms (cached rules)", p99 < 10.0, f"p99={p99:.3f}ms"))

    pg_conn.close()

    print(f"{'CHECK':<65} {'RESULT':<8} DETAIL")
    print("-" * 110)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<65} {status_str:<8} {detail}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
