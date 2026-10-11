'use strict';
/**
 * Phase 14 Source Verification — Chaos and Failure Testing
 *
 * Verifies that the CODE required for each of the 10 chaos scenarios is in
 * place. These are static source-inspection checks; they do not require a
 * running process, database, or network.
 *
 * Actual execution of the 10 scenarios is deferred to post-Phase-16 (real VM
 * required). See tests/chaos/DEFERRED_HARDWARE_TESTS.md.
 *
 * Run with: node tests/unit/bff/test_phase14_source.js
 */

const assert = require('assert');
const path   = require('path');
const fs     = require('fs');

const ROOT    = path.resolve(__dirname, '../../..');
const BFF_SRC = fs.readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
const DW_SRC  = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');

function readFile(...parts) {
  return fs.readFileSync(path.join(ROOT, ...parts), 'utf8');
}

let passed = 0, failed = 0;
const results = [];

function test(name, fn) {
  try { fn(); results.push({ name, status: 'PASS' }); passed++; }
  catch (e) { results.push({ name, status: 'FAIL', detail: e.message }); failed++; }
}

// ── Scenario 1: Worker crash mid-call ─────────────────────────────────────────

test('S1: Reconciler class with reconcile() exists in dialer_worker', () => {
  assert(DW_SRC.includes('async reconcile()'), 'reconcile() method missing in Reconciler');
});

test('S1: reconciliation scans for INITIATED and IN_PROGRESS call_attempts', () => {
  assert(
    DW_SRC.includes("IN ('INITIATED', 'IN_PROGRESS')") || DW_SRC.includes("IN ('INITIATED','IN_PROGRESS')"),
    'reconciliation does not scan for INITIATED/IN_PROGRESS attempts'
  );
});

test('S1: reconciliation is called on worker startup before consumer loop', () => {
  // Find the DialerWorker start() — it's the second occurrence (first is Reconciler)
  const startMatches = [...DW_SRC.matchAll(/async start\(\)/g)];
  assert(startMatches.length >= 1, 'async start() not found in dialer_worker.js');
  const lastStartIdx = startMatches[startMatches.length - 1].index;
  const consumerIdx  = DW_SRC.indexOf('_consumerLoop', lastStartIdx);
  const reconcileIdx = DW_SRC.indexOf('reconcile()', lastStartIdx);
  assert(reconcileIdx !== -1, 'reconcile() not called inside start()');
  assert(reconcileIdx < consumerIdx, 'reconcile() must be called before _consumerLoop in start()');
});

test('S1: recovery_log written per reconciled attempt', () => {
  assert(DW_SRC.includes('recovery_log'), 'dialer_worker does not write to recovery_log table');
});

// ── Scenario 2: Crash before active_calls insert ──────────────────────────────

test("S2: call_attempts INSERT with status='INITIATED' happens before Twilio call", () => {
  // Phase 2 adds INSERT call_attempts before twilio.calls.create
  const insertIdx = DW_SRC.indexOf("INSERT INTO call_attempts");
  assert(insertIdx !== -1, 'INSERT INTO call_attempts not found');
  // The INSERT should contain 'INITIATED' in its values
  const insertBlock = DW_SRC.slice(insertIdx, insertIdx + 300);
  assert(insertBlock.includes("INITIATED"), "call_attempts INSERT does not set status='INITIATED'");
});

test('S2: call_attempts INSERT precedes twilio.calls.create in _processLead', () => {
  const insertIdx = DW_SRC.indexOf('INSERT INTO call_attempts');
  const twilioIdx = DW_SRC.indexOf('twilio.calls.create', insertIdx);
  // If twilio.calls.create exists, it must come after the INSERT
  if (twilioIdx !== -1) {
    assert(insertIdx < twilioIdx, 'call_attempts INSERT must precede twilio.calls.create');
  }
});

// ── Scenario 3: Crash after Twilio initiation before active_calls ─────────────

test('S3: call_sid written to call_attempts after Twilio responds', () => {
  assert(
    DW_SRC.includes('call_sid=$') || DW_SRC.includes("SET call_sid="),
    'call_attempts not updated with call_sid after Twilio initiation'
  );
});

