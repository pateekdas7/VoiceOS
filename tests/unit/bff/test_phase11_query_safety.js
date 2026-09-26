'use strict';
/**
 * Phase 11 Verification Tests — Query Safety & API Hygiene
 *
 * 11a. Pagination — parsePage helper present, enforced on all uncapped list routes
 * 11b. UUID validation — UUID_RE global, requireUUID middleware, applied to key routes
 * 11c. Idempotency — idempotency() middleware, Redis setnx pattern, applied to POST mutations
 * 11d. Multi-origin CORS — FRONTEND_ALLOWED_ORIGINS env var, dynamic origin function
 * 11e. Column projection — SELECT * removed from sensitive admin routes
 *
 * Run with: node tests/unit/bff/test_phase11_query_safety.js
 * No database or network required — source inspection only.
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
    results.push({ name, status: 'PASS' });
    fn();
    passed++;
    return Promise.resolve();
  } catch (e) {
    results[results.length - 1] = { name, status: 'FAIL', detail: e.message };
    failed++;
    return Promise.resolve();
  }
}

async function runAll(tests) {
  for (const t of tests) await t;
  console.log('\n── Phase 11 Query Safety Tests ────────────────────────────');
  for (const r of results) {
    console.log(`  [${r.status}] ${r.name}${r.detail ? ' — ' + r.detail : ''}`);
  }
  console.log(`\n  ${passed} passed, ${failed} failed\n`);
  process.exit(failed > 0 ? 1 : 0);
}

// ─── Source slice helpers ──────────────────────────────────────────────────────

function sliceFn(name) {
  const decl = `function ${name}(`;
  let start = SRC.indexOf(decl);
  if (start === -1) return '';
  let depth = 0, i = start;
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') depth++;
    if (SRC[i] === '}') { depth--; if (depth === 0) break; }
  }
  return SRC.slice(start, i + 1);
}

function sliceRoute(prefix) {
  const start = SRC.indexOf(prefix);
  if (start === -1) return '';
  let depth = 0, i = start, entered = false;
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') { depth++; entered = true; }
    if (SRC[i] === '}') { depth--; if (entered && depth === 0) break; }
  }
  return SRC.slice(start, i + 1);
}

const parsePageSrc   = sliceFn('parsePage');
const requireUUIDSrc = sliceFn('requireUUID');
const idempotencySrc = sliceFn('idempotency');

// ─── 11a: Pagination ──────────────────────────────────────────────────────────

const TESTS = [

test('11a: parsePage helper is defined', () => {
  assert.ok(parsePageSrc.length > 0, 'parsePage function not found in bff.js');
}),

test('11a: parsePage applies Math.min cap on limit', () => {
  assert.ok(parsePageSrc.includes('Math.min'), 'parsePage must cap limit with Math.min');
}),

test('11a: parsePage uses PAGE_MAX_LIMIT or a maxLimit parameter', () => {
  assert.ok(
    SRC.includes('PAGE_MAX_LIMIT') || parsePageSrc.includes('maxLimit'),
    'parsePage must reference PAGE_MAX_LIMIT or maxLimit'
  );
}),

test('11a: GET /campaigns uses parsePage', () => {
  const src = sliceRoute("app.get('/campaigns', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /campaigns must use parsePage');
}),

test('11a: GET /campaigns/:id/leads uses parsePage', () => {
  const src = sliceRoute("app.get('/campaigns/:id/leads', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /campaigns/:id/leads must use parsePage');
}),

test('11a: GET /campaigns/:id/pipelines uses parsePage', () => {
  const src = sliceRoute("app.get('/campaigns/:id/pipelines', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /campaigns/:id/pipelines must use parsePage');
}),

test('11a: GET /campaigns/:id/execution-events uses parsePage', () => {
  const src = sliceRoute("app.get('/campaigns/:id/execution-events', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /campaigns/:id/execution-events must use parsePage');
}),

test('11a: GET /pipelines/:pipelineId/execution-events uses parsePage', () => {
  const src = sliceRoute("app.get('/pipelines/:pipelineId/execution-events', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /pipelines/:pipelineId/execution-events must use parsePage');
}),

test('11a: GET /pipelines/:pipelineId/leads uses parsePage', () => {
  const src = sliceRoute("app.get('/pipelines/:pipelineId/leads', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /pipelines/:pipelineId/leads must use parsePage');
}),

test('11a: GET /admin/clients uses parsePage', () => {
  const src = sliceRoute("app.get('/admin/clients', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /admin/clients must use parsePage');
}),

test('11a: GET /team uses parsePage', () => {
  const src = sliceRoute("app.get('/team', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /team must use parsePage');
}),

test('11a: GET /dialer/active-calls uses parsePage', () => {
  const src = sliceRoute("app.get('/dialer/active-calls', requireAuth");
  assert.ok(src.includes('parsePage'), 'GET /dialer/active-calls must use parsePage');
}),

// ─── 11b: UUID validation ─────────────────────────────────────────────────────

test('11b: UUID_RE is defined as a global constant (not inside a function)', () => {
  // Must appear before the first 'function ' declaration that uses it
  const uuidReIdx = SRC.indexOf('const UUID_RE =');
  assert.ok(uuidReIdx > -1, 'const UUID_RE = ... not found');
  // Verify it is at module scope: no preceding 'function' body that contains it
  // Simple proxy: it must not be preceded by 'const UUID_RE =' inside a function literal
  const precedingText = SRC.slice(0, uuidReIdx);
  // Count open braces minus close braces in preceding text to estimate scope depth
  let depth = 0;
  for (const ch of precedingText) {
    if (ch === '{') depth++;
    if (ch === '}') depth--;
  }
  assert.strictEqual(depth, 0, 'UUID_RE must be declared at module scope (depth 0), not inside a function');
}),

test('11b: requireUUID middleware is defined', () => {
  assert.ok(requireUUIDSrc.length > 0, 'requireUUID function not found in bff.js');
}),

test('11b: requireUUID tests each param with UUID_RE', () => {
  assert.ok(requireUUIDSrc.includes('UUID_RE.test'), 'requireUUID must test params with UUID_RE');
}),

test('11b: requireUUID returns 400 with invalid_uuid on mismatch', () => {
  assert.ok(requireUUIDSrc.includes('400'),           'requireUUID must return HTTP 400');
  assert.ok(requireUUIDSrc.includes("'invalid_uuid'"), "requireUUID must return { error: 'invalid_uuid' }");
}),

test('11b: requireUUID calls next() when all params are valid', () => {
  assert.ok(requireUUIDSrc.includes('next()'), 'requireUUID must call next() on valid UUIDs');
}),

test('11b: GET /campaigns/:id applies requireUUID', () => {
  assert.ok(
    SRC.includes("app.get('/campaigns/:id', requireAuth, requireUUID("),
    "GET /campaigns/:id must use requireUUID"
  );
}),

test('11b: PUT /campaigns/:id applies requireUUID', () => {
  assert.ok(
    SRC.includes("app.put('/campaigns/:id', requireAuth, requireUUID("),
    "PUT /campaigns/:id must use requireUUID"
  );
}),

test('11b: GET /campaigns/:id/pipelines/:pipelineId applies requireUUID for both params', () => {
  assert.ok(
    SRC.includes("app.get('/campaigns/:id/pipelines/:pipelineId', requireAuth, requireUUID('id', 'pipelineId')"),
    "GET /campaigns/:id/pipelines/:pipelineId must validate both :id and :pipelineId"
  );
}),

test('11b: GET /admin/clients/:tenantId applies requireUUID', () => {
  assert.ok(
    SRC.includes("requireUUID('tenantId')"),
    "GET /admin/clients/:tenantId must use requireUUID('tenantId')"
  );
}),

// ─── 11c: Idempotency ─────────────────────────────────────────────────────────

test('11c: idempotency middleware is defined', () => {
  assert.ok(idempotencySrc.length > 0, 'idempotency function not found in bff.js');
}),

test('11c: idempotency reads Idempotency-Key header', () => {
  assert.ok(
    idempotencySrc.includes("idempotency-key"),
    "idempotency must read the 'idempotency-key' header"
  );
}),

test('11c: idempotency checks Redis cache before processing', () => {
  assert.ok(idempotencySrc.includes('redis.get'), 'idempotency must check Redis cache with redis.get');
}),

test('11c: idempotency stores response in Redis (redis.set)', () => {
  assert.ok(idempotencySrc.includes('redis.set'), 'idempotency must persist response with redis.set');
}),

test('11c: idempotency passes through when no Idempotency-Key header is sent', () => {
  assert.ok(idempotencySrc.includes('next()'), 'idempotency must call next() when no key header present');
}),

test('11c: POST /campaigns uses idempotency middleware', () => {
  assert.ok(
    SRC.includes("app.post('/campaigns', requireAuth, idempotency("),
    "POST /campaigns must use idempotency middleware"
  );
}),

test('11c: POST /campaigns/:id/pipelines uses idempotency middleware', () => {
  assert.ok(
    SRC.includes("app.post('/campaigns/:id/pipelines', requireAuth, requireUUID") &&
    SRC.includes("idempotency('pipeline')"),
    "POST /campaigns/:id/pipelines must use idempotency middleware"
  );
}),

// ─── 11d: Multi-origin CORS ───────────────────────────────────────────────────

test('11d: FRONTEND_ALLOWED_ORIGINS env var is referenced', () => {
  assert.ok(SRC.includes('FRONTEND_ALLOWED_ORIGINS'), 'FRONTEND_ALLOWED_ORIGINS env var not referenced');
}),

test('11d: CORS origins are parsed from comma-separated list', () => {
  assert.ok(SRC.includes(".split(',')"), 'CORS origins must be split from comma-separated string');
}),

test('11d: CORS origin is a dynamic function (not a static string)', () => {
  const corsIdx = SRC.indexOf('app.use(cors(');
  assert.ok(corsIdx > -1, 'app.use(cors(...)) not found');
  const corsBlock = SRC.slice(corsIdx, corsIdx + 400);
  assert.ok(corsBlock.includes('origin: (origin, cb)'), 'CORS origin must be a dynamic function, not a static string');
}),

test('11d: CORS falls back to FRONTEND_BASE_URL when FRONTEND_ALLOWED_ORIGINS is not set', () => {
  assert.ok(SRC.includes('FRONTEND_BASE_URL'), 'Must fall back to FRONTEND_BASE_URL env var');
}),

// ─── 11e: Column projection ───────────────────────────────────────────────────

test('11e: GET /admin/clients does not use bare SELECT * FROM tenants', () => {
  const src = sliceRoute("app.get('/admin/clients', requireAuth");
  assert.ok(!src.includes('SELECT * FROM tenants'), 'GET /admin/clients must not use SELECT * FROM tenants — use explicit columns');
}),

test('11e: GET /admin/clients/:tenantId does not use bare SELECT * FROM tenants', () => {
  const src = sliceRoute("app.get('/admin/clients/:tenantId', requireAuth");
  assert.ok(!src.includes('SELECT * FROM tenants'), 'GET /admin/clients/:tenantId must not use SELECT * — use explicit columns');
}),

test('11e: admin routes use a shared safe column list', () => {
  assert.ok(SRC.includes('_TENANT_SAFE_COLS'), 'Expected _TENANT_SAFE_COLS constant for explicit tenant column projection');
}),

test('11e: GET /campaigns/:id/leads/:leadId/events does not use SELECT *', () => {
  const src = sliceRoute("app.get('/campaigns/:id/leads/:leadId/events', requireAuth");
  assert.ok(!src.includes('SELECT *'), 'Lead event timeline must not use SELECT * — use explicit columns');
}),

];

runAll(TESTS);
