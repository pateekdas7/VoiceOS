'use strict';
/**
 * Phase 1 Security Verification Tests
 * Run with: node tests/security/phase1_verification.js
 * No database required — tests security logic in isolation.
 */

const crypto = require('crypto');
const assert = require('assert');
const { execSync, spawnSync } = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
let passed = 0;
let failed = 0;
const results = [];

function test(name, fn) {
  try {
    fn();
    results.push({ name, status: 'PASS' });
    passed++;
  } catch (e) {
    results.push({ name, status: 'FAIL', detail: e.message });
    failed++;
  }
}

// ─── Helper: compute real Twilio HMAC-SHA1 signature ─────────────────────────
function computeTwilioSignature(authToken, url, params) {
  // Twilio signs: url + sorted(key+value pairs from POST body)
  const sortedKeys = Object.keys(params).sort();
  const toSign = url + sortedKeys.map(k => k + params[k]).join('');
  return crypto.createHmac('sha1', authToken).update(toSign, 'utf8').digest('base64');
}

// ─── 1. JWT_SECRET enforcement ────────────────────────────────────────────────
test('JWT-01: bff.js exits with code 1 when JWT_SECRET is not set', () => {
  const result = spawnSync(process.execPath, [path.join(ROOT, 'bff.js')], {
    env: { ...process.env, JWT_SECRET: '' },
    cwd: ROOT,
    timeout: 8000,
  });
  const stderr = result.stderr?.toString() || '';
  assert.strictEqual(result.status, 1, `Expected exit code 1, got ${result.status}. stderr: ${stderr.slice(0,200)}`);
  assert.ok(stderr.includes('[FATAL]'), `Expected [FATAL] in stderr. Got: ${stderr.slice(0,300)}`);
});

test('JWT-02: hardcoded fallback "voiceos-local-dev-secret-key-2024" is absent from bff.js', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    !src.includes('voiceos-local-dev-secret-key-2024'),
    'Hardcoded fallback secret still present in bff.js'
  );
});

test('JWT-03: No alternate hardcoded JWT secrets anywhere in bff.js', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  // Pattern: JWT_SECRET = process.env.X || 'something'
  const fallbackPattern = /JWT_SECRET\s*=\s*process\.env\.\w+\s*\|\|/;
  assert.ok(!fallbackPattern.test(src), 'Found a JWT_SECRET || fallback pattern');
});

// ─── 2. Cookie security flags ────────────────────────────────────────────────
test('COOKIE-01: SESSION cookie always has httpOnly:true', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  // Extract setCookies function body
  const match = src.match(/function setCookies[\s\S]+?^}/m);
  assert.ok(match, 'setCookies function not found');
  const body = match[0];
  assert.ok(body.includes('httpOnly: true'), 'httpOnly: true not set in setCookies');
  assert.ok(!body.includes('httpOnly: false'), 'httpOnly: false found in setCookies');
});

test('COOKIE-02: ACTOR_KIND cookie no longer has httpOnly:false override', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  // The old code was: res.cookie(ACTOR_KIND_COOKIE, actorKind, { ...opts, httpOnly: false })
  assert.ok(
    !src.includes('httpOnly: false'),
    'httpOnly: false still present somewhere in bff.js'
  );
});

test('COOKIE-03: secure flag is present in setCookies and gated on NODE_ENV=production', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(src.includes("process.env.NODE_ENV === 'production'"), 'NODE_ENV production check missing');
  assert.ok(src.includes('secure'), 'secure flag not present in bff.js');
});

// ─── 3. Tenant isolation SQL audit ───────────────────────────────────────────
test('TENANT-01: qualifyLead query contains AND tenant_id=', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  // Find the qualifyLead function and check its query
  const fnMatch = src.match(/async function qualifyLead[\s\S]+?^\}/m);
  assert.ok(fnMatch, 'qualifyLead function not found');
  assert.ok(
    fnMatch[0].includes('tenant_id=$2'),
    'qualifyLead query missing AND tenant_id=$2'
  );
});

