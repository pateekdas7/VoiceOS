# Deferred Hardware Tests — Phase 14 Chaos Scenarios

These tests require a real production-equivalent VM with GPU, actual Twilio credentials,
a running PostgreSQL instance, Redis, and the full voice runtime stack.

**Execute after Phase 16 (Production VM provisioned and validated).**

Run with: `python tests/chaos/phase14_productionization_chaos.py`

---

## S1 — Worker Crash Mid-Call

**What to test:** Kill `dialer_worker` process during an active call, restart it, verify
reconciliation promotes stale `INITIATED`/`IN_PROGRESS` attempts to `FAILED` with
`CRASH_INITIATED` or `CRASH_IN_PROGRESS` classification, and writes to `recovery_log`.

**Requires:** Running PostgreSQL with seeded `call_attempts`, live `dialer_worker` process.

**Pass criteria:**
- All `INITIATED`/`IN_PROGRESS` rows from before the crash are updated to `FAILED`
- `recovery_log` has one row per reconciled attempt
- Worker resumes consuming normally after reconciliation completes

---

## S2 — Crash Before active_calls Insert

**What to test:** Inject a fault after `INSERT INTO call_attempts` but before the
`INSERT INTO active_calls`. Verify that on restart, reconciliation detects the orphaned
`INITIATED` attempt and marks it `FAILED`.

**Requires:** PostgreSQL with instrumented dialer_worker (fault injection point).

**Pass criteria:**
- No active call leaked into the queue
- Orphaned `call_attempts` row classified as `CRASH_INITIATED`

---

## S3 — Crash After Twilio Initiation Before active_calls

**What to test:** Inject fault after `twilio.calls.create` returns a `call_sid` but before
`active_calls` INSERT. Verify reconciliation fetches call status from Twilio API, and
classifies accordingly (`CRASH_INITIATED`).

**Requires:** Live Twilio test credentials, PostgreSQL, network access.

**Pass criteria:**
- `call_attempts.call_sid` is populated
- Reconciler contacts Twilio, gets call status
- Row classified per actual Twilio call status

---

## S4 — Forged /dialer/callback

**What to test:** Send a POST to `/dialer/callback` with a valid body but wrong HMAC
signature. Verify 403 is returned and no event is pushed to Redis.

**Requires:** Running bff.js, Redis, valid Twilio test account for correct HMAC generation.

**Pass criteria:**
- Forged request: HTTP 403, no Redis LPUSH
- Genuine request (correct HMAC): HTTP 204, event pushed

---

## S5 — Duplicate Twilio Callback

**What to test:** Send the same `/dialer/callback` payload (same `CallSid` + `CallStatus`)
twice within 30 seconds. Verify only one event is processed.

**Requires:** Running bff.js, Redis with idempotency_keys TTL.

**Pass criteria:**
- Second identical POST returns HTTP 204 but does not push duplicate event
- Redis `LLEN` of the queue increments by 1, not 2

---

## S6 — Redis Restart During Active Call

**What to test:** Kill and restart Redis while a call is in progress. Verify:
1. Voice runtime WebSocket processing continues uninterrupted (Redis not on hot path)
2. On clean shutdown with SIGTERM, `dialer_worker` requeues in-progress leads

**Requires:** Running Redis, voice runtime, dialer_worker, active call in progress.

**Pass criteria:**
- STT/TTS/LLM turns continue without error during Redis outage
- After SIGTERM, in-progress leads are requeued (not dropped)

---

## S7 — PostgreSQL Failure During Post-Call Write

**What to test:** Kill PostgreSQL immediately after call ends (before `_handleCallEnd`
completes). Verify `_handleCallEnd` is awaited and the error surfaces rather than being
silently dropped.

**Requires:** PostgreSQL, dialer_worker, fault injection capability.

**Pass criteria:**
- `_handleCallEnd` throws and the error is logged
- Worker does not silently continue to the next lead after a write failure
- No partial write (either full commit or rollback)

---

## S8 — GPU Timeout During Live Call

**What to test:** Induce GPU OOM or timeout on the STT service during an active call.
Verify:
1. STT circuit breaker opens after threshold
2. Voice runtime plays `clarify_ask_repeat` phrase, not silent drop
3. After 3 consecutive STT failures, call gracefully hangs up

**Requires:** GPU-attached VM, voice runtime, active call, fault injection.

**Pass criteria:**
- Caller hears clarification prompt on first/second failure
- Call ends gracefully after third consecutive failure
- Circuit breaker trips and logs state transition

---

## S9 — Voice Runtime Restart During 5 Concurrent Calls

**What to test:** Send SIGTERM to `voiceos-voice-runtime.service` while 5 calls are active.
Verify drain gate prevents new WS connections, existing calls complete, systemd respects
`TimeoutStopSec`.

**Requires:** Systemd, 5 concurrent test calls, voice runtime with drain gate.

**Pass criteria:**
- No new calls accepted after SIGTERM
- All 5 active calls finish (or are gracefully ended, not abruptly cut)
- Process exits within `TimeoutStopSec`

---

## S10 — MongoDB Unavailable

**What to test:** Kill MongoDB while the voice runtime is handling calls. Verify transcript
writes fail silently (logged, not re-raised), and the conversation turn continues normally.

**Requires:** MongoDB, voice runtime, active call, fault injection.

**Pass criteria:**
- Call continues uninterrupted
- MongoDB failure logged as WARNING/ERROR, not bubbled up
- No crash or unhandled exception in voice runtime process
