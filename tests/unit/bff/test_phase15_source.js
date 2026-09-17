'use strict';
/**
 * Phase 15 Source Verification — Staging Validation Readiness
 *
 * Checks that the CODE and SCRIPTS required for staging validation exist and
 * are properly structured. These are static source-inspection checks — they
 * do not require a running staging environment.
 *
 * Actual execution of the 6 staging scenarios, load test, and security scan
 * is deferred to post-Phase-16 (real staging environment required).
 *
 * Run with: node tests/unit/bff/test_phase15_source.js
 */

const assert = require('assert');
const path   = require('path');
const fs     = require('fs');

const ROOT = path.resolve(__dirname, '../../..');

function readFile(...parts) {
  return fs.readFileSync(path.join(ROOT, ...parts), 'utf8');
}

function fileExists(...parts) {
  return fs.existsSync(path.join(ROOT, ...parts));
}

let passed = 0, failed = 0;
const results = [];

function test(name, fn) {
  try { fn(); results.push({ name, status: 'PASS' }); passed++; }
  catch (e) { results.push({ name, status: 'FAIL', detail: e.message }); failed++; }
}

// ── 15a: Staging environment scripts ─────────────────────────────────────────

test('15a: setup_staging.sh exists', () => {
  assert(fileExists('scripts/staging/setup_staging.sh'), 'scripts/staging/setup_staging.sh missing');
});

test('15a: setup_staging.sh creates staging PostgreSQL database', () => {
  const src = readFile('scripts/staging/setup_staging.sh');
  assert(src.includes('voiceos_staging'), 'setup_staging.sh does not reference voiceos_staging DB');
});

test('15a: setup_staging.sh applies alembic migrations', () => {
  const src = readFile('scripts/staging/setup_staging.sh');
  assert(src.includes('alembic upgrade head'), 'setup_staging.sh does not apply alembic migrations');
});

test('15a: setup_staging.sh starts staging bff.js on separate port', () => {
  const src = readFile('scripts/staging/setup_staging.sh');
  assert(src.includes('STAGING_BFF_PORT') || src.includes('8100'), 'setup_staging.sh missing staging BFF port');
});

test('15a: teardown_staging.sh exists', () => {
  assert(fileExists('scripts/staging/teardown_staging.sh'), 'scripts/staging/teardown_staging.sh missing');
});

// ── 15b: End-to-end test scenarios ───────────────────────────────────────────

test('15b: test_phase15_staging.py exists', () => {
  assert(fileExists('tests/e2e/test_phase15_staging.py'), 'tests/e2e/test_phase15_staging.py missing');
});

test('15b: staging E2E covers all 6 scenarios', () => {
  const src = readFile('tests/e2e/test_phase15_staging.py');
  assert(src.includes('scenario_1_full_happy_path'), 'Scenario 1 missing');
  assert(src.includes('scenario_2_post_call_durability'), 'Scenario 2 missing');
  assert(src.includes('scenario_3_hitl_escalation'), 'Scenario 3 missing');
  assert(src.includes('scenario_4_import_resume'), 'Scenario 4 missing');
  assert(src.includes('scenario_5_crash_recovery'), 'Scenario 5 missing');
  assert(src.includes('scenario_6_billing'), 'Scenario 6 missing');
});

test('15b: staging E2E Scenario 1 covers tenant→campaign→leads→import', () => {
  const src = readFile('tests/e2e/test_phase15_staging.py');
  const s1 = src.slice(src.indexOf('scenario_1_full_happy_path'));
  assert(s1.includes('tenant_id'), 'Scenario 1: tenant creation missing');
  assert(s1.includes('campaign_id'), 'Scenario 1: campaign creation missing');
  assert(s1.includes('import_id'), 'Scenario 1: lead upload missing');
  assert(s1.includes('DONE'), 'Scenario 1: import DONE polling missing');
});

test('15b: staging E2E Scenario 3 handles HITL 404 gracefully', () => {
  const src = readFile('tests/e2e/test_phase15_staging.py');
  const s3 = src.slice(src.indexOf('scenario_3_hitl_escalation'));
  assert(s3.includes('404'), 'Scenario 3: 404 handling missing for empty HITL queue');
});