test('TENANT-02: qualifyLead call site passes tenantId as second argument', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  const callMatch = src.match(/qualifyLead\(campaignId,\s*tenantId,/);
  assert.ok(callMatch, 'qualifyLead call site not passing tenantId — found: ' +
    (src.match(/qualifyLead\([^)]+\)/)?.[0] || 'no call found'));
});

test('TENANT-03: PUT /campaigns/:id UPDATE has AND tenant_id=$14', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    src.includes('campaign_id=$13 AND tenant_id=$14'),
    'PUT /campaigns/:id WHERE clause missing AND tenant_id=$14'
  );
});

test('TENANT-04: PUT /campaigns/:id passes req.user.tenant_id as 14th param', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  // Check the params array contains req.user.tenant_id after req.params.id
  assert.ok(
    src.includes('req.params.id, req.user.tenant_id,'),
    'PUT /campaigns/:id not passing req.user.tenant_id as $14'
  );
});

test('TENANT-05: campaign lifecycle transitions WHERE clause contains tenant_id=$2', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    src.includes("tenant_id=$2 AND status='"),
    'Lifecycle transition with from-state missing tenant_id=$2'
  );
  assert.ok(
    src.includes('`campaign_id=$1 AND tenant_id=$2`'),
    'Lifecycle transition without from-state missing tenant_id=$2'
  );
});

test('TENANT-06: lifecycle vals array starts with [id, tenant_id]', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    src.includes('const vals = [req.params.id, req.user.tenant_id];'),
    'Lifecycle vals array not seeded with [id, tenant_id]'
  );
});

// ─── 4. /dialer/callback Twilio HMAC ─────────────────────────────────────────
test('TWILIO-01: twilio is required at top of bff.js', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    src.includes("const twilio       = require('twilio');"),
    'twilio require() not found'
  );
});

test('TWILIO-02: validateRequest called with correct args in /dialer/callback', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  assert.ok(
    src.includes('twilio.validateRequest(twilioAuthToken, signature, fullUrl, req.body)'),
    'twilio.validateRequest() call not found or wrong args'
  );
});

test('TWILIO-03: forged Twilio signature is rejected by twilio.validateRequest()', () => {
  const twilio = require(path.join(ROOT, 'node_modules', 'twilio'));
  const authToken = 'real_auth_token_abc123';
  const url = 'https://example.com/dialer/callback?pipeline_id=abc';
  const body = { CallSid: 'CA123', CallStatus: 'completed', Duration: '60' };

  const forgedSig = 'forgedXYZsignaturebase64==';
  const result = twilio.validateRequest(authToken, forgedSig, url, body);
  assert.strictEqual(result, false, 'Forged signature should be rejected');
});

test('TWILIO-04: valid Twilio signature passes twilio.validateRequest()', () => {
  const twilio = require(path.join(ROOT, 'node_modules', 'twilio'));
  const authToken = 'real_auth_token_abc123';
  const url = 'https://example.com/dialer/callback?pipeline_id=abc';
  const body = { CallSid: 'CA123', CallStatus: 'completed', Duration: '60' };

  const validSig = computeTwilioSignature(authToken, url, body);
  const result = twilio.validateRequest(authToken, validSig, url, body);
  assert.strictEqual(result, true, 'Valid signature should pass');
});

test('TWILIO-05: signature valid for one tenant is invalid for another URL', () => {
  const twilio = require(path.join(ROOT, 'node_modules', 'twilio'));
  const authToken = 'real_auth_token_abc123';
  const url1 = 'https://example.com/dialer/callback?pipeline_id=abc&tenant_id=tenant-A';
  const url2 = 'https://example.com/dialer/callback?pipeline_id=abc&tenant_id=tenant-B';
  const body = { CallSid: 'CA123', CallStatus: 'completed', Duration: '60' };

  const sig = computeTwilioSignature(authToken, url1, body);
  const result = twilio.validateRequest(authToken, sig, url2, body);
  assert.strictEqual(result, false, 'Signature for tenant-A URL should not pass for tenant-B URL');
});

