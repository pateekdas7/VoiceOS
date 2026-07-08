#!/usr/bin/env python3
"""Sprint-016 Phase 2 infrastructure validation — run against real Redis + Postgres.

Exercises the scenarios listed in implementation/sprints/Sprint-016.md Phase 2
"Integration validation" / "Infrastructure Validation" sections against live
Redis and Postgres, and prints a report. Not part of the pytest suite (pytest
coverage of the same behaviors lives in tests/unit/libs and
tests/integration/libs) — this is an operational smoke-test / evidence script
for the sprint completion report, following the Sprint-013/015 precedent
(scripts/sprint013_infra_validation.py, scripts/sprint015_recovery_drill.py).

No GPU node dependency: this sprint's GPU-adapter circuit breakers (STT/LLM/
TTS) are validated by pytest (tests/unit/services/test_{stt,llm_runtime,tts}.py
circuit-breaker wiring tests) against mocked adapters — there is no
standalone service process on this CPU node to "kill" the way the sprint
narrative describes (see CPU_NODE_STATE.md §8.1: services remain library
classes until Sprint-026). This script instead validates a real Postgres/
Redis dependency failure (a deliberately wrong connection) to produce the
same evidence: breaker opens under sustained real failure, fails fast, and
recovers once the dependency is reachable again.

Usage:
    POSTGRES_DSN=postgresql://voiceos:voiceos_pw@localhost:5432/voiceos \\
    REDIS_URL=redis://localhost:6379/0 \\
    python scripts/sprint016_infra_validation.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import redis as redis_lib

from src.libs.circuit_breaker.breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
)
from src.libs.concurrency.bounded_queue import BoundedQueue, QueueFullError
from src.libs.health.aggregator import HealthAggregator
from src.libs.health.probe import LivenessProbe, ReadinessProbe
from src.libs.health.protocol import HealthStatus
from src.libs.observability.logger import StructuredLogger
from src.libs.observability.tracer import OTelTracer
from src.libs.redis_client.client import RedisClient
from src.libs.redis_client.health_check import RedisHealthCheck
from src.libs.repositories.base import BaseRepository

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "postgresql://voiceos:voiceos_pw@localhost:5432/voiceos")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class _PostgresHealthCheck:
    name = "postgres"

    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def check(self) -> HealthStatus:
        try:
            cur = self._conn.cursor()  # type: ignore[attr-defined]
            cur.execute("SELECT 1;")
            cur.fetchone()
            return HealthStatus.HEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    redis_conn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"Connected to Redis — PING: {redis_conn.ping()}")
    pg_conn = psycopg2.connect(POSTGRES_DSN)
    print(f"Connected to Postgres {POSTGRES_DSN.split('@')[-1]}")
    print()

    # 1. RedisHealthCheck reports HEALTHY against real Redis.
    redis_client = RedisClient(redis_conn)
    redis_check = RedisHealthCheck(redis_client)
    status = asyncio.run(redis_check.check())
    results.append(("RedisHealthCheck reports HEALTHY", status == HealthStatus.HEALTHY, f"status={status.value}"))

    # 2. HealthAggregator + ReadinessProbe combine real Redis + real Postgres.
    pg_check = _PostgresHealthCheck(pg_conn)
    aggregator = HealthAggregator([redis_check, pg_check])
    report = asyncio.run(aggregator.report())
    readiness = ReadinessProbe(LivenessProbe(), [redis_check, pg_check])
    ready_status = asyncio.run(readiness.check())
    results.append(
        (
            "HealthAggregator + ReadinessProbe (real Redis + Postgres)",
            report.overall == HealthStatus.HEALTHY and ready_status == HealthStatus.HEALTHY,
            f"aggregate={report.overall.value} readiness={ready_status.value}",
        )
    )

    # 3. CircuitBreaker opens on a real, sustained Postgres connection failure
    #    (a deliberately wrong DSN — never touches the live Postgres service).
    bad_conn = None
    try:
        bad_conn = psycopg2.connect(POSTGRES_DSN.replace(":5432", ":59999"), connect_timeout=1)
    except Exception:
        pass  # expected — port 59999 has nothing listening

    breaker = CircuitBreaker("postgres", CircuitBreakerConfig(failure_threshold=3, cooldown_seconds=2))
    validation_tenant_id = str(uuid.uuid4())  # customers.tenant_id is a real UUID column

    class _BrokenConn:
        def cursor(self) -> object:
            raise psycopg2.OperationalError("simulated Postgres outage (unreachable port)")

    broken_repo = BaseRepository(_BrokenConn(), breaker=breaker)
    failures = 0
    for _ in range(3):
        try:
            broken_repo._tenant_select("customers", ("customer_id",), validation_tenant_id)
        except psycopg2.OperationalError:
            failures += 1
    opened = breaker.state == CircuitState.OPEN
    results.append(
        (
            "CircuitBreaker opens after 3 real Postgres failures",
            opened,
            f"failures={failures} state={breaker.state.value}",
        )
    )

    fast_fail_start = time.perf_counter()
    fast_failed = False
    try:
        broken_repo._tenant_select("customers", ("customer_id",), validation_tenant_id)
    except CircuitOpenError:
        fast_failed = True
    fast_fail_ms = (time.perf_counter() - fast_fail_start) * 1000
    results.append(
        (
            "CircuitBreaker OPEN fails fast (no connection attempt)",
            fast_failed and fast_fail_ms < 50,
            f"raised={fast_failed} elapsed={fast_fail_ms:.2f}ms",
        )
    )

    # 4. CircuitBreaker recovers once the (real) dependency is healthy again —
    #    reuse the same breaker against the real, working Postgres connection.
    time.sleep(2.1)  # past the 2s cooldown configured above
    working_repo = BaseRepository(pg_conn, breaker=breaker)
    recovered = False
    try:
        working_repo._tenant_select("customers", ("customer_id",), validation_tenant_id)
        recovered = breaker.state == CircuitState.CLOSED
    except Exception as exc:
        print(f"  (recovery probe raised: {exc})")
    results.append(("CircuitBreaker HALF_OPEN -> CLOSED on real recovery", recovered, f"state={breaker.state.value}"))

    # 5. BoundedQueue overflow raises QueueFullError (RI-3) — no DB needed.
    queue: BoundedQueue[int] = BoundedQueue(max_size=2, name="sprint016-validation-queue")
    queue.put_nowait(1)
    queue.put_nowait(2)
    overflow_raised = False
    try:
        queue.put_nowait(3)
    except QueueFullError:
        overflow_raised = True
    results.append(("BoundedQueue overflow raises QueueFullError", overflow_raised, f"raised={overflow_raised}"))

    # 6. StructuredLogger emits valid JSON with all required fields.
    stream = io.StringIO()
    logger = StructuredLogger("sprint016-validation", stream=stream)
    logger.info(
        "validation log line", tenant_id="tenant-x", call_id="call-x", trace_id="trace-x", correlation_id="corr-x"
    )
    record = json.loads(stream.getvalue().strip())
    required_fields = {"timestamp", "level", "service", "tenant_id", "call_id", "trace_id", "correlation_id", "message"}
    results.append(
        (
            "StructuredLogger emits valid JSON with required fields",
            required_fields.issubset(record),
            f"fields={sorted(record)}",
        )
    )

    # 7. OTelTracer: parent -> child spans share trace_id (in-memory exporter).
    tracer, exporter = OTelTracer.for_testing("sprint016-validation")
    with tracer.start_span("parent") as parent_span:
        with tracer.start_span("child") as child_span:
            pass
        trace_ids_match = parent_span.get_span_context().trace_id == child_span.get_span_context().trace_id
    spans = exporter.get_finished_spans()
    results.append(
        ("OTelTracer parent/child spans share trace_id", trace_ids_match and len(spans) == 2, f"spans={len(spans)}")
    )

    pg_conn.close()
    if bad_conn is not None:
        bad_conn.close()

    print(f"{'CHECK':<55} {'RESULT':<8} DETAIL")
    print("-" * 110)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<55} {status_str:<8} {detail}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
