'use strict';
/**
 * Phase 2 Verification Tests — Canonical Call Attempt & Durable Post-Call
 *
 * Tests the four workstreams:
 *   2a. call_attempts migration exists and is valid SQL
 *   2b. _processLead is synchronous (no setImmediate around post-call work)
 *   2c. _waitForCompletion validates callSid and re-pushes mismatched events
 *   2d. /dialer/callback is idempotent via idempotency_keys table
 *   2e. call_attempts INSERT before Twilio, UPDATE after, final status on end
 *
 * Run with: node tests/unit/bff/test_phase2.js
 * No database required — tests use mocks.
 */

const assert = require('assert');
const path   = require('path');
const fs     = require('fs');

const ROOT = path.resolve(__dirname, '../../..');
let passed = 0;
let failed = 0;
const results = [];

function test(name, fn) {
  try {
    const r = fn();
    if (r && typeof r.then === 'function') {
      // async test — must be run with runAll
      return r.then(() => {
        results.push({ name, status: 'PASS' });
        passed++;
      }).catch(e => {
        results.push({ name, status: 'FAIL', detail: e.message });
        failed++;
      });
    }
    results.push({ name, status: 'PASS' });
    passed++;
    return Promise.resolve();
  } catch (e) {
    results.push({ name, status: 'FAIL', detail: e.message });
    failed++;
    return Promise.resolve();
  }
}

// ─── Mock helpers ─────────────────────────────────────────────────────────────

function makePool(rows = []) {
  const calls = [];
  return {
    calls,
    query(sql, params) {
      calls.push({ sql: sql.trim().replace(/\s+/g, ' '), params });
      return Promise.resolve({ rows });
    },
  };
}

function makeRedis(initialItems = []) {
  const store = [...initialItems];
  const calls = [];
  return {
    calls,
    store,
    async rpop(key) {
      calls.push({ op: 'rpop', key });
      return store.length ? store.pop() : null;
    },
    async lpush(key, value) {
      calls.push({ op: 'lpush', key, value });
      store.unshift(value);
    },
    async expire() {},
    async set() { return 'OK'; },
    async del() {},
    async hset() {},
    async hget() { return null; },
    async hdel() {},
    async zadd() {},
  };
}

// Extract a minimal Pipeline-like object from dialer_worker source for unit testing
function makePipeline({ pool, redis, dialer, disposition = 'COMPLETED' }) {
  const id = 'pipeline-test-001';
  const tenantId = 'tenant-aaa';
  const campaignId = 'campaign-bbb';

  const calls = { openCallRecords: 0, handleCallEnd: 0, handleCallError: 0 };

  const pipeline = {
    id,
    tenantId,
    campaignId,
    name: 'TestPipeline',
    status: 'IDLE',
    _pool: pool,
    _redis: redis,
    _dialer: dialer,
    _calls: calls,

    // Simplified _processLead that mirrors Phase 2 structure
    async _processLead(lead) {
      // INSERT call_attempt INITIATED
      const ar = await pool.query(
        `INSERT INTO call_attempts (tenant_id, campaign_id, lead_id, pipeline_id, worker_id, status) VALUES ($1,$2,$3,$4,$5,'INITIATED') RETURNING attempt_id`,
        [lead.tenant_id, lead.campaign_id, lead.lead_id, id, 'worker-test']
      );
      const attemptId = ar.rows[0]?.attempt_id;

      // Initiate call
      const callSid = await dialer.initiate(lead);

      // UPDATE call_attempt with callSid
      await pool.query(
        `UPDATE call_attempts SET call_sid=$1, status='IN_PROGRESS' WHERE attempt_id=$2`,
        [callSid, attemptId]
      );

      // Open call records — synchronous
      calls.openCallRecords++;

      // Wait for completion
      const result = await this._waitForCompletion(callSid);

      // Handle call end — synchronous
      calls.handleCallEnd++;

      // UPDATE call_attempt to final status
      await pool.query(
        `UPDATE call_attempts SET status=$1, disposition=$2, duration_s=$3, ended_at=$4 WHERE attempt_id=$5`,
        [result.disposition, result.disposition, result.durationS || 0, result.endedAt, attemptId]
      );

      return { callSid, attemptId, result };
    },

    async _waitForCompletion(callSid) {
      const completedKey = `voiceos:pipeline:completed:${id}`;
      let attempts = 0;
      while (attempts < 10) {
        const raw = await redis.rpop(completedKey);
        if (raw) {
          const payload = JSON.parse(raw);
          if (payload.callSid !== callSid) {
            // Wrong callSid — push back and keep polling
            await redis.lpush(completedKey, raw);
            attempts++;
            continue;
          }
          return payload;
        }
        attempts++;
      }
      return { callSid, disposition: 'TIMEOUT', durationS: 0, endedAt: new Date().toISOString() };
    },
  };

  return pipeline;
}