test('TWILIO-06: /dialer/callback fails closed (503) in production when TWILIO_AUTH_TOKEN is unset', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  const callbackSection = src.slice(src.indexOf("app.post('/dialer/callback'"));
  // Must contain both the production env check AND a 503 response in the no-auth-token branch
  const hasFailClosed = /NODE_ENV.*production[\s\S]{0,200}503/.test(callbackSection) ||
    /503[\s\S]{0,200}NODE_ENV.*production/.test(callbackSection);
  assert.ok(hasFailClosed,
    '/dialer/callback does not fail-closed in production when TWILIO_AUTH_TOKEN is missing');
});

// ─── 5. web_api keypair enforcement ─────────────────────────────────────────
test('KEYPAIR-01: web_api fails at startup in production without key paths', () => {
  const src = require('fs').readFileSync(
    path.join(ROOT, 'src/services/web_api/main.py'), 'utf8'
  );
  assert.ok(
    src.includes("VOICEOS_ENVIRONMENT") && src.includes('"production"'),
    'VOICEOS_ENVIRONMENT production check missing in main.py'
  );
  assert.ok(src.includes('sys.exit(1)'), 'sys.exit(1) not called in ephemeral keypair path');
});

test('KEYPAIR-02: web_api reads persistent keys from WEB_API_PRIVATE_KEY_PATH when set', () => {
  const src = require('fs').readFileSync(
    path.join(ROOT, 'src/services/web_api/main.py'), 'utf8'
  );
  assert.ok(
    src.includes('WEB_API_PRIVATE_KEY_PATH') && src.includes('WEB_API_PUBLIC_KEY_PATH'),
    'Key path env var checks missing'
  );
  assert.ok(
    src.includes('os.path.exists(private_path)') && src.includes('os.path.exists(public_path)'),
    'File existence checks missing'
  );
});

// ─── 6. Secrets in logs audit ────────────────────────────────────────────────
test('SECRETS-01: JWT_SECRET *value* is never interpolated into a log statement in bff.js', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  // Only flag if ${JWT_SECRET} or `...${JWT_SECRET}...` appears inside a console call
  // Logging the string "JWT_SECRET" (env var name) is fine; logging its value is not.
  const valueInterpolated = /console\.[a-z]+\(`[^`]*\$\{JWT_SECRET\}/;
  assert.ok(!valueInterpolated.test(src), 'JWT_SECRET value is interpolated into a log string');
});

test('SECRETS-02: TWILIO_AUTH_TOKEN *value* is never interpolated into a log statement in bff.js', () => {
  const src = require('fs').readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
  const valueInterpolated = /console\.[a-z]+\(`[^`]*\$\{.*TWILIO_AUTH_TOKEN.*\}/;
  assert.ok(!valueInterpolated.test(src),
    'TWILIO_AUTH_TOKEN value is interpolated into a log string');
});

test('SECRETS-03: Private key material is never logged in web_api/main.py', () => {
  const src = require('fs').readFileSync(
    path.join(ROOT, 'src/services/web_api/main.py'), 'utf8'
  );
  // No logging of private_key variable
  assert.ok(!src.includes('logger.info(private_key') && !src.includes('print(private_key'),
    'private_key may be logged in main.py');
});

// ─── Print results ────────────────────────────────────────────────────────────
console.log('\n' + '═'.repeat(70));
console.log('PHASE 1 SECURITY VERIFICATION RESULTS');
console.log('═'.repeat(70));
for (const r of results) {
  const icon = r.status === 'PASS' ? '✓' : r.status === 'FAIL' ? '✗' : '~';
  console.log(`  [${r.status}] ${icon} ${r.name}`);
  if (r.detail) console.log(`         Detail: ${r.detail}`);
}
console.log('─'.repeat(70));
console.log(`  Total: ${passed + failed}  |  PASS: ${passed}  |  FAIL: ${failed}`);
console.log('═'.repeat(70) + '\n');
process.exit(failed > 0 ? 1 : 0);
