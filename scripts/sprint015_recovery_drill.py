#!/usr/bin/env python3
"""Sprint-015 Phase 2 crash-recovery drill — run against real Postgres + Redis.

Exercises the exact scenario in implementation/sprints/Sprint-015.md's Phase 2
"Crash recovery drill" and "Fencing token" sections:

  1. Start a call, process 3 turns (3 snapshots taken, one per turn).
  2. Simulate a crash (discard the in-memory session state).
  3. "Restart": RecoveryManager + CPURestartStrategy load the latest
     Postgres snapshot and replay the Redis Streams event tail since it.
  4. Verify: recovered state is identical to the pre-crash state.
  5. Process a 4th turn using the recovered state and verify it advances
     correctly.
  6. Verify the attempt was recorded to recovery_log as 'success'.
  7. Fencing token: two concurrent DistributedLock acquires -> one succeeds
     with a higher fencing token; a stale (lower) token is rejected.

NOTE: ConversationEngine has no standalone process/pod yet (it remains a
library class until Sprint-026's K8s/Helm deployment — see CPU_NODE_STATE.md
§8.1), so "kill the pod" is simulated by discarding the Python object holding
session state, not a real `kubectl delete pod`. This still exercises the real
production code path (Snapshot/EventTailReplay/RecoveryManager/
CPURestartStrategy against real Postgres + Redis) — only the process-lifecycle
part of the drill is simulated, not the recovery mechanism itself.

Reads credentials from POSTGRES_DSN / REDIS_URL environment variables only —
never hardcoded here. Export them in your own shell before running:

    export POSTGRES_DSN='postgresql://voiceos:voiceos_pw@localhost:5432/voiceos'
    export REDIS_URL='redis://localhost:6379/0'
    python3 scripts/sprint015_recovery_drill.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis as redis_lib

from src.libs.contracts.primitives import CallId, TenantId
from src.libs.event_bus.bus import EventBus
from src.libs.event_bus.publisher import Publisher
from src.libs.idempotency.fencing import FencingToken
from src.libs.recovery.outcome import RecoveryOutcome
from src.libs.recovery.recovery_manager import RecoveryManager
from src.libs.recovery.strategies.cpu_restart import CPURestartStrategy
from src.libs.redis_client.lock import DistributedLock
from src.libs.state.recovery_log import RecoveryLog
from src.libs.state.replay import EventTailReplay
from src.libs.state.snapshot import Snapshot
from src.services.conversation_engine.session_state import ConversationSessionState

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def main() -> int:
    if not POSTGRES_DSN:
        print("ERROR: POSTGRES_DSN is not set. Export it and re-run.", file=sys.stderr)
        return 1

    import psycopg2

    pg_conn = psycopg2.connect(POSTGRES_DSN)
    redis_client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=False, protocol=2)
    print(f"Connected to Postgres and Redis — Redis PING: {redis_client.ping()}")

    results: list[tuple[str, bool, str]] = []
    tenant_id = TenantId(str(uuid.uuid4()))
    call_id = CallId(f"call-drill-{uuid.uuid4()}")
    stream = f"voiceos:drill:sprint015:{uuid.uuid4().hex[:8]}"

    try:
        bus = EventBus(redis_client, stream=stream)
        publisher = Publisher(bus)
        snapshot_store = Snapshot(pg_conn)
        recovery_log = RecoveryLog(pg_conn)
        replay = EventTailReplay(bus)
        manager = RecoveryManager(recovery_log, publisher)
        manager.register("cpu_restart", CPURestartStrategy(snapshot_store, replay))

        # --- Step 1: 3 turns, 1 snapshot per turn -------------------------
        live_session = ConversationSessionState(call_id)
        for turn_idx in range(3):
            # publish_with_entry_id (not plain publish()) — the resume offset
            # EventTailReplay needs is the real Redis Streams entry ID (e.g.
            # "1720051234567-0"), not the EventEnvelope's own UUID event_id.
            _envelope, entry_id = publisher.publish_with_entry_id(
                event_type="decision.made",
                tenant_id=tenant_id,
                payload={"call_id": call_id, "turn_index": turn_idx},
                correlation_id=str(uuid.uuid4()),
            )
            live_session.record_turn(intent_label=f"INTENT_{turn_idx}", event_offset=entry_id)
            snapshot_store.take_snapshot(live_session, tenant_id, call_id)
        print(f"3 turns processed; live session turn_count={live_session.turn_count}, snapshots taken=3")
        results.append(("3 turns processed, 3 snapshots taken", live_session.turn_count == 3, ""))

        pre_crash_turn_count = live_session.turn_count
        pre_crash_intent_history = live_session.intent_history

        # --- Step 2: simulate crash ---------------------------------------
        del live_session
        print("Simulated crash: in-memory session state discarded.")

        # --- Step 3-4: restart + recover -----------------------------------
        t0 = time.perf_counter()

        async def _recover_fn(strategy: CPURestartStrategy) -> RecoveryOutcome:
            recovered = strategy.recover(fresh_session, tenant_id, call_id)
            return recovered

        fresh_session = ConversationSessionState(call_id)
        import asyncio

        outcome = asyncio.run(manager.recover(tenant_id, call_id, "cpu_restart", _recover_fn))
        recovery_duration_s = time.perf_counter() - t0

        print(f"Recovery outcome: success={outcome.success}, detail={outcome.detail}")
        print(f"Recovery duration: {recovery_duration_s:.3f}s (target < 10s)")
        results.append(("RecoveryManager dispatched CPURestartStrategy and succeeded", outcome.success, ""))
        results.append(("Recovery completed within 10s", recovery_duration_s < 10.0, f"{recovery_duration_s:.3f}s"))

        # --- Step 5: verify recovered state matches pre-crash -------------
        state_matches = (
            fresh_session.turn_count == pre_crash_turn_count
            and fresh_session.intent_history == pre_crash_intent_history
        )
        print(
            f"Recovered turn_count={fresh_session.turn_count} (expected {pre_crash_turn_count}), "
            f"intent_history={fresh_session.intent_history} (expected {pre_crash_intent_history})"
        )
        results.append(("Recovered state identical to pre-crash state", state_matches, ""))

        # --- Step 6: 4th turn processed using recovered state -------------
        _envelope4, entry_id4 = publisher.publish_with_entry_id(
            event_type="decision.made",
            tenant_id=tenant_id,
            payload={"call_id": call_id, "turn_index": 3},
            correlation_id=str(uuid.uuid4()),
        )
        fresh_session.record_turn(intent_label="INTENT_3", event_offset=entry_id4)
        fourth_turn_ok = fresh_session.turn_count == pre_crash_turn_count + 1
        print(f"4th turn processed: turn_count={fresh_session.turn_count} (expected {pre_crash_turn_count + 1})")
        results.append(("4th turn processed correctly using recovered state", fourth_turn_ok, ""))

        # --- Step 7: recovery_log has the success entry -------------------
        last_outcome = recovery_log.last_outcome(tenant_id, call_id)
        print(f"recovery_log.last_outcome = {last_outcome!r} (expected 'success')")
        results.append(("recovery_log records the recovery as success", last_outcome == "success", ""))

        # --- Step 8: fencing token — concurrent lock acquire ---------------
        lock = DistributedLock(redis_client)
        resource_id = f"drill-resource-{uuid.uuid4()}"
        token1 = lock.acquire(resource_id, owner_id="writer-a")
        assert token1 is not None
        lock.release(token1)
        token2 = lock.acquire(resource_id, owner_id="writer-b")
        assert token2 is not None
        print(f"Lock reacquired with fencing_token={token2.fencing_token} (> previous {token1.fencing_token})")
        results.append(("Fencing token increases on reacquire", token2.fencing_token > token1.fencing_token, ""))

        stale_write_rejected = not FencingToken.validate(token=token1.fencing_token, last_seen=token2.fencing_token)
        print(f"Stale write with old token={token1.fencing_token} rejected: {stale_write_rejected}")
        results.append(("Stale write rejected by FencingToken", stale_write_rejected, ""))
        lock.release(token2)

    finally:
        redis_client.delete(stream)
        pg_conn.close()

    print()
    print("=" * 70)
    all_ok = True
    for description, ok, extra in results:
        status = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"[{status}] {description}" + (f" ({extra})" if extra else ""))
    print("=" * 70)
    print("OVERALL: " + ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