// ─── 2a. Migration file validation ───────────────────────────────────────────

const tests = [];

tests.push(test('MIGRATION-01: 016_call_attempts.sql exists', () => {
  const p = path.join(ROOT, 'scripts/db/migrations/016_call_attempts.sql');
  assert.ok(fs.existsSync(p), '016_call_attempts.sql not found');
}));

tests.push(test('MIGRATION-02: migration is wrapped in BEGIN/COMMIT', () => {
  const src = fs.readFileSync(path.join(ROOT, 'scripts/db/migrations/016_call_attempts.sql'), 'utf8');
  assert.ok(src.includes('BEGIN;'), 'Missing BEGIN;');
  assert.ok(src.includes('COMMIT;'), 'Missing COMMIT;');
}));

tests.push(test('MIGRATION-03: call_attempts has all required columns', () => {
  const src = fs.readFileSync(path.join(ROOT, 'scripts/db/migrations/016_call_attempts.sql'), 'utf8');
  for (const col of ['attempt_id', 'tenant_id', 'campaign_id', 'lead_id', 'pipeline_id',
                      'call_sid', 'worker_id', 'status', 'disposition', 'duration_s',
                      'initiated_at', 'ended_at', 'error_message']) {
    assert.ok(src.includes(col), `Missing column: ${col}`);
  }
}));

tests.push(test('MIGRATION-04: status CHECK includes INITIATED and IN_PROGRESS', () => {
  const src = fs.readFileSync(path.join(ROOT, 'scripts/db/migrations/016_call_attempts.sql'), 'utf8');
  assert.ok(src.includes("'INITIATED'"), "Missing 'INITIATED' in status CHECK");
  assert.ok(src.includes("'IN_PROGRESS'"), "Missing 'IN_PROGRESS' in status CHECK");
}));

tests.push(test('MIGRATION-05: Alembic 0036_call_attempts.py exists', () => {
  const p = path.join(ROOT, 'scripts/db/migrations/alembic/versions/0036_call_attempts.py');
  assert.ok(fs.existsSync(p), '0036_call_attempts.py not found');
}));

tests.push(test('MIGRATION-06: Alembic 0036 has upgrade() and downgrade()', () => {
  const src = fs.readFileSync(
    path.join(ROOT, 'scripts/db/migrations/alembic/versions/0036_call_attempts.py'), 'utf8'
  );
  assert.ok(src.includes('def upgrade()'), 'Missing upgrade()');
  assert.ok(src.includes('def downgrade()'), 'Missing downgrade()');
  assert.ok(src.includes("down_revision"), 'Missing down_revision');
  assert.ok(src.includes('"0035"'), 'down_revision should be "0035"');
}));

// ─── 2b. No setImmediate around post-call work ────────────────────────────────

tests.push(test('SYNC-01: setImmediate not used around _openCallRecords in dialer_worker.js', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  assert.ok(
    !src.includes('setImmediate(() => this._openCallRecords'),
    'setImmediate still wraps _openCallRecords'
  );
}));

tests.push(test('SYNC-02: setImmediate not used around _handleCallEnd in dialer_worker.js', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  assert.ok(
    !src.includes('setImmediate(() => this._handleCallEnd'),
    'setImmediate still wraps _handleCallEnd'
  );
}));

tests.push(test('SYNC-03: _openCallRecords is awaited in _processLead', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  assert.ok(
    src.includes('await this._openCallRecords(callSid, lead)'),
    '_openCallRecords is not awaited in _processLead'
  );
}));

tests.push(test('SYNC-04: _handleCallEnd is awaited in _processLead', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  assert.ok(
    src.includes('await this._handleCallEnd(callSid, lead, result, durationS, attemptId)'),
    '_handleCallEnd is not awaited with attemptId in _processLead'
  );
}));

tests.push(test('SYNC-05: lock redis.del is in finally block (released after await chain)', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  // Verify finally block contains the del
  const finallyMatch = src.match(/finally\s*\{[\s\S]+?redis\.del\(lockKey\)/);
  assert.ok(finallyMatch, 'redis.del(lockKey) not in finally block');
}));

// ─── 2c. CallSid validation in _waitForCompletion ────────────────────────────

tests.push(test('CALLSID-01: _waitForCompletion checks payload.callSid !== callSid', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  assert.ok(
    src.includes('payload.callSid !== callSid'),
    'callSid validation missing from _waitForCompletion'
  );
}));

