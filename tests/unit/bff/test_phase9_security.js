'use strict';
/**
 * Phase 9 Verification Tests — RBAC, Rate Limiting & Security Headers
 *
 * 9a. requireRole() — source structure, correct guard expression, route wiring
 * 9b. Login rate limiting — helper presence, integration in login route, env config
 * 9c. API rate limiting — global middleware, Redis key strategy, bypass paths, fail-open
 * 9d. Security headers — all required headers present in middleware
 * 9e. Input validation — validateBody structure, schema expressions, route wiring
 *
 * Run with: node tests/unit/bff/test_phase9_security.js
 * No database or network required — source inspection only (no eval / new Function).
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

async function runAll(tests) {
  for (const t of tests) await t;
  console.log('\n── Phase 9 Security Tests ─────────────────────────────────');
  for (const r of results) {
    console.log(`  [${r.status}] ${r.name}${r.detail ? ' — ' + r.detail : ''}`);
  }
  console.log(`\n  ${passed} passed, ${failed} failed\n`);
  process.exit(failed > 0 ? 1 : 0);
}

// ─── Source slice helpers (no eval, no new Function) ──────────────────────────

// Return the source text of a named function (declaration or expression).
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

// Return the source text of a route handler by its app.METHOD prefix.
function sliceRoute(prefix) {
  const start = SRC.indexOf(prefix);
  if (start === -1) return '';
  // scan forward to find the closing }); of the async handler
  let depth = 0, i = start, inHandler = false;
  for (; i < SRC.length; i++) {
    if (SRC[i] === '{') { depth++; inHandler = true; }
    if (SRC[i] === '}') {
      depth--;
      if (inHandler && depth === 0) break;
    }
  }
  return SRC.slice(start, i + 1);
}

// ─── 9a: requireRole ──────────────────────────────────────────────────────────

const requireRoleSrc  = sliceFn('requireRole');
const validateBodySrc = sliceFn('validateBody');

const TESTS = [

test('9a: requireRole function is defined', () => {
  assert.ok(requireRoleSrc.length > 0, 'requireRole not found in bff.js');
}),

test('9a: requireRole checks role inclusion (roles.includes)', () => {
  assert.ok(requireRoleSrc.includes('roles.includes'), 'Expected roles.includes() guard in requireRole');
}),

test('9a: requireRole returns 403 on mismatch', () => {
  assert.ok(requireRoleSrc.includes('403'), 'requireRole must return HTTP 403');
  assert.ok(requireRoleSrc.includes("'forbidden'"), "requireRole must return { error: 'forbidden' }");
}),

test('9a: requireRole calls next() on match', () => {
  assert.ok(requireRoleSrc.includes('next()'), 'requireRole must call next() when role is allowed');
}),

test('9a: requireRole logs the denial', () => {
  assert.ok(requireRoleSrc.includes("log.warn"), 'requireRole must log.warn on denial');
}),

test('9a: /admin/clients is gated with requireRole(PLATFORM_ADMIN)', () => {
  assert.ok(
    SRC.includes("app.get('/admin/clients', requireAuth, requireRole('PLATFORM_ADMIN')"),
    "Expected requireRole('PLATFORM_ADMIN') on GET /admin/clients"
  );
}),

test('9a: /admin/clients/:tenantId is gated with requireRole(PLATFORM_ADMIN)', () => {
  assert.ok(
    SRC.includes("app.get('/admin/clients/:tenantId', requireAuth, requireRole('PLATFORM_ADMIN')"),
    "Expected requireRole('PLATFORM_ADMIN') on GET /admin/clients/:tenantId"
  );
}),

// ─── 9b: Login rate limiting ──────────────────────────────────────────────────

test('9b: checkLoginRateLimit is defined', () => {
  assert.ok(SRC.includes('async function checkLoginRateLimit('), 'checkLoginRateLimit missing');
}),

test('9b: checkLoginRateLimit reads from Redis and returns allowed:false when exceeded', () => {
  const src = sliceFn('checkLoginRateLimit');
  assert.ok(src.includes('redis.get'),                   'checkLoginRateLimit must read Redis counter');
  assert.ok(src.includes('allowed: false'),              'Must return { allowed: false } when limit exceeded');
  assert.ok(src.includes('retryAfter'),                  'Must include retryAfter in rejection response');
}),

test('9b: recordLoginFailure increments Redis counter and sets TTL', () => {
  const src = sliceFn('recordLoginFailure');
  assert.ok(src.includes('redis.incr'),   'recordLoginFailure must incr Redis key');
  assert.ok(src.includes('redis.expire'), 'recordLoginFailure must set TTL on first increment');
}),

test('9b: clearLoginRateLimit deletes the Redis key', () => {
  const src = sliceFn('clearLoginRateLimit');
  assert.ok(src.includes('redis.del'), 'clearLoginRateLimit must del Redis key');
}),

test('9b: login route checks rate limit before querying the database', () => {
  const loginSrc = sliceRoute("app.post('/auth/password/login'");
  const rlPos = loginSrc.indexOf('checkLoginRateLimit');
  const dbPos = loginSrc.indexOf('pool.query');
  assert.ok(rlPos > -1,  'checkLoginRateLimit not called in login route');
  assert.ok(rlPos < dbPos, 'checkLoginRateLimit must be called before the first pool.query');
}),

test('9b: login route returns 429 with Retry-After when rate limited', () => {
  const loginSrc = sliceRoute("app.post('/auth/password/login'");
  assert.ok(loginSrc.includes('429'),          'Login route must return 429 when rate limited');
  assert.ok(loginSrc.includes('Retry-After'), 'Login route must set Retry-After header');
}),

test('9b: login route clears rate limit on successful authentication', () => {
  const loginSrc = sliceRoute("app.post('/auth/password/login'");
  assert.ok(loginSrc.includes('clearLoginRateLimit'), 'clearLoginRateLimit must be called on successful login');
}),

test('9b: login route records failure on bad credentials', () => {
  const loginSrc = sliceRoute("app.post('/auth/password/login'");
  assert.ok(loginSrc.includes('recordLoginFailure'), 'recordLoginFailure must be called on failed login');
}),

test('9b: LOGIN_MAX_FAILS and LOGIN_WINDOW_S are env-configurable', () => {
  assert.ok(SRC.includes('process.env.LOGIN_MAX_FAILURES'),   'LOGIN_MAX_FAILURES env var missing');
  assert.ok(SRC.includes('process.env.LOGIN_RATE_WINDOW_S'), 'LOGIN_RATE_WINDOW_S env var missing');
}),

// ─── 9c: API rate limiting ────────────────────────────────────────────────────

test('9c: per-tenant Redis key is used for authenticated requests', () => {
  assert.ok(SRC.includes('api_rl:t:'), 'Tenant-keyed rate limit key (api_rl:t:) missing');
}),

test('9c: per-IP Redis key is used as fallback for unauthenticated requests', () => {
  assert.ok(SRC.includes('api_rl:ip:'), 'IP-keyed rate limit key (api_rl:ip:) missing');
}),

test('9c: tenant key is preferred when req.user.tenant_id is set', () => {
  const rlSection = SRC.slice(SRC.indexOf('api_rl:t:') - 200, SRC.indexOf('api_rl:t:') + 200);
  assert.ok(rlSection.includes('tenant_id'), 'tenant_id must gate the tenant key selection');
}),

test('9c: Twilio webhook and health check paths bypass rate limiting', () => {
  assert.ok(SRC.includes("'/dialer/twiml'"),   'Twilio TwiML path must bypass rate limiting');
  assert.ok(SRC.includes("'/system/health'"),  'Health check path must bypass rate limiting');
}),

test('9c: X-RateLimit headers are set on every response', () => {
  assert.ok(SRC.includes("'X-RateLimit-Limit'"),     'X-RateLimit-Limit header missing');
  assert.ok(SRC.includes("'X-RateLimit-Remaining'"), 'X-RateLimit-Remaining header missing');
}),

test('9c: rate limiter fails open (catch swallows Redis errors)', () => {
  // The catch block must not call res.status(500) — it must call next().
  const rlIdx    = SRC.indexOf('api_rl:t:');
  const rlRegion = SRC.slice(Math.max(0, rlIdx - 800), rlIdx + 800);
  const catchIdx = rlRegion.lastIndexOf('} catch');
  assert.ok(catchIdx > -1, 'No catch block found near API rate limiter');
  const catchBlock = rlRegion.slice(catchIdx, catchIdx + 80);
  assert.ok(!catchBlock.includes('status(500)'), 'Rate limiter must not return 500 on Redis error (fail open)');
}),

test('9c: API_RATE_MAX_REQUESTS and API_RATE_WINDOW_S are env-configurable', () => {
  assert.ok(SRC.includes('process.env.API_RATE_MAX_REQUESTS'), 'API_RATE_MAX_REQUESTS env var missing');
  assert.ok(SRC.includes('process.env.API_RATE_WINDOW_S'),     'API_RATE_WINDOW_S env var missing');
}),

// ─── 9d: Security headers ─────────────────────────────────────────────────────

test('9d: X-Content-Type-Options: nosniff is set', () => {
  assert.ok(SRC.includes("'X-Content-Type-Options', 'nosniff'"), 'X-Content-Type-Options: nosniff missing');
}),

test('9d: X-Frame-Options: DENY is set', () => {
  assert.ok(SRC.includes("'X-Frame-Options', 'DENY'"), 'X-Frame-Options: DENY missing');
}),

test('9d: Referrer-Policy header is set', () => {
  assert.ok(SRC.includes("'Referrer-Policy'"), 'Referrer-Policy header missing');
}),

test('9d: Permissions-Policy header is set', () => {
  assert.ok(SRC.includes("'Permissions-Policy'"), 'Permissions-Policy header missing');
}),

test('9d: HSTS header is set only in production', () => {
  assert.ok(SRC.includes("'Strict-Transport-Security'"), 'HSTS header missing');
  const hstsIdx  = SRC.indexOf("'Strict-Transport-Security'");
  const region   = SRC.slice(Math.max(0, hstsIdx - 200), hstsIdx + 100);
  assert.ok(region.includes("NODE_ENV === 'production'"), 'HSTS must be gated on NODE_ENV === production');
}),

test('9d: security headers middleware is registered before route handlers', () => {
  const headerPos = SRC.indexOf("'X-Content-Type-Options'");
  const routePos  = SRC.indexOf("app.get('/campaigns'");
  assert.ok(headerPos > -1 && routePos > -1, 'Cannot find header middleware or campaign routes');
  assert.ok(headerPos < routePos, 'Security headers middleware must be registered before route handlers');
}),

// ─── 9e: Input validation ─────────────────────────────────────────────────────

test('9e: validateBody function is defined', () => {
  assert.ok(validateBodySrc.length > 0, 'validateBody not found in bff.js');
}),

test('9e: validateBody iterates schema fields', () => {
  assert.ok(validateBodySrc.includes('Object.entries(schema)'), 'validateBody must iterate Object.entries(schema)');
}),

test('9e: validateBody checks required fields', () => {
  assert.ok(validateBodySrc.includes('rules.required'), 'validateBody must check rules.required');
}),

test('9e: validateBody enforces maxLength', () => {
  assert.ok(validateBodySrc.includes('rules.maxLength'), 'validateBody must enforce maxLength');
}),

test('9e: validateBody enforces minLength', () => {
  assert.ok(validateBodySrc.includes('rules.minLength'), 'validateBody must enforce minLength');
}),

test('9e: validateBody returns 400 with details on failure', () => {
  assert.ok(validateBodySrc.includes('400'),               'validateBody must return HTTP 400');
  assert.ok(validateBodySrc.includes("'validation_error'"), "validateBody must return { error: 'validation_error' }");
  assert.ok(validateBodySrc.includes('details'),           'validateBody must include details array');
}),

test('9e: validateBody calls next() when validation passes', () => {
  assert.ok(validateBodySrc.includes('next()'), 'validateBody must call next() when valid');
}),

test('9e: validateBody is applied to POST /campaigns', () => {
  const src = sliceRoute("app.post('/campaigns', requireAuth");
  assert.ok(src.includes('validateBody('), 'validateBody not applied to POST /campaigns');
}),

test('9e: validateBody is applied to PUT /campaigns/:id', () => {
  const src = sliceRoute("app.put('/campaigns/:id', requireAuth");
  assert.ok(src.includes('validateBody('), 'validateBody not applied to PUT /campaigns/:id');
}),

test('9e: validateBody is applied to POST /campaigns/:id/pipelines', () => {
  const src = sliceRoute("app.post('/campaigns/:id/pipelines', requireAuth");
  assert.ok(src.includes('validateBody('), 'validateBody not applied to POST /campaigns/:id/pipelines');
}),

test('9e: validateBody is applied to PATCH /campaigns/:id/pipelines/:pipelineId', () => {
  const src = sliceRoute("app.patch('/campaigns/:id/pipelines/:pipelineId', requireAuth");
  assert.ok(src.includes('validateBody('), 'validateBody not applied to PATCH /campaigns/:id/pipelines/:pipelineId');
}),

];

runAll(TESTS);