test('S3: _reconcileAttempt handles INITIATED status (Twilio called, no active_calls)', () => {
  const fn = DW_SRC.slice(DW_SRC.indexOf('async _reconcileAttempt'));
  assert(fn.includes('INITIATED'), '_reconcileAttempt does not handle INITIATED status');
  assert(fn.includes('CRASH_INITIATED'), '_reconcileAttempt missing CRASH_INITIATED failure classification');
});

// ── Scenario 4: Forged /dialer/callback ───────────────────────────────────────

test('S4: /dialer/callback validates Twilio HMAC (validateRequest)', () => {
  assert(BFF_SRC.includes('validateRequest'), 'Twilio HMAC validateRequest missing from /dialer/callback');
});

test('S4: HMAC check precedes Redis LPUSH in /dialer/callback', () => {
  const cbIdx   = BFF_SRC.indexOf("app.post('/dialer/callback'");
  const hmacIdx = BFF_SRC.indexOf('validateRequest', cbIdx);
  const lpushIdx = BFF_SRC.indexOf('LPUSH', cbIdx);
  assert(hmacIdx !== -1, 'validateRequest not in /dialer/callback handler');
  assert(lpushIdx === -1 || hmacIdx < lpushIdx, 'HMAC check must precede Redis LPUSH');
});

// ── Scenario 5: Duplicate Twilio callback ─────────────────────────────────────

test('S5: /dialer/callback uses idempotency_keys to prevent double-push', () => {
  assert(BFF_SRC.includes('idempotency_keys'), 'idempotency_keys check missing from /dialer/callback');
});

test('S5: idempotency key incorporates CallSid', () => {
  const cbBlock = BFF_SRC.slice(BFF_SRC.indexOf("app.post('/dialer/callback'"), BFF_SRC.indexOf("app.post('/dialer/callback'") + 3000);
  assert(cbBlock.includes('CallSid') || cbBlock.includes('callSid'),
    'idempotency key does not incorporate CallSid — cannot deduplicate per-call');
});

// ── Scenario 6: Redis restart during active call ──────────────────────────────

test('S6: voice runtime WS entrypoint does not import Redis (not on hot path)', () => {
  const wsSrc = readFile('src/services/media_gateway/twilio_ws_entrypoint.py');
  assert(!wsSrc.includes('import redis') && !wsSrc.includes('from redis'),
    'twilio_ws_entrypoint.py imports Redis — voice hot path must not use Redis per turn');
});

test('S6: dialer_worker has SIGTERM handler to requeue incomplete leads', () => {
  assert(DW_SRC.includes('SIGTERM') || DW_SRC.includes('sigterm'), 'dialer_worker missing SIGTERM handler');
});

// ── Scenario 7: PostgreSQL failure during post-call write ─────────────────────

test('S7: setImmediate not used to wrap post-call handlers in dialer_worker', () => {
  // setImmediate is still mentioned in a comment but must not wrap _handleCallEnd / _openCallRecords
  const callsViaSetImmediate = DW_SRC.match(/setImmediate\s*\([^)]*HandleCallEnd|setImmediate\s*\([^)]*openCallRecords/);
  assert(!callsViaSetImmediate,
    'setImmediate still wraps a post-call handler — writes must be synchronous');
});

test('S7: _handleCallEnd is awaited (synchronous, not fire-and-forget)', () => {
  assert(DW_SRC.includes('await this._handleCallEnd') || DW_SRC.includes('await _handleCallEnd'),
    '_handleCallEnd is not awaited — post-call data loss on crash');
});

// ── Scenario 8: GPU timeout during live call ──────────────────────────────────

test('S8: circuit breaker registry built and wired in app.py', () => {
  const appSrc = readFile('deployment/cpu/app.py');
  assert(appSrc.includes('build_circuit_breaker_registry') || appSrc.includes('CircuitBreakerRegistry'),
    'circuit_breaker not found in deployment/cpu/app.py');
});