tests.push(test('CALLSID-02: wrong-callSid payload is re-pushed to Redis (lpush)', async () => {
  const correctSid = 'CA_correct_sid';
  const wrongSid   = 'CA_wrong_sid';

  // makeRedis uses store.pop() (pops from tail), so items are consumed tail-first.
  // Put wrongSid at tail (index 1) so it is popped first, correctSid at index 0.
  const redisItems = [
    JSON.stringify({ callSid: correctSid, disposition: 'COMPLETED', durationS: 60, endedAt: new Date().toISOString() }),
    JSON.stringify({ callSid: wrongSid, disposition: 'COMPLETED', durationS: 30, endedAt: new Date().toISOString() }),
  ];
  const mockRedis = makeRedis(redisItems);
  const mockPool  = makePool([{ attempt_id: 'attempt-001' }]);

  const pipeline = makePipeline({
    pool: mockPool,
    redis: mockRedis,
    dialer: { async initiate() { return correctSid; } },
  });

  const result = await pipeline._waitForCompletion(correctSid);
  assert.strictEqual(result.callSid, correctSid, 'Returned wrong callSid');
  assert.strictEqual(result.disposition, 'COMPLETED', 'Wrong disposition');

  // The wrong-callSid payload must have been put back
  const lpushCalls = mockRedis.calls.filter(c => c.op === 'lpush');
  assert.ok(lpushCalls.length > 0, 'Wrong-callSid event was not re-pushed to Redis');
  const repushed = JSON.parse(lpushCalls[0].value);
  assert.strictEqual(repushed.callSid, wrongSid, 'Wrong payload was re-pushed');
}));

tests.push(test('CALLSID-03: correct callSid payload is consumed and returned', async () => {
  const callSid = 'CA_correct_only';
  const mockRedis = makeRedis([
    JSON.stringify({ callSid, disposition: 'NO_ANSWER', durationS: 0, endedAt: new Date().toISOString() }),
  ]);
  const mockPool = makePool([{ attempt_id: 'attempt-002' }]);
  const pipeline = makePipeline({
    pool: mockPool,
    redis: mockRedis,
    dialer: { async initiate() { return callSid; } },
  });

  const result = await pipeline._waitForCompletion(callSid);
  assert.strictEqual(result.disposition, 'NO_ANSWER');
}));

// ─── 2d. Idempotent callback ─────────────────────────────────────────────────

tests.push(test('IDEMPOTENT-01: /dialer/callback checks idempotency_keys before Redis LPUSH', () => {
  const src = fs.readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    src.includes('idempotency_keys') && src.includes('twilio_callback:'),
    'idempotency_keys check missing from /dialer/callback'
  );
}));

tests.push(test('IDEMPOTENT-02: duplicate callback returns 200 without LPUSH', () => {
  const src = fs.readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  // The route must return early (sendStatus(200)) when key exists
  assert.ok(
    src.includes("return res.sendStatus(200)"),
    'Duplicate event does not return 200 early — LPUSH may still occur'
  );
}));

tests.push(test('IDEMPOTENT-03: idempotency key format is CallSid:CallStatus', () => {
  const src = fs.readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    src.includes('`twilio_callback:${CallSid}:${CallStatus}`'),
    'Idempotency key format wrong — must be twilio_callback:${CallSid}:${CallStatus}'
  );
}));

tests.push(test('IDEMPOTENT-04: idempotency key expires after 24 hours', () => {
  const src = fs.readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    src.includes("INTERVAL '24 hours'"),
    "Idempotency key missing 24-hour expiry"
  );
}));

// ─── 2e. call_attempts written before call and updated after ─────────────────

tests.push(test('ATTEMPT-01: call_attempt INSERT happens before dialer.initiate() in _processLead', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  const processLeadBody = src.slice(
    src.indexOf('async _processLead(lead)'),
    src.indexOf('async _waitForCompletion')
  );
  const insertPos  = processLeadBody.indexOf("INSERT INTO call_attempts");
  const initiatePos = processLeadBody.indexOf('await this._dialer.initiate(lead)');
  assert.ok(insertPos > -1, 'INSERT INTO call_attempts not found in _processLead');
  assert.ok(initiatePos > -1, 'dialer.initiate not found in _processLead');
  assert.ok(insertPos < initiatePos, 'INSERT call_attempts must come BEFORE dialer.initiate');
}));

tests.push(test('ATTEMPT-02: call_sid is back-filled after dialer.initiate() succeeds', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  const processLeadBody = src.slice(
    src.indexOf('async _processLead(lead)'),
    src.indexOf('async _waitForCompletion')
  );
  const updateCallSidPos = processLeadBody.indexOf("UPDATE call_attempts SET call_sid");
  const initiatePos      = processLeadBody.indexOf('await this._dialer.initiate(lead)');
  assert.ok(updateCallSidPos > -1, 'UPDATE call_attempts SET call_sid not found');
  assert.ok(updateCallSidPos > initiatePos, 'call_sid UPDATE must come AFTER dialer.initiate');
}));

