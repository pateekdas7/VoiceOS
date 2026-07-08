#!/usr/bin/env python3
"""Sprint-015 idempotency validation — N concurrent callers, real Postgres.

Referenced by implementation/sprints/Sprint-015.md's DR Validation section:

    python3 scripts/validate/idempotency_test.py --concurrent 10
    # Expected: exactly 1 record in Postgres

Spawns ``--concurrent`` OS threads, each with its own psycopg2 connection,
all calling ``IdempotencyGuard.execute_once()`` with the same key. Exactly
one thread's effect_fn must execute; the rest must receive the cached result.

This script reads the DB credential from the POSTGRES_DSN environment
variable — it is never hardcoded here. Export it in your own shell before
running (it is a non-secret dev-only credential per CPU_NODE_STATE.md, but
this script does not assume or print its value):

    export POSTGRES_DSN='postgresql://voiceos:voiceos_pw@localhost:5432/voiceos'
    python3 scripts/validate/idempotency_test.py --concurrent 10
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.libs.contracts.primitives import TenantId
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.repositories.idempotency import IdempotencyRepository

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrent", type=int, default=10, help="Number of concurrent callers")
    args = parser.parse_args()

    if not POSTGRES_DSN:
        print("ERROR: POSTGRES_DSN is not set. Export it and re-run.", file=sys.stderr)
        return 1

    import psycopg2

    tenant_id = TenantId(str(uuid.uuid4()))
    key = f"validation-key-{uuid.uuid4()}"

    call_count = 0
    count_lock = threading.Lock()

    def run_once(_index: int) -> dict[str, int]:
        conn = psycopg2.connect(POSTGRES_DSN)
        try:
            repo = IdempotencyRepository(conn)
            guard = IdempotencyGuard(repo, poll_interval_seconds=0.02, poll_timeout_seconds=10.0)

            async def effect_fn() -> dict[str, int]:
                nonlocal call_count
                with count_lock:
                    call_count += 1
                return {"n": 1}

            return asyncio.run(guard.execute_once(tenant_id, key, "validation", effect_fn))
        finally:
            conn.close()

    print(f"Spawning {args.concurrent} concurrent callers with idempotency key={key!r} ...")
    with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
        results = list(pool.map(run_once, range(args.concurrent)))

    conn = psycopg2.connect(POSTGRES_DSN)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM idempotency_keys WHERE key = %s", (key,))
        (record_count,) = cur.fetchone()
        cur.execute("DELETE FROM idempotency_keys WHERE key = %s", (key,))
        conn.commit()
    finally:
        conn.close()

    print(f"effect_fn executed {call_count} time(s) (expected: 1)")
    print(f"Postgres record count for this key: {record_count} (expected: 1)")
    print(f"All {len(results)} callers received the same result: {len(set(map(str, results))) == 1}")

    ok = call_count == 1 and record_count == 1 and len(set(map(str, results))) == 1
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
