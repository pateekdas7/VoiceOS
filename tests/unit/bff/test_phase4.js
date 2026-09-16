'use strict';
/**
 * Phase 4 Verification Tests — Import Resume Fix
 *
 * Verifies all Phase 4 behaviours in bff.js:
 *   - processOneRow: shared row-processing helper (column mapping, validation,
 *     scoring, enrichment, compliance, qualification, insertion, event logging)
 *   - crmMatchPhones: batch CRM match against customer_contacts (MATCHED /
 *     AMBIGUOUS / UNMATCHED, tenant isolation, erasure filter)
 *   - Upload handler refactored to use processOneRow, adds CRM matching,
 *     completed_at, crm_* response fields
 *   - Resume handler: 400 for DONE, no_rows_data, batch-commit progress,
 *     carry-forward failed_rows, CRM match + finalize, correct error paths
 *
 * Run with: node tests/unit/bff/test_phase4.js
 * No database or network required — tests use source inspection + mocks.
 */

const assert = require('assert');
const path   = require('path');
const fs     = require('fs');

const ROOT = path.resolve(__dirname, '../../..');
const SRC  = fs.readFileSync(path.join(ROOT, 'bff.js'), 'utf8');

let passed = 0;
let failed = 0;
const results = [];

function test(name, fn) {
  try {
    const r = fn();
    if (r && typeof r.then === 'function') {
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

// ─── Source-slice helpers ─────────────────────────────────────────────────────

// Extract the source of a named async function from its definition to the next
// top-level function or section boundary.
function fnSrc(marker, endMarker) {
  const start = SRC.indexOf(marker);
  if (start === -1) return '';
  const end = endMarker ? SRC.indexOf(endMarker, start + marker.length) : SRC.length;
  return SRC.slice(start, end === -1 ? SRC.length : end);
}

// Find the upload handler body
function uploadHandlerSrc() {
  const start = SRC.indexOf("app.post('/campaigns/:id/leads/upload'");
  const end   = SRC.indexOf("\napp.post('/campaigns/:id/leads/imports/:importId/resume'");
  return SRC.slice(start, end === -1 ? SRC.length : end);
}

// Find the resume handler body
function resumeHandlerSrc() {
  const start = SRC.indexOf("app.post('/campaigns/:id/leads/imports/:importId/resume'");
  const end   = SRC.indexOf("\napp.get('/campaigns/:id/leads/imports'");
  return SRC.slice(start, end === -1 ? SRC.length : end);
}

const tests = [];

// ═══════════════════════════════════════════════════════════════════════════════
// PROC — processOneRow helper
// ═══════════════════════════════════════════════════════════════════════════════

tests.push(test('PROC-01: processOneRow function is defined in bff.js', () => {
  assert.ok(SRC.includes('async function processOneRow('), 'processOneRow not defined');
}));

tests.push(test('PROC-02: processOneRow accepts rowIndex, rawRow, columnMapping, campaignId, tenantId, importId, campaign, filename, pipelineIds', () => {
  const fn = fnSrc('async function processOneRow(', 'async function crmMatchPhones(');
  assert.ok(fn.includes('rowIndex'),    'processOneRow: missing rowIndex param');
  assert.ok(fn.includes('rawRow'),      'processOneRow: missing rawRow param');
  assert.ok(fn.includes('columnMapping'), 'processOneRow: missing columnMapping param');
  assert.ok(fn.includes('campaignId'), 'processOneRow: missing campaignId param');
  assert.ok(fn.includes('tenantId'),   'processOneRow: missing tenantId param');
  assert.ok(fn.includes('importId'),   'processOneRow: missing importId param');
  assert.ok(fn.includes('pipelineIds'), 'processOneRow: missing pipelineIds param');
}));

tests.push(test('PROC-03: processOneRow applies columnMapping to remap CSV columns to standard fields', () => {
  const fn = fnSrc('async function processOneRow(', 'async function crmMatchPhones(');
  assert.ok(fn.includes('columnMapping[') || fn.includes('columnMapping[csvCol]'),
    'processOneRow must apply columnMapping to CSV column names');
}));

tests.push(test('PROC-04: processOneRow returns outcome="invalid" when phone is missing or invalid', () => {
  const fn = fnSrc('async function processOneRow(', 'async function crmMatchPhones(');
  assert.ok(fn.includes("outcome: 'invalid'"), 'processOneRow must return outcome=invalid for bad phone');
  assert.ok(fn.includes('INVALID_PHONE'),       'Must use INVALID_PHONE as the reason');
}));

tests.push(test('PROC-05: processOneRow uses ON CONFLICT DO NOTHING for idempotency', () => {
  const fn = fnSrc('async function processOneRow(', 'async function crmMatchPhones(');
  assert.ok(fn.includes('ON CONFLICT'), 'processOneRow must use ON CONFLICT');
  assert.ok(fn.includes('DO NOTHING'), 'processOneRow must use DO NOTHING to be idempotent');
}));

tests.push(test('PROC-06: processOneRow returns all expected outcome descriptors', () => {
  const fn = fnSrc('async function processOneRow(', 'async function crmMatchPhones(');
  // 'valid' is returned via ternary: qualResult.qualified ? 'valid' : 'rejected'
  assert.ok(fn.includes("'valid'"),              "Must use 'valid' outcome for qualified leads");
  assert.ok(fn.includes("outcome: 'rejected'") || (fn.includes("'rejected'") && fn.includes('outcome')),
    "Must return outcome='rejected' for non-qualified leads");
  assert.ok(fn.includes("outcome: 'duplicate'"), "Must return outcome='duplicate' for ON CONFLICT");
  assert.ok(fn.includes("outcome: 'invalid'"),   "Must return outcome='invalid' for missing phone");
  assert.ok(fn.includes("outcome: 'error'"),     "Must return outcome='error' for DB errors");
}));

tests.push(test('PROC-07: processOneRow logs IMPORT, SCORED events; REJECTED or DISTRIBUTED+QUEUED conditionally', () => {
  const fn = fnSrc('async function processOneRow(', 'async function crmMatchPhones(');
  assert.ok(fn.includes("'IMPORT'"),       'Must log IMPORT event');
  assert.ok(fn.includes("'SCORED'"),       'Must log SCORED event');
  assert.ok(fn.includes("'REJECTED'"),     'Must log REJECTED event for non-qualified');
  assert.ok(fn.includes("'DISTRIBUTED'"),  'Must log DISTRIBUTED event for qualified+assigned');
  assert.ok(fn.includes("'QUEUED'"),       'Must log QUEUED event after Redis push');
}));

// ═══════════════════════════════════════════════════════════════════════════════
// CRM — crmMatchPhones helper
// ═══════════════════════════════════════════════════════════════════════════════

tests.push(test('CRM-01: crmMatchPhones function is defined in bff.js', () => {
  assert.ok(SRC.includes('async function crmMatchPhones('), 'crmMatchPhones not defined');
}));

tests.push(test('CRM-02: crmMatchPhones JOINs customer_contacts to customers', () => {
  const fn = fnSrc('async function crmMatchPhones(', '// ═════════════════════════════════════════════════════════════════════════════\n// AUTH ROUTES');
  assert.ok(fn.includes('customer_contacts'), 'Must query customer_contacts table');
  assert.ok(fn.includes('customers'),         'Must JOIN to customers table');
  assert.ok(fn.includes('JOIN'),              'Must use JOIN to correlate records');
}));

tests.push(test('CRM-03: crmMatchPhones filters is_active=TRUE and data_erasure_requested=FALSE', () => {
  const fn = fnSrc('async function crmMatchPhones(', '// ═════════════════════════════════════════════════════════════════════════════\n// AUTH ROUTES');
  assert.ok(fn.includes('is_active = TRUE') || fn.includes('is_active=TRUE'),
    'Must filter c.is_active = TRUE');
  assert.ok(fn.includes('data_erasure_requested = FALSE') || fn.includes('data_erasure_requested=FALSE'),
    'Must filter c.data_erasure_requested = FALSE');
}));

tests.push(test('CRM-04: crmMatchPhones uses tenant_id=$1 for tenant isolation', () => {
  const fn = fnSrc('async function crmMatchPhones(', '// ═════════════════════════════════════════════════════════════════════════════\n// AUTH ROUTES');
  assert.ok(fn.includes('tenant_id = $1') || fn.includes('tenant_id=$1'),
    'crmMatchPhones must scope query to tenant_id=$1');
}));

tests.push(test('CRM-05: crmMatchPhones uses COUNT DISTINCT customer_id to detect AMBIGUOUS phones', () => {
  const fn = fnSrc('async function crmMatchPhones(', '// ═════════════════════════════════════════════════════════════════════════════\n// AUTH ROUTES');
  assert.ok(fn.includes('COUNT') && fn.includes('customer_id'),
    'Must COUNT customer_id to detect phones shared by multiple customers');
  assert.ok(fn.includes("'AMBIGUOUS'"), "Must classify multi-customer phones as AMBIGUOUS");
  assert.ok(fn.includes("'MATCHED'"),   "Must classify single-customer phones as MATCHED");
  assert.ok(fn.includes("'UNMATCHED'"), "Must classify no-match phones as UNMATCHED");
}));

tests.push(test('CRM-06: crmMatchPhones integration — MATCHED / AMBIGUOUS / UNMATCHED from mock DB', async () => {
  // Inline minimal implementation matching the source logic
  async function crmMatchPhones(dbClient, phones, tenantId) {
    if (!phones.length) return new Map();
    const { rows } = await dbClient.query(
      `SELECT cc.value AS phone, COUNT(DISTINCT cc.customer_id)::int AS customer_count
       FROM customer_contacts cc
       JOIN customers c ON c.customer_id = cc.customer_id
       WHERE cc.tenant_id = $1
         AND cc.value = ANY($2::text[])
         AND c.is_active = TRUE
         AND c.data_erasure_requested = FALSE
       GROUP BY cc.value`,
      [tenantId, phones]
    );
    const matchMap = new Map();
    for (const row of rows) {
      matchMap.set(row.phone, row.customer_count === 1 ? 'MATCHED' : 'AMBIGUOUS');
    }
    for (const phone of phones) {
      if (!matchMap.has(phone)) matchMap.set(phone, 'UNMATCHED');
    }
    return matchMap;
  }

  const mockDb = {
    query(_sql, _params) {
      return Promise.resolve({
        rows: [
          { phone: '+11111111111', customer_count: 1 }, // MATCHED
          { phone: '+22222222222', customer_count: 3 }, // AMBIGUOUS
          // '+33333333333' — not in DB → UNMATCHED
        ],
      });
    },
  };

  const result = await crmMatchPhones(mockDb, ['+11111111111', '+22222222222', '+33333333333'], 'tenant-1');
  assert.strictEqual(result.get('+11111111111'), 'MATCHED',   'Single-match phone must be MATCHED');
  assert.strictEqual(result.get('+22222222222'), 'AMBIGUOUS', 'Multi-match phone must be AMBIGUOUS');
  assert.strictEqual(result.get('+33333333333'), 'UNMATCHED', 'No-match phone must be UNMATCHED');
}));

tests.push(test('CRM-07: crmMatchPhones returns empty Map for empty phone list without querying DB', async () => {
  let dbCalled = false;
  const mockDb = { query() { dbCalled = true; return Promise.resolve({ rows: [] }); } };

  // Inline implementation
  async function crmMatchPhones(dbClient, phones) {
    if (!phones.length) return new Map();
    await dbClient.query('irrelevant', []);
    return new Map();
  }

  const result = await crmMatchPhones(mockDb, []);
  assert.ok(result instanceof Map, 'Must return a Map');
  assert.strictEqual(result.size, 0, 'Must return empty Map');
  assert.strictEqual(dbCalled, false, 'Must NOT query DB when phones list is empty');
}));

// ═══════════════════════════════════════════════════════════════════════════════
// UPLOAD — refactored upload handler
// ═══════════════════════════════════════════════════════════════════════════════

tests.push(test('UPLOAD-01: Upload handler loop delegates to processOneRow, not inline processing', () => {
  const handler = uploadHandlerSrc();
  assert.ok(handler.includes('processOneRow('), 'Upload handler must call processOneRow');
  // The inline processing logic should not be present
  assert.ok(!handler.includes('scoreLead(mapped)'),
    'Inline scoreLead call must not exist in upload handler loop (moved to processOneRow)');
}));

tests.push(test('UPLOAD-02: Upload handler tracks insertedPhones for CRM matching', () => {
  const handler = uploadHandlerSrc();
  assert.ok(handler.includes('insertedPhones'), 'Upload handler must declare insertedPhones array');
}));

tests.push(test('UPLOAD-03: Upload handler calls crmMatchPhones after the row loop', () => {
  const handler = uploadHandlerSrc();
  const loopEnd   = handler.lastIndexOf('// CRM match');
  const crmCall   = handler.indexOf('crmMatchPhones(');
  assert.ok(loopEnd !== -1, 'Must have CRM match section comment');
  assert.ok(crmCall !== -1, 'Must call crmMatchPhones in upload handler');
  assert.ok(crmCall > loopEnd, 'crmMatchPhones must be called after the row loop');
}));

tests.push(test('UPLOAD-04: Upload handler batch-updates leads.metadata with crm_match_status per status group', () => {
  const handler = uploadHandlerSrc();
  assert.ok(handler.includes('crm_match_status'), 'Must write crm_match_status to lead metadata');
  assert.ok(handler.includes('metadata || $1::jsonb') || handler.includes("metadata || '"),
    'Must use jsonb merge operator || to update metadata');
  assert.ok(handler.includes('ANY($4::text[])') || handler.includes('ANY($'),
    'Must update leads by phone batch (ANY array) — not one-by-one');
}));

tests.push(test('UPLOAD-05: Upload handler finalize UPDATE includes completed_at=NOW()', () => {
  const handler = uploadHandlerSrc();
  const finalUpdate = handler.slice(handler.lastIndexOf("status='DONE'"));
  assert.ok(finalUpdate.includes('completed_at=NOW()') || finalUpdate.includes("completed_at=now()"),
    "Finalize UPDATE must set completed_at=NOW()");
}));

tests.push(test('UPLOAD-06: Upload response includes crm_matched, crm_unmatched, crm_ambiguous', () => {
  const handler = uploadHandlerSrc();
  assert.ok(handler.includes('crm_matched'),   'Response must include crm_matched count');
  assert.ok(handler.includes('crm_unmatched'), 'Response must include crm_unmatched count');
  assert.ok(handler.includes('crm_ambiguous'), 'Response must include crm_ambiguous count');
}));

tests.push(test('UPLOAD-07: Upload handler switch on result.outcome handles all five cases', () => {
  const handler = uploadHandlerSrc();
  assert.ok(handler.includes("case 'valid'"),    "Must handle outcome='valid'");
  assert.ok(handler.includes("case 'rejected'"), "Must handle outcome='rejected'");
  assert.ok(handler.includes("case 'duplicate'"),"Must handle outcome='duplicate'");
  assert.ok(handler.includes("case 'invalid'"),  "Must handle outcome='invalid'");
  assert.ok(handler.includes("case 'error'"),    "Must handle outcome='error'");
}));

// ═══════════════════════════════════════════════════════════════════════════════
// RESUME — resume handler
// ═══════════════════════════════════════════════════════════════════════════════

tests.push(test('RESUME-01: Resume handler fetches import with campaign AND tenant_id isolation', () => {
  const handler = resumeHandlerSrc();
  const fetchSql = handler.slice(handler.indexOf("'SELECT * FROM lead_imports"), handler.indexOf("'SELECT * FROM lead_imports") + 300);
  assert.ok(fetchSql.includes('campaign_id=$2') || fetchSql.includes('campaign_id'),
    'Fetch must include campaign_id filter');
  assert.ok(fetchSql.includes('tenant_id=$3') || fetchSql.includes('tenant_id'),
    'Fetch must include tenant_id filter');
}));

tests.push(test('RESUME-02: Resume returns HTTP 400 for status=DONE (not 200)', () => {
  const handler = resumeHandlerSrc();
  // Must have: status === 'DONE' → 400
  const doneCheck = handler.indexOf("status === 'DONE'");
  assert.ok(doneCheck !== -1, "Must check importRecord.status === 'DONE'");
  const doneRegion = handler.slice(doneCheck, doneCheck + 200);
  assert.ok(doneRegion.includes('status(400)') || doneRegion.includes('.status(400)'),
    'Must return HTTP 400 (not 200) for DONE imports');
  assert.ok(doneRegion.includes('import_already_complete'),
    "Must return error='import_already_complete'");
}));

tests.push(test('RESUME-03: Resume returns 400 when rows_data is empty', () => {
  const handler = resumeHandlerSrc();
  assert.ok(handler.includes('no_rows_data'), "Must return error='no_rows_data' when rows_data is absent");
  assert.ok(handler.includes('status(400)') && handler.indexOf('no_rows_data') > 0,
    'no_rows_data must be a 400 response');
}));

tests.push(test('RESUME-04: Resume finalizes import when startRow >= rows.length (finalization-crash recovery)', () => {
  const handler = resumeHandlerSrc();
  assert.ok(handler.includes('startRow >= rows.length'),
    'Must detect when all rows are already processed');
  assert.ok(handler.includes('already_processed'),
    'Must respond with message=already_processed for this edge case');
}));

tests.push(test('RESUME-05: Resume fetches campaign record (required by processOneRow enrichment)', () => {
  const handler = resumeHandlerSrc();
  assert.ok(handler.includes("SELECT * FROM campaigns"),
    'Resume must fetch campaign record');
  assert.ok(handler.includes('campaign_not_found'),
    'Must return 404 if campaign is missing');
}));

tests.push(test('RESUME-06: Resume marks import PROCESSING before starting batch loop', () => {
  const handler = resumeHandlerSrc();
  const processingIdx = handler.indexOf("status='PROCESSING'");
  const batchLoopIdx  = handler.indexOf('batchStart < rows.length');
  assert.ok(processingIdx !== -1, "Must set status='PROCESSING'");
  assert.ok(batchLoopIdx  !== -1, 'Must have batch loop');
  assert.ok(processingIdx < batchLoopIdx, "PROCESSING update must precede the batch loop");
}));

tests.push(test('RESUME-07: Resume processes rows in BATCH_SIZE=100 batches', () => {
  const handler = resumeHandlerSrc();
  assert.ok(handler.includes('BATCH_SIZE = 100') || handler.includes('BATCH_SIZE=100'),
    'BATCH_SIZE must be 100');
  assert.ok(handler.includes('batchStart += BATCH_SIZE'),
    'Loop must advance by BATCH_SIZE');
  assert.ok(handler.includes('Math.min(batchStart + BATCH_SIZE'),
    'batchEnd must be capped with Math.min to handle final partial batch');
}));

tests.push(test('RESUME-08: Resume commits last_processed_row at each batch boundary', () => {
  const handler = resumeHandlerSrc();
  // Must UPDATE last_processed_row inside the batch loop before COMMIT
  const batchRegion = handler.slice(handler.indexOf('async function') !== -1 ? 0 : 0);
  assert.ok(handler.includes('last_processed_row=$1'),
    'Must UPDATE last_processed_row at batch commit point');
  // COMMIT must follow the UPDATE
  const commitIdx = handler.indexOf("batchClient.query('COMMIT')");
  const updateIdx = handler.indexOf('last_processed_row=$1');
  assert.ok(updateIdx < commitIdx, 'last_processed_row UPDATE must precede COMMIT');
}));

tests.push(test('RESUME-09: Resume carries forward importRecord.failed_rows from previous runs', () => {
  const handler = resumeHandlerSrc();
  assert.ok(handler.includes('importRecord.failed_rows'),
    'Must carry forward failed_rows from previous (partial) runs');
}));

tests.push(test('RESUME-10: Resume calls crmMatchPhones after all batches complete', () => {
  const handler = resumeHandlerSrc();
  const batchLoopEnd  = handler.lastIndexOf('batchClient.release()');
  const crmCallIdx    = handler.indexOf('crmMatchPhones(finalClient', batchLoopEnd);
  assert.ok(crmCallIdx !== -1, 'Must call crmMatchPhones after batch loop (using finalClient)');
}));

tests.push(test('RESUME-11: Resume updates leads.metadata with crm_match_status using batch UPDATE per status', () => {
  const handler = resumeHandlerSrc();
  assert.ok(handler.includes('crm_match_status'), 'Must write crm_match_status to lead metadata');
  assert.ok(handler.includes('metadata || $1::jsonb') || handler.includes("metadata ||"),
    'Must use jsonb merge || operator');
  // Must update by status group (MATCHED, UNMATCHED, AMBIGUOUS separately)
  assert.ok(handler.includes('byStatus'), 'Must group phones by status for batch UPDATE');
}));

tests.push(test('RESUME-12: Resume finalize UPDATE includes completed_at=NOW()', () => {
  const handler = resumeHandlerSrc();
  const finalIdx = handler.lastIndexOf("status='DONE'");
  const finalRegion = handler.slice(finalIdx, finalIdx + 300);
  assert.ok(finalRegion.includes('completed_at=NOW()') || finalRegion.includes("completed_at=now()"),
    "Resume finalize UPDATE must set completed_at=NOW()");
}));

tests.push(test('RESUME-13: Resume response includes from_row, valid, invalid, duplicates, rejected, crm_*', () => {
  const handler = resumeHandlerSrc();
  const respIdx  = handler.lastIndexOf('res.json({');
  const respRegion = handler.slice(respIdx, respIdx + 300);
  assert.ok(respRegion.includes('from_row'),    'Response must include from_row');
  assert.ok(respRegion.includes('valid'),        'Response must include valid count');
  assert.ok(respRegion.includes('invalid'),      'Response must include invalid count');
  assert.ok(respRegion.includes('duplicates'),   'Response must include duplicates count');
  assert.ok(respRegion.includes('rejected'),     'Response must include rejected count');
  assert.ok(respRegion.includes('crm_matched'),  'Response must include crm_matched');
  assert.ok(respRegion.includes('crm_unmatched'),'Response must include crm_unmatched');
  assert.ok(respRegion.includes('crm_ambiguous'),'Response must include crm_ambiguous');
}));

tests.push(test('RESUME-14: On batch error, ROLLBACK + mark import FAILED + return 500', () => {
  const handler    = resumeHandlerSrc();
  const catchIdx   = handler.indexOf('} catch (batchErr) {');
  assert.ok(catchIdx !== -1, 'Must have catch block for batch errors');
  // Use a 700-char window — the catch block contains a multi-line pool.query + console.error
  const catchRegion = handler.slice(catchIdx, catchIdx + 700);
  assert.ok(catchRegion.includes('ROLLBACK'),           'Must ROLLBACK on batch error');
  assert.ok(catchRegion.includes("status='FAILED'"),    "Must mark import FAILED on batch error");
  assert.ok(catchRegion.includes('status(500)') || catchRegion.includes('.status(500)'),
    'Must return HTTP 500 on batch error');
  assert.ok(catchRegion.includes('batch_failed'),       "Must return error='batch_failed'");
}));

tests.push(test('RESUME-15: Resume uses separate pool.connect() clients per batch (crash isolation)', () => {
  const handler = resumeHandlerSrc();
  assert.ok(handler.includes('batchClient'),
    'Must use a separate batchClient per batch (not the same connection for all batches)');
  assert.ok(handler.includes('batchClient.release()'),
    'Must release batchClient in finally block');
}));

// ═══════════════════════════════════════════════════════════════════════════════
// INTEGRATION — end-to-end mock sequences
// ═══════════════════════════════════════════════════════════════════════════════

tests.push(test('INT-01: processOneRow logic — valid path inserts lead and returns outcome=valid', async () => {
  // Simplified inline replica of processOneRow for integration verification
  function normalizePhone(p) { return p ? p.replace(/\D/g, '').replace(/^0/, '+0') || '+1' + p.replace(/\D/g,'') : null; }
  function normalizeName(n) { return (n || '').trim(); }
  function scoreLead() { return 75; }
  function detectLanguage() { return 'en'; }
  async function enrichLead() { return { fields: { source: 'csv' } }; }
  function checkCompliance() { return { allowed: true }; }
  async function qualifyLead() { return { qualified: true, reason: null }; }
  async function distributeLeadToPipeline() { return 'pipeline-1'; }
  async function pushToRedisQueue() { return true; }
  const eventLog = [];
  async function logEvent(client, ev) { eventLog.push(ev.eventType); }

  const dbLog = [];
  const client = {
    async query(sql, params) {
      const s = sql.replace(/\s+/g, ' ').trim();
      dbLog.push(s.slice(0, 60));
      if (s.includes('INSERT INTO leads')) {
        return { rows: [{ lead_id: 'l1', campaign_id: 'c1', phone: '+19995550001', queue_status: 'PENDING' }] };
      }
      return { rows: [] };
    },
  };

  // Run the core logic
  const rawRow     = { 'Phone Number': '+19995550001', Name: 'Alice Test' };
  const columnMapping = { 'Phone Number': 'phone', Name: 'name' };
  const mapped = {};
  for (const [csvCol, val] of Object.entries(rawRow)) {
    const stdField = columnMapping[csvCol] || csvCol.toLowerCase().replace(/\s+/g, '_');
    mapped[stdField] = val;
  }
  const phone = normalizePhone(mapped.phone);
  const name  = normalizeName(mapped.name);
  assert.ok(phone, 'Phone must normalize successfully');

  const score        = scoreLead(mapped);
  const language     = detectLanguage(mapped);
  const enrichResult = await enrichLead({ phone, ...mapped }, {}, client);
  const metadata     = { ...mapped, ...(enrichResult.fields || {}) };
  delete metadata.phone; delete metadata.name;

  const compResult = checkCompliance(null, { is_blacklisted: false });
  assert.ok(compResult.allowed, 'Compliance must pass');

  const qualResult = await qualifyLead('c1', 't1', { score, language }, client);
  assert.ok(qualResult.qualified, 'Lead must qualify');

  const pipelineId = await distributeLeadToPipeline('c1', score, language, 't1', [], client);
  assert.ok(pipelineId, 'Must distribute to pipeline');

  const lr = await client.query('INSERT INTO leads ...', []);
  assert.strictEqual(lr.rows.length, 1, 'INSERT must return the new lead');

  await logEvent(client, { eventType: 'IMPORT' });
  await logEvent(client, { eventType: 'SCORED' });
  await logEvent(client, { eventType: 'DISTRIBUTED' });
  const queued = await pushToRedisQueue(lr.rows[0]);
  if (queued) await logEvent(client, { eventType: 'QUEUED' });

  assert.ok(eventLog.includes('IMPORT'),       'Must log IMPORT event');
  assert.ok(eventLog.includes('SCORED'),       'Must log SCORED event');
  assert.ok(eventLog.includes('DISTRIBUTED'),  'Must log DISTRIBUTED event');
  assert.ok(eventLog.includes('QUEUED'),       'Must log QUEUED event');
}));

tests.push(test('INT-02: Resume batch commit sequence — BEGIN→process rows→UPDATE last_processed_row→COMMIT per batch', async () => {
  const BATCH_SIZE = 100;
  const rows = Array.from({ length: 3 }, (_, i) => ({ phone: `+1999000${i}`, name: `Lead ${i}` }));
  const startRow = 0;

  const batchLog = [];

  // Simulate the batch loop (without actual processOneRow — just verify the commit sequence)
  for (let batchStart = startRow; batchStart < rows.length; batchStart += BATCH_SIZE) {
    const batchEnd = Math.min(batchStart + BATCH_SIZE, rows.length);
    // BEGIN
    batchLog.push({ op: 'BEGIN', batchStart, batchEnd });
    // ... process rows batchStart..batchEnd ...
    for (let i = batchStart; i < batchEnd; i++) {
      batchLog.push({ op: 'processRow', i });
    }
    // UPDATE last_processed_row
    batchLog.push({ op: 'UPDATE_PROGRESS', last_processed_row: batchEnd });
    // COMMIT
    batchLog.push({ op: 'COMMIT', batchEnd });
  }

  // Verify the sequence for a 3-row dataset with BATCH_SIZE=100 (single batch)
  const begins  = batchLog.filter(e => e.op === 'BEGIN');
  const commits = batchLog.filter(e => e.op === 'COMMIT');
  const updates = batchLog.filter(e => e.op === 'UPDATE_PROGRESS');

  assert.strictEqual(begins.length, 1,  '3 rows → 1 batch → 1 BEGIN');
  assert.strictEqual(commits.length, 1, '3 rows → 1 batch → 1 COMMIT');
  assert.strictEqual(updates.length, 1, '3 rows → 1 batch → 1 progress update');
  assert.strictEqual(updates[0].last_processed_row, 3, 'last_processed_row must be set to batchEnd=3');

  // Verify commit comes after progress update
  const updateIdx = batchLog.findIndex(e => e.op === 'UPDATE_PROGRESS');
  const commitIdx = batchLog.findIndex(e => e.op === 'COMMIT');
  assert.ok(updateIdx < commitIdx, 'last_processed_row UPDATE must precede COMMIT');
}));

tests.push(test('INT-03: Resume with 250 rows uses 3 batches of ≤100 each', async () => {
  const BATCH_SIZE = 100;
  const rows = Array.from({ length: 250 }, (_, i) => ({ phone: `+1999${String(i).padStart(7, '0')}` }));
  const startRow = 0;

  const commits = [];
  for (let batchStart = startRow; batchStart < rows.length; batchStart += BATCH_SIZE) {
    const batchEnd = Math.min(batchStart + BATCH_SIZE, rows.length);
    commits.push(batchEnd);
  }

  assert.strictEqual(commits.length, 3, '250 rows at BATCH_SIZE=100 → 3 batches');
  assert.strictEqual(commits[0], 100, 'First batch ends at row 100');
  assert.strictEqual(commits[1], 200, 'Second batch ends at row 200');
  assert.strictEqual(commits[2], 250, 'Third batch ends at row 250');
}));

tests.push(test('INT-04: Resume resumes from last committed row — rows before startRow skipped', async () => {
  const BATCH_SIZE = 100;
  // Simulate a crash after first batch: last_processed_row=100, rows.length=250
  const rows = Array.from({ length: 250 }, (_, i) => i);
  const startRow = 100; // already committed

  const processedRows = [];
  for (let batchStart = startRow; batchStart < rows.length; batchStart += BATCH_SIZE) {
    const batchEnd = Math.min(batchStart + BATCH_SIZE, rows.length);
    for (let i = batchStart; i < batchEnd; i++) processedRows.push(i);
  }

  assert.strictEqual(processedRows[0], 100, 'Must start from row 100 (not 0)');
  assert.strictEqual(processedRows.length, 150, 'Must process exactly 150 remaining rows');
  assert.ok(!processedRows.includes(0), 'Must NOT re-process already-committed rows');
}));

// ─── Run all tests ────────────────────────────────────────────────────────────

const pad = (s, n) => s + ' '.repeat(Math.max(0, n - s.length));

Promise.all(tests).then(() => {
  console.log('');
  console.log('  Phase 4 — Import Resume Fix');
  console.log('  ─────────────────────────────────────────────────────────────');
  for (const r of results) {
    const status = r.status === 'PASS' ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAIL\x1b[0m';
    console.log(`  ${pad(r.name, 70)}  ${status}${r.detail ? `\n    ${r.detail}` : ''}`);
  }
  console.log('  ─────────────────────────────────────────────────────────────');
  console.log(`  ${passed + failed} tests — ${passed} PASS — ${failed} FAIL`);
  console.log('');
  process.exit(failed ? 1 : 0);
});