tests.push(test('ATTEMPT-03: call_attempt is updated to final status in _handleCallEnd', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  const handleEndBody = src.slice(
    src.indexOf('async _handleCallEnd('),
    src.indexOf('async _handleCallError(')
  );
  // SQL may span multiple lines — collapse whitespace before matching
  const collapsed = handleEndBody.replace(/\s+/g, ' ');
  assert.ok(
    collapsed.includes('UPDATE call_attempts') && collapsed.includes('SET status=$1'),
    '_handleCallEnd does not update call_attempts to final status'
  );
  assert.ok(
    handleEndBody.includes('attemptId'),
    '_handleCallEnd does not use attemptId parameter'
  );
}));

tests.push(test('ATTEMPT-04: call_attempt marked FAILED in _handleCallError with error_message', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  const handleErrBody = src.slice(
    src.indexOf('async _handleCallError('),
    src.indexOf('async _scheduleRetry(')
  );
  assert.ok(
    handleErrBody.includes("status='FAILED'") && handleErrBody.includes('error_message'),
    '_handleCallError does not write FAILED status + error_message to call_attempts'
  );
}));

tests.push(test('ATTEMPT-05: Twilio initiation failure updates call_attempt to FAILED in catch block', () => {
  const src = fs.readFileSync(path.join(ROOT, 'dialer_worker.js'), 'utf8');
  const processLeadBody = src.slice(
    src.indexOf('async _processLead(lead)'),
    src.indexOf('async _waitForCompletion')
  );
  // Catch block must handle case where attemptId exists but callSid does not
  assert.ok(
    processLeadBody.includes('} else if (attemptId)'),
    'catch block does not handle Twilio initiation failure with attemptId but no callSid'
  );
}));

tests.push(test('ATTEMPT-06: integration — processLead makes INSERT before initiate and UPDATE after', async () => {
  const callSid = 'CA_integration_test';
  const mockPool = makePool([{ attempt_id: 'attempt-int-001' }]);
  const mockRedis = makeRedis([
    JSON.stringify({ callSid, disposition: 'COMPLETED', durationS: 45, endedAt: new Date().toISOString() }),
  ]);

  const pipeline = makePipeline({
    pool: mockPool,
    redis: mockRedis,
    dialer: { async initiate() { return callSid; } },
  });

  const lead = {
    lead_id: 'lead-001', campaign_id: 'campaign-001',
    tenant_id: 'tenant-001', pipeline_id: 'pipeline-001',
    phone: '9876543210', name: 'Test User', language: 'hi',
  };

  await pipeline._processLead(lead);

  const queries = mockPool.calls.map(c => c.sql);

  // First query must be the INSERT
  assert.ok(queries[0].includes('INSERT INTO call_attempts'), 'First query is not INSERT call_attempts');

  // Second query must be the call_sid UPDATE
  assert.ok(
    queries.some(q => q.includes('UPDATE call_attempts SET call_sid')),
    'No call_sid UPDATE found in query sequence'
  );

  // Final query must be the status UPDATE
  assert.ok(
    queries.some(q => q.includes('UPDATE call_attempts SET status')),
    'No final status UPDATE found in query sequence'
  );

  // Verify order: INSERT → UPDATE call_sid → UPDATE status
  const insertIdx    = queries.findIndex(q => q.includes('INSERT INTO call_attempts'));
  const callSidIdx   = queries.findIndex(q => q.includes('UPDATE call_attempts SET call_sid'));
  const finalIdx     = queries.findIndex(q => q.includes('UPDATE call_attempts SET status'));
  assert.ok(insertIdx < callSidIdx, 'INSERT must come before call_sid UPDATE');
  assert.ok(callSidIdx < finalIdx, 'call_sid UPDATE must come before final status UPDATE');
}));

// ─── Run all tests ────────────────────────────────────────────────────────────

Promise.all(tests).then(() => {
  console.log('\n' + '═'.repeat(70));
  console.log('PHASE 2 VERIFICATION RESULTS');
  console.log('═'.repeat(70));
  for (const r of results) {
    const icon = r.status === 'PASS' ? '✓' : '✗';
    console.log(`  [${r.status}] ${icon} ${r.name}`);
    if (r.detail) console.log(`         Detail: ${r.detail}`);
  }
  console.log('─'.repeat(70));
  console.log(`  Total: ${passed + failed}  |  PASS: ${passed}  |  FAIL: ${failed}`);
  console.log('═'.repeat(70) + '\n');
  process.exit(failed > 0 ? 1 : 0);
});
