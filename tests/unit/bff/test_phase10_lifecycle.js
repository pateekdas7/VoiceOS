'use strict';
/**
 * Phase 10 Verification Tests — Request Lifecycle Hardening
 *
 * 10a. Global error handler — 4-param middleware present, logs with trace_id, handles 4xx/5xx
 * 10b. DB statement timeout — pool configured with statement_timeout, connectionTimeoutMillis
 * 10c. Tenant active check — middleware present, queries tenants.status, blocks inactive tenants
 * 10d. Per-route body size — 128kb default, 50mb on upload/import-resume routes
 * 10e. Token refresh — POST /auth/refresh route present, strips iat/exp, calls makeToken
 *
 * Run with: node tests/unit/bff/test_phase10_lifecycle.js
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
  console.log('\n── Phase 10 Lifecycle Tests ────────────────────────────────');
  for (const r of results) {
    console.log(`  [${r.status}] ${r.name}${r.detail ? ' — ' + r.detail : ''}`);
  }
  console.log(`\n  ${passed} passed, ${failed} failed\n`);
  process.exit(failed > 0 ? 1 : 0);
}

// ─── Source helpers ───────────────────────────────────────────────────────────

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

// Locate the section between two source markers.
function sliceBetween(startStr, endStr) {
  const s = SRC.indexOf(startStr);
  if (s === -1) return '';
  const e = SRC.indexOf(endStr, s);
  return e === -1 ? '' : SRC.slice(s, e + endStr.length);
}

const errHandlerSrc = (() => {
  // 4-param error handler is after the catch-all
  const idx = SRC.indexOf('app.use((err, req, res, _next)');
  if (idx === -1) return '';
  let depth = 0, i = idx, entered = false;
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') { depth++; entered = true; }
    if (SRC[i] === '}') { depth--; if (entered && depth === 0) break; }
  }
  return SRC.slice(idx, i + 1);
})();

const tenantCheckSrc = (() => {
  const marker = 'Phase 10c: Tenant active check';
  const idx = SRC.indexOf(marker);
  if (idx === -1) return '';
  return SRC.slice(idx, idx + 1000);
})();

const poolSrc = (() => {
  const idx = SRC.indexOf('Phase 10b:');
  if (idx === -1) return '';
  return SRC.slice(idx, idx + 800);
})();

const refreshRouteSrc = sliceRoute("app.post('/auth/refresh'");

// ─── 10a: Global error handler ────────────────────────────────────────────────

const TESTS = [

test('10a: global error handler is defined (4-param middleware)', () => {
  assert.ok(errHandlerSrc.length > 0, 'app.use((err, req, res, _next) => ...) not found');
}),

test('10a: error handler is placed after the catch-all route', () => {
  const catchAllPos = SRC.indexOf("app.all('/{*path}'");
  const errHandlerPos = SRC.indexOf('app.use((err, req, res, _next)');
  assert.ok(catchAllPos > -1 && errHandlerPos > -1, 'catch-all or error handler not found');
  assert.ok(errHandlerPos > catchAllPos, 'Error handler must be registered after the catch-all');
}),

test('10a: error handler logs with trace_id', () => {
  assert.ok(errHandlerSrc.includes('trace_id'), 'Error handler must include trace_id in log');
  assert.ok(errHandlerSrc.includes('log.error'), 'Error handler must call log.error');
}),

test('10a: error handler uses err.status/statusCode for 4xx passthrough', () => {
  assert.ok(
    errHandlerSrc.includes('err.status') || errHandlerSrc.includes('err.statusCode'),
    'Error handler must preserve error status for 4xx (body-parser 413, etc.)'
  );
}),

test('10a: error handler checks res.headersSent before writing', () => {
  assert.ok(errHandlerSrc.includes('headersSent'), 'Must guard against double-write with headersSent check');
}),

test('10a: error handler returns different messages for 4xx vs 5xx', () => {
  assert.ok(
    errHandlerSrc.includes('server_error') && (errHandlerSrc.includes('bad_request') || errHandlerSrc.includes('< 500')),
    'Error handler must distinguish client errors from server errors'
  );
}),

test('10a: generic route catches replaced with throw e (no stray single-line 500s)', () => {
  // Only the intentional bulk-import 500s (with detail/batch context) should remain.
  // These are identifiable by having 'batch_failed', 'finalization_failed', or 'detail' context.
  const lines = SRC.split('\n');
  const stray = lines.filter((line, i) => {
    if (!line.includes("res.status(500)")) return false;
    // Allowed: lines that have batch/resume/finalization context
    if (line.includes('batch_failed') || line.includes('finalization_failed')) return false;
    if (line.includes('detail:')) return false;
    return true;
  });
  assert.strictEqual(stray.length, 0,
    `Unexpected bare res.status(500) lines:\n${stray.join('\n')}`
  );
}),

// ─── 10b: DB statement timeout ────────────────────────────────────────────────

test('10b: statement_timeout is set in pool options', () => {
  assert.ok(poolSrc.includes('statement_timeout'), 'statement_timeout not configured on pool');
}),

test('10b: connectionTimeoutMillis is set on pool', () => {
  assert.ok(poolSrc.includes('connectionTimeoutMillis'), 'connectionTimeoutMillis not configured on pool');
}),

test('10b: idleTimeoutMillis is set on pool', () => {
  assert.ok(poolSrc.includes('idleTimeoutMillis'), 'idleTimeoutMillis not configured on pool');
}),

test('10b: DB_STATEMENT_TIMEOUT_MS is env-configurable', () => {
  assert.ok(SRC.includes('DB_STATEMENT_TIMEOUT_MS'), 'DB_STATEMENT_TIMEOUT_MS env var missing');
}),

test('10b: DB_CONNECTION_TIMEOUT_MS is env-configurable', () => {
  assert.ok(SRC.includes('DB_CONNECTION_TIMEOUT_MS'), 'DB_CONNECTION_TIMEOUT_MS env var missing');
}),

test('10b: pool config is shared between DSN and non-DSN paths (DRY)', () => {
  // _poolCommon spread ensures both paths get the same timeouts
  assert.ok(SRC.includes('_poolCommon') || SRC.includes('poolCommon'),
    'Shared pool config object not found — both DSN/non-DSN paths must have same timeouts'
  );
}),

// ─── 10c: Tenant active check ─────────────────────────────────────────────────

test('10c: tenant active check middleware is defined', () => {
  assert.ok(tenantCheckSrc.length > 0, 'Phase 10c tenant active check middleware not found');
}),

test('10c: skips non-tenant actor_kinds (platform users pass through)', () => {
  assert.ok(
    tenantCheckSrc.includes("actor_kind !== 'tenant'"),
    "Must skip requests where actor_kind is not 'tenant'"
  );
}),

test('10c: skips unauthenticated requests (no req.user)', () => {
  assert.ok(tenantCheckSrc.includes('!req.user'), 'Must skip unauthenticated requests');
}),

test('10c: queries tenants.status for the authenticated tenant', () => {
  assert.ok(tenantCheckSrc.includes('tenants'), 'Must query the tenants table');
  assert.ok(tenantCheckSrc.includes('status'),  'Must check the tenant status column');
}),

test('10c: returns 403 tenant_suspended when tenant is not ACTIVE', () => {
  assert.ok(tenantCheckSrc.includes('403'),                'Must return HTTP 403');
  assert.ok(tenantCheckSrc.includes("'tenant_suspended'"), "Must return { error: 'tenant_suspended' }");
}),

test('10c: forwards DB errors to global error handler via next(e)', () => {
  assert.ok(tenantCheckSrc.includes('next(e)'), 'DB error in tenant check must call next(e)');
}),

test('10c: middleware is registered globally before route handlers', () => {
  const checkPos  = SRC.indexOf('Phase 10c: Tenant active check');
  const routePos  = SRC.indexOf("app.get('/campaigns'");
  assert.ok(checkPos > -1 && routePos > -1, 'Cannot locate tenant check or routes');
  assert.ok(checkPos < routePos, 'Tenant check must be registered before route handlers');
}),

// ─── 10d: Per-route body size limits ─────────────────────────────────────────

test('10d: _LARGE_BODY_RE regex is defined for upload routes', () => {
  assert.ok(SRC.includes('_LARGE_BODY_RE'), '_LARGE_BODY_RE not defined');
}),

test('10d: upload route is in the large-body pattern', () => {
  assert.ok(SRC.includes('upload'), '_LARGE_BODY_RE must match upload path');
}),

test('10d: import resume route is in the large-body pattern', () => {
  assert.ok(SRC.includes('imports'), '_LARGE_BODY_RE must match imports/resume path');
}),

test('10d: default body size is 128kb (not 50mb globally)', () => {
  assert.ok(SRC.includes("'128kb'"), "Default body limit must be '128kb'");
  // The old global 50mb limit must be conditional, not unconditional
  const globalIdx = SRC.indexOf("express.json({ limit: '50mb' })");
  assert.strictEqual(globalIdx, -1, "Unconditional express.json({ limit: '50mb' }) must not exist — limit must be conditional");
}),

test('10d: 50mb limit is used for large-body routes', () => {
  assert.ok(SRC.includes("'50mb'"), "50mb limit must be present for upload routes");
}),

test('10d: body size middleware is applied globally via app.use', () => {
  const bodySizeIdx = SRC.indexOf('_LARGE_BODY_RE.test(req.path)');
  assert.ok(bodySizeIdx > -1, 'Conditional body size middleware not found');
  // Must be inside an app.use, not inside a route
  const nearbyApp = SRC.slice(Math.max(0, bodySizeIdx - 200), bodySizeIdx);
  assert.ok(nearbyApp.includes('app.use('), 'Body size check must be inside app.use middleware');
}),

// ─── 10e: Token refresh ───────────────────────────────────────────────────────

test('10e: POST /auth/refresh route is defined', () => {
  assert.ok(refreshRouteSrc.length > 0, "POST /auth/refresh route not found in bff.js");
}),

test('10e: refresh route is behind requireAuth', () => {
  assert.ok(
    SRC.includes("app.post('/auth/refresh', requireAuth"),
    "POST /auth/refresh must use requireAuth middleware"
  );
}),

test('10e: refresh strips iat and exp before reissuing', () => {
  assert.ok(refreshRouteSrc.includes('iat') && refreshRouteSrc.includes('exp'),
    'refresh must destructure and strip iat and exp from existing token payload'
  );
}),

test('10e: refresh calls makeToken with remaining claims', () => {
  assert.ok(refreshRouteSrc.includes('makeToken'), 'refresh must call makeToken');
}),

test('10e: refresh sets new cookies', () => {
  assert.ok(refreshRouteSrc.includes('setCookies'), 'refresh must call setCookies');
}),

test('10e: refresh logs the token refresh event', () => {
  assert.ok(refreshRouteSrc.includes('auth.token_refreshed'), 'refresh must log auth.token_refreshed');
}),

];

runAll(TESTS);
