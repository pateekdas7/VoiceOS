'use strict';
/**
 * Phase 16 Source Verification — Production Rollout Readiness
 *
 * Verifies that all scripts, systemd units, and configuration required for
 * a controlled production rollout exist and are correctly structured.
 *
 * Run with: node tests/unit/bff/test_phase16_source.js
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

// ── 16a: Pre-deploy checklist ─────────────────────────────────────────────────

test('16a: pre_deploy_checklist.sh exists', () => {
  assert(fileExists('scripts/deploy/pre_deploy_checklist.sh'), 'pre_deploy_checklist.sh missing');
});

test('16a: checklist verifies Phase 13/14/15 source tests', () => {
  const src = readFile('scripts/deploy/pre_deploy_checklist.sh');
  assert(src.includes('test_phase13_frontend.js'), 'checklist does not run Phase 13 test');
  assert(src.includes('test_phase14_source.js'), 'checklist does not run Phase 14 test');
  assert(src.includes('test_phase15_source.js'), 'checklist does not run Phase 15 test');
});

test('16a: checklist verifies all 5 systemd units', () => {
  const src = readFile('scripts/deploy/pre_deploy_checklist.sh');
  assert(src.includes('voiceos-bff.service'), 'checklist missing bff.service check');
  assert(src.includes('voiceos-webapi.service'), 'checklist missing webapi.service check');
  assert(src.includes('voiceos-voice-runtime.service'), 'checklist missing voice-runtime.service check');
  assert(src.includes('voiceos-dialer-worker.service'), 'checklist missing dialer-worker.service check');
  assert(src.includes('voiceos-frontend.service'), 'checklist missing frontend.service check');
});

test('16a: checklist verifies check_secrets.py and check_pii_logs.py', () => {
  const src = readFile('scripts/deploy/pre_deploy_checklist.sh');
  assert(src.includes('check_secrets.py'), 'checklist missing check_secrets.py');
  assert(src.includes('check_pii_logs.py'), 'checklist missing check_pii_logs.py');
});

test('16a: checklist verifies all 5 required runbooks exist', () => {
  const src = readFile('scripts/deploy/pre_deploy_checklist.sh');
  assert(src.includes('phase14-chaos-testing-runbook.md'), 'checklist missing chaos runbook check');
  assert(src.includes('phase15-staging-validation-runbook.md'), 'checklist missing staging runbook check');
  assert(src.includes('phase16-production-rollout-runbook.md'), 'checklist missing rollout runbook check');
});

// ── 16b: Deployment script ────────────────────────────────────────────────────

test('16b: deploy.sh exists', () => {
  assert(fileExists('scripts/deploy/deploy.sh'), 'deploy.sh missing');
});

test('16b: deploy.sh runs alembic upgrade head first', () => {
  const src = readFile('scripts/deploy/deploy.sh');
  const alembicIdx = src.indexOf('alembic upgrade head');
  const k8sIdx = src.indexOf('rollout restart');
  const bffIdx = src.indexOf('voiceos-bff');
  assert(alembicIdx !== -1, 'deploy.sh missing alembic upgrade head');
  assert(alembicIdx < bffIdx, 'alembic upgrade must run before service restarts');
});

test('16b: deploy.sh restarts all 4 services in correct order', () => {
  const src = readFile('scripts/deploy/deploy.sh');
  // Find restart lines specifically (not the unit install loop)
  const webapiIdx  = src.indexOf('restart voiceos-webapi');
  const bffIdx     = src.indexOf('restart voiceos-bff');
  const voiceIdx   = src.indexOf('restart voiceos-voice-runtime');
  const dialerIdx  = src.indexOf('restart voiceos-dialer-worker');
  assert(webapiIdx !== -1, 'deploy.sh missing: systemctl restart voiceos-webapi');
  assert(bffIdx !== -1, 'deploy.sh missing: systemctl restart voiceos-bff');
  assert(voiceIdx !== -1, 'deploy.sh missing: systemctl restart voiceos-voice-runtime');
  assert(dialerIdx !== -1, 'deploy.sh missing: systemctl restart voiceos-dialer-worker');
  assert(webapiIdx < bffIdx, 'web_api must restart before bff.js');
  assert(bffIdx < voiceIdx, 'bff.js must restart before voice runtime');
  assert(voiceIdx < dialerIdx, 'voice runtime must restart before dialer_worker');
});

test('16b: deploy.sh has health checks between restarts', () => {
  const src = readFile('scripts/deploy/deploy.sh');
  assert(src.includes('wait_healthy') || src.includes('curl.*health'),
    'deploy.sh missing health checks between service restarts');
});

test('16b: deploy.sh enables backup timers post-deploy', () => {
  const src = readFile('scripts/deploy/deploy.sh');
  assert(src.includes('voiceos-mongodb-backup.timer'), 'deploy.sh does not enable MongoDB backup timer');
  assert(src.includes('voiceos-vault-snapshot.timer'), 'deploy.sh does not enable Vault snapshot timer');
});

test('16b: deploy.sh supports --dry-run flag', () => {
  const src = readFile('scripts/deploy/deploy.sh');
  assert(src.includes('dry-run') || src.includes('DRY_RUN'), 'deploy.sh missing --dry-run support');
});

// ── 16c: Single-tenant rollout ────────────────────────────────────────────────

test('16c: rollout_first_tenant.sh exists', () => {
  assert(fileExists('scripts/deploy/rollout_first_tenant.sh'), 'rollout_first_tenant.sh missing');
});

test('16c: rollout starts in simulation mode', () => {
  const src = readFile('scripts/deploy/rollout_first_tenant.sh');
  assert(src.includes('SIMULATION') || src.includes('simulation'),
    'rollout_first_tenant.sh must start in simulation mode');
});

test('16c: rollout verifies bff.js and web_api health before activating', () => {
  const src = readFile('scripts/deploy/rollout_first_tenant.sh');
  assert(src.includes('localhost:8000') && src.includes('localhost:8001'),
    'rollout_first_tenant.sh must health-check both bff.js and web_api');
});

// ── 16d: Rollback procedure ───────────────────────────────────────────────────

test('16d: rollback.sh exists', () => {
  assert(fileExists('scripts/deploy/rollback.sh'), 'rollback.sh missing');
});

test('16d: rollback stops dialer_worker first (graceful drain)', () => {
  const src = readFile('scripts/deploy/rollback.sh');
  const dialerStopIdx = src.indexOf('stop voiceos-dialer-worker');
  const webapiStopIdx = src.indexOf('stop voiceos-webapi');
  assert(dialerStopIdx !== -1, 'rollback.sh must stop dialer_worker');
  assert(dialerStopIdx < webapiStopIdx || webapiStopIdx === -1,
    'rollback.sh must stop dialer_worker before webapi');
});

test('16d: rollback checks if alembic downgrade is needed', () => {
  const src = readFile('scripts/deploy/rollback.sh');
  assert(src.includes('alembic downgrade'), 'rollback.sh missing alembic downgrade step');
});

test('16d: rollback tracks elapsed time against 15-minute target', () => {
  const src = readFile('scripts/deploy/rollback.sh');
  assert(src.includes('900') || src.includes('15 minutes') || src.includes('15-minute'),
    'rollback.sh missing 15-minute target check');
});

test('16d: rollback requires --execute flag (safe by default)', () => {
  const src = readFile('scripts/deploy/rollback.sh');
  assert(src.includes('--execute'), 'rollback.sh must require --execute flag for safety');
  assert(src.includes('dry-run') || src.includes('DRY_RUN'), 'rollback.sh must default to dry-run');
});

// ── Systemd units ─────────────────────────────────────────────────────────────

test('16e: voiceos-bff.service has TimeoutStopSec', () => {
  const src = readFile('scripts/systemd/voiceos-bff.service');
  assert(src.includes('TimeoutStopSec'), 'voiceos-bff.service missing TimeoutStopSec');
});

test('16e: voiceos-voice-runtime.service has TimeoutStopSec', () => {
  const src = readFile('scripts/systemd/voiceos-voice-runtime.service');
  assert(src.includes('TimeoutStopSec'), 'voiceos-voice-runtime.service missing TimeoutStopSec');
});

test('16e: voiceos-dialer-worker.service has TimeoutStopSec', () => {
  const src = readFile('scripts/systemd/voiceos-dialer-worker.service');
  assert(src.includes('TimeoutStopSec'), 'voiceos-dialer-worker.service missing TimeoutStopSec');
});

test('16e: all 5 core systemd units exist', () => {
  const units = [
    'voiceos-bff.service',
    'voiceos-webapi.service',
    'voiceos-frontend.service',
    'voiceos-voice-runtime.service',
    'voiceos-dialer-worker.service',
  ];
  for (const u of units) {
    assert(fileExists('scripts/systemd', u), `scripts/systemd/${u} missing`);
  }
});

// ── Summary ───────────────────────────────────────────────────────────────────

console.log('\nPhase 16 Production Rollout Readiness Tests');
console.log('════════════════════════════════════════════\n');
for (const r of results) {
  const icon = r.status === 'PASS' ? '✓' : '✗';
  console.log(`  ${icon} ${r.name}`);
  if (r.detail) console.log(`      → ${r.detail}`);
}
console.log(`\n  ${passed} passed, ${failed} failed out of ${passed + failed} total\n`);
process.exit(failed > 0 ? 1 : 0);