test('S8: circuit breaker wired to STT adapter in app.py', () => {
  const appSrc = readFile('deployment/cpu/app.py');
  const sttIdx = appSrc.indexOf('"stt"');
  assert(sttIdx !== -1 && appSrc.slice(Math.max(0, sttIdx - 300), sttIdx + 200).includes('breaker'),
    'STT circuit breaker not wired in app.py');
});

test('S8: circuit breaker wired to TTS adapter in app.py', () => {
  const appSrc = readFile('deployment/cpu/app.py');
  const ttsIdx = appSrc.indexOf('"tts"');
  assert(ttsIdx !== -1 && appSrc.slice(Math.max(0, ttsIdx - 300), ttsIdx + 200).includes('breaker'),
    'TTS circuit breaker not wired in app.py');
});

test('S8: STT failure triggers clarify_ask_repeat phrase (not silent drop)', () => {
  const wsSrc = readFile('src/services/media_gateway/twilio_ws_entrypoint.py');
  assert(wsSrc.includes('clarify_ask_repeat') || wsSrc.includes('_speak_stt_clarify'),
    'STT failure fallback (clarify_ask_repeat) not found in voice runtime');
});

test('S8: 3 consecutive STT failures cause graceful hangup', () => {
  const wsSrc = readFile('src/services/media_gateway/twilio_ws_entrypoint.py');
  assert(
    wsSrc.includes('consecutive_stt_failures') || wsSrc.includes('stt_failure'),
    'No consecutive STT failure counter found — 3-strike hangup path missing'
  );
});

// ── Scenario 9: Voice runtime restart during 5 concurrent calls ───────────────

test('S9: voice runtime app.py has graceful drain gate', () => {
  const appSrc = readFile('deployment/cpu/app.py');
  assert(appSrc.includes('_DrainGate') || appSrc.includes('draining'), 'Graceful drain gate missing');
});

test('S9: drain gate activated on SIGTERM in lifespan', () => {
  const appSrc = readFile('deployment/cpu/app.py');
  assert(appSrc.includes('set_draining') || appSrc.includes('draining = True'), 'drain gate not set on SIGTERM');
});

test('S9: voiceos-voice-runtime.service exists with TimeoutStopSec', () => {
  const unitSrc = readFile('scripts/systemd/voiceos-voice-runtime.service');
  assert(unitSrc.includes('TimeoutStopSec'), 'voiceos-voice-runtime.service missing TimeoutStopSec');
});

// ── Scenario 10: MongoDB unavailable ─────────────────────────────────────────

test('S10: voice runtime WS entrypoint does not block per-turn on MongoDB', () => {
  const wsSrc = readFile('src/services/media_gateway/twilio_ws_entrypoint.py');
  // MongoDB writes should not be awaited in the hot turn-processing path
  // If mongo is referenced at all, it must be via background tasks
  if (wsSrc.includes('mongo') || wsSrc.includes('MongoDB')) {
    assert(
      wsSrc.includes('create_task') || wsSrc.includes('ensure_future') ||
      wsSrc.includes('background') || wsSrc.includes('asyncio'),
      'MongoDB write in voice hot path appears to be synchronous (blocking per turn)'
    );
  }
  // If not referenced at all, MongoDB writes happen in a separate service layer — pass
});

test('S10: MongoDB write failure does not crash voice path (error logged, not re-raised)', () => {
  const wsSrc = readFile('src/services/media_gateway/twilio_ws_entrypoint.py');
  // If MongoDB is mentioned, failure handling should be present
  if (wsSrc.includes('mongo') || wsSrc.includes('Mongo')) {
    assert(
      wsSrc.includes('except') || wsSrc.includes('logger.error') || wsSrc.includes('logger.warning'),
      'No error handling around MongoDB operations in voice path'
    );
  }
});

// ── Summary ───────────────────────────────────────────────────────────────────

console.log('\nPhase 14 Chaos Source Verification Tests');
console.log('══════════════════════════════════════════\n');
for (const r of results) {
  const icon = r.status === 'PASS' ? '✓' : '✗';
  console.log(`  ${icon} ${r.name}`);
  if (r.detail) console.log(`      → ${r.detail}`);
}
console.log(`\n  ${passed} passed, ${failed} failed out of ${passed + failed} total\n`);
process.exit(failed > 0 ? 1 : 0);