test('15b: staging E2E Scenario 5 covers crash recovery via reconciliation', () => {
  const src = readFile('tests/e2e/test_phase15_staging.py');
  const s5 = src.slice(src.indexOf('scenario_5_crash_recovery'));
  assert(s5.includes('SIGKILL') || s5.includes('-9'), 'Scenario 5: SIGKILL missing');
  assert(s5.includes('recovery_log'), 'Scenario 5: recovery_log check missing');
});

// ── 15c: Load test ────────────────────────────────────────────────────────────

test('15c: locustfile_bff.py exists', () => {
  assert(fileExists('tests/load/locustfile_bff.py'), 'tests/load/locustfile_bff.py missing');
});

test('15c: BFF load test targets 50 concurrent users (not 500)', () => {
  const src = readFile('tests/load/locustfile_bff.py');
  assert(src.includes('50'), 'locustfile_bff.py does not mention 50 concurrent users');
  assert(!src.includes('--users 500'), 'locustfile_bff.py should not target 500 users (that is the GPU test)');
});

test('15c: BFF load test has gate assertions (no 5xx, p95 < 2000ms)', () => {
  const src = readFile('tests/load/locustfile_bff.py');
  assert(src.includes('process_exit_code'), 'load test missing exit code gate assertions');
  assert(src.includes('2000'), 'load test missing p95 gate of 2000ms');
});

test('15c: BFF load test exercises list_campaigns, HITL, and analytics', () => {
  const src = readFile('tests/load/locustfile_bff.py');
  assert(src.includes('list_campaigns') || src.includes('/campaigns'), 'load test missing campaigns task');
  assert(src.includes('/hitl/'), 'load test missing HITL task');
  assert(src.includes('analytics') || src.includes('dashboard'), 'load test missing analytics task');
});

// ── 15d: Security scan ────────────────────────────────────────────────────────

test('15d: phase15_security_scan.sh exists', () => {
  assert(fileExists('tests/security/phase15_security_scan.sh'), 'tests/security/phase15_security_scan.sh missing');
});

test('15d: security scan runs gitleaks', () => {
  const src = readFile('tests/security/phase15_security_scan.sh');
  assert(src.includes('gitleaks'), 'security scan does not include gitleaks');
});

test('15d: security scan runs OWASP ZAP', () => {
  const src = readFile('tests/security/phase15_security_scan.sh');
  assert(src.includes('zap') || src.includes('ZAP'), 'security scan does not include OWASP ZAP');
});

test('15d: security scan runs trufflehog', () => {
  const src = readFile('tests/security/phase15_security_scan.sh');
  assert(src.includes('trufflehog'), 'security scan does not include trufflehog');
});

test('15d: security scan checks PII/secrets in logs', () => {
  const src = readFile('tests/security/phase15_security_scan.sh');
  assert(src.includes('check_pii_logs'), 'security scan does not invoke check_pii_logs.py');
});

// ── bff.js route coverage for staging E2E ────────────────────────────────────

test('15e: bff.js has /campaigns route (Scenario 1)', () => {
  const bffSrc = readFile('bff.js');
  assert(bffSrc.includes("app.post('/campaigns'") || bffSrc.includes('app.post("/campaigns"'),
    'bff.js missing POST /campaigns');
});

test('15e: hitl.ts frontend calls /hitl/queue/claim-next (Scenario 3)', () => {
  const hitlSrc = readFile('frontend/lib/api/hitl.ts');
  assert(hitlSrc.includes('claim-next'), 'hitl.ts missing /hitl/queue/claim-next call');
});

test('15e: bff.js has import status route (Scenario 4)', () => {
  const bffSrc = readFile('bff.js');
  assert(bffSrc.includes("app.get('/campaigns/:id/leads/imports/:importId'"),
    'bff.js missing import status route required for Scenario 4');
});

// ── Summary ───────────────────────────────────────────────────────────────────

console.log('\nPhase 15 Staging Validation Readiness Tests');
console.log('════════════════════════════════════════════\n');
for (const r of results) {
  const icon = r.status === 'PASS' ? '✓' : '✗';
  console.log(`  ${icon} ${r.name}`);
  if (r.detail) console.log(`      → ${r.detail}`);
}
console.log(`\n  ${passed} passed, ${failed} failed out of ${passed + failed} total\n`);
process.exit(failed > 0 ? 1 : 0);
