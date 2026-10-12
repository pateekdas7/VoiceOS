# Phase 14 — Chaos and Failure Testing Runbook

**Execute after Phase 16 on a production-equivalent VM.**

---

## Prerequisites

1. All Phase 1–16 services running: `bff.js`, `dialer_worker`, voice runtime, PostgreSQL, Redis
2. Twilio test credentials configured: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
3. MongoDB running (S10 only — optional)
4. Source test passes: `node tests/unit/bff/test_phase14_source.js` → 26/26

---

## S1 — Worker Crash Mid-Call

**Trigger:** `kill -9 $(pgrep -f dialer_worker)` during an active call

**Expected:**
- Worker restarts, `Reconciler.reconcile()` runs before `_consumerLoop`
- All `INITIATED`/`IN_PROGRESS` attempts from before the crash → `FAILED`
- `recovery_log` has one row per reconciled attempt

**Verify:**
```sql
SELECT status, failure_class, created_at
FROM recovery_log
ORDER BY created_at DESC
LIMIT 10;
```

**Pass criteria:** All stale attempts reconciled within 30s of restart.

---

## S2 — Crash Before active_calls Insert

**Trigger:** Seed an `INITIATED` `call_attempts` row with no matching `active_calls` row, then restart worker.

**Expected:**
- Reconciler detects orphaned `INITIATED` row
- Classifies as `CRASH_INITIATED`, sets `status = 'FAILED'`
- Entry written to `recovery_log`

**Verify:**
```sql
SELECT ca.status, rl.failure_class
FROM call_attempts ca
JOIN recovery_log rl ON rl.call_attempt_id = ca.call_attempt_id
WHERE ca.call_attempt_id = '<seeded_id>';
```

**Pass criteria:** `status = 'FAILED'`, `failure_class = 'CRASH_INITIATED'`.

---

## S3 — Crash After Twilio Initiation Before active_calls

**Trigger:** Seed an `INITIATED` `call_attempts` row with a `call_sid` but no `active_calls` row.

**Expected:**
- Reconciler detects `call_sid` is populated
- Contacts Twilio API for actual call status (or classifies as `CRASH_INITIATED`)
- Marks attempt `FAILED`

**Verify:** Same SQL as S2. `failure_class` should be `CRASH_INITIATED`.

---

## S4 — Forged /dialer/callback

**Trigger:**
```bash
curl -X POST http://localhost:8000/dialer/callback \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "X-Twilio-Signature: FORGED" \
  -d "CallSid=CAtest&CallStatus=completed"
```

**Expected:** HTTP 403, no Redis LPUSH.

**Verify Redis:**
```bash
redis-cli LLEN dialer_callbacks  # should not increase
```

**Pass criteria:** 403 returned, queue length unchanged.

---

## S5 — Duplicate Twilio Callback

**Trigger:** Send the same valid callback twice within the idempotency TTL (30s).

**Expected:** Both return HTTP 200/204, but queue length increments by 1 (not 2).

**Verify:**
```bash
redis-cli LLEN dialer_callbacks  # note before/after
```

**Pass criteria:** Queue increments by exactly 1 after two identical requests.

---

## S6 — Redis Restart During Active Call

**Trigger:** `systemctl restart redis` during a live call.

**Expected:**
1. Voice runtime (WebSocket) continues processing — Redis not on turn hot path
2. `dialer_worker` reconnects automatically
3. SIGTERM requeues in-progress leads

**Verify:** Call completes normally. Check `journalctl -u voiceos-dialer-worker` for reconnect messages.

**Pass criteria:** No call dropout, no unhandled exception in voice runtime.

---

## S7 — PostgreSQL Failure During Post-Call Write

**Trigger:** `systemctl stop postgresql` immediately after a call ends.

**Expected:**
- `_handleCallEnd` throws an error (not silently swallowed)
- Error logged with `logger.error` or equivalent
- Worker does not silently proceed to next lead without recording the failure

**Verify:**
```bash
journalctl -u voiceos-dialer-worker -n 100 | grep -i "error\|ECONNREFUSED"
```

**Pass criteria:** Error is surfaced in logs, not dropped. No partial write without error.

---

## S8 — GPU Timeout During Live Call

**Trigger:** Saturate GPU memory or inject artificial timeout in STT adapter.

**Expected:**
1. STT circuit breaker opens after threshold (typically 3–5 failures)
2. First/second STT failures: caller hears `clarify_ask_repeat` phrase
3. Third consecutive failure: graceful hangup

**Verify:**
```bash
curl http://localhost:8002/health | python3 -m json.tool | grep -A5 circuit_breakers
```

**Pass criteria:** Breaker state transitions to OPEN, call ends gracefully (no abrupt disconnect).

---

## S9 — Voice Runtime Restart During 5 Concurrent Calls

**Trigger:** `systemctl stop voiceos-voice-runtime` while 5 test calls are active.

**Expected:**
1. Drain gate prevents new WebSocket connections immediately
2. Existing 5 calls continue until they finish (or are gracefully ended)
3. Process exits cleanly within `TimeoutStopSec` seconds

**Verify:**
```bash
# After SIGTERM, new WS connection should be rejected
wscat -c ws://localhost:8002/ws/media  # expect immediate close
```

**Pass criteria:** No new connections accepted post-SIGTERM, existing calls not abruptly cut.

---

## S10 — MongoDB Unavailable

**Trigger:** `systemctl stop mongod` during active calls.

**Expected:**
1. Voice runtime continues handling turns without interruption
2. MongoDB failure logged as WARNING/ERROR — not re-raised
3. No crash or unhandled exception

**Verify:**
```bash
journalctl -u voiceos-voice-runtime -n 100 | grep -i mongo
curl http://localhost:8002/health  # should still return 200
```

**Pass criteria:** Voice runtime healthy, MongoDB errors logged but not fatal.

---

## Automated Execution

```bash
# Dry run (safe — no destructive commands)
python tests/chaos/phase14_productionization_chaos.py

# Run all scenarios (DESTRUCTIVE — production-equivalent VM only)
python tests/chaos/phase14_productionization_chaos.py --execute

# Run single scenario
python tests/chaos/phase14_productionization_chaos.py --execute --scenario S4
```

---

## Rollback Checklist

After any scenario that stops a service, verify all are running:

```bash
systemctl is-active voiceos-dialer-worker voiceos-voice-runtime redis postgresql
```

If any are stopped:
```bash
systemctl start postgresql redis voiceos-dialer-worker voiceos-voice-runtime
```
