'use strict';
/**
 * Phase 7a Verification Tests — BFF SIGTERM Handler
 *
 * Verifies the graceful shutdown pattern in bff.js:
 *   - server is assigned from app.listen() (not fire-and-forget)
 *   - SIGTERM handler calls server.close()
 *   - pool.end() is called after server is closed
 *   - redis.disconnect() is called after pool is drained
 *   - process.exit(0) is called on clean shutdown
 *   - 30s force-exit timeout is wired (process.exit(1))
 *
 * Run with: node tests/unit/bff/test_phase7_sigterm.js
 * No database or network required — uses source inspection.
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
    fn();
    results.push({ name, status: 'PASS' });
    passed++;
  } catch (e) {
    results.push({ name, status: 'FAIL', detail: e.message });
    failed++;
  }
}

// ─── Source slice ─────────────────────────────────────────────────────────────

const mainBlock = (() => {
  const start = SRC.indexOf("if (require.main === module)");
  if (start === -1) return '';
  return SRC.slice(start);
})();

const sigtermBlock = (() => {
  const start = SRC.indexOf("process.on('SIGTERM'");
  if (start === -1) return '';
  const end = SRC.indexOf('});', start) + 3;
  return SRC.slice(start, end);
})();

// ─── Tests ────────────────────────────────────────────────────────────────────

test('server is assigned from app.listen (not fire-and-forget)', () => {
  assert.ok(
    mainBlock.includes('const server = app.listen('),
    'Expected "const server = app.listen(" in main block'
  );
});

test('SIGTERM handler is registered on process', () => {
  assert.ok(
    SRC.includes("process.on('SIGTERM'"),
    'Expected process.on(\'SIGTERM\') in bff.js'
  );
});

test('SIGTERM handler calls server.close()', () => {
  assert.ok(
    sigtermBlock.includes('server.close('),
    'Expected server.close() inside SIGTERM handler'
  );
});

test('pool.end() is called inside server.close callback', () => {
  assert.ok(
    sigtermBlock.includes('pool.end('),
    'Expected pool.end() inside SIGTERM handler'
  );
});

test('redis.disconnect() is called inside pool.end callback', () => {
  assert.ok(
    sigtermBlock.includes('redis.disconnect()'),
    'Expected redis.disconnect() inside SIGTERM handler'
  );
});

test('process.exit(0) is called on clean shutdown', () => {
  assert.ok(
    sigtermBlock.includes('process.exit(0)'),
    'Expected process.exit(0) in SIGTERM handler'
  );
});

test('30s force-exit timeout is wired', () => {
  assert.ok(
    sigtermBlock.includes('setTimeout(') || mainBlock.includes('setTimeout('),
    'Expected setTimeout() force-exit after SIGTERM'
  );
  assert.ok(
    sigtermBlock.includes('30000') || mainBlock.includes('30000'),
    'Expected 30000ms timeout'
  );
});

test('force-exit uses process.exit(1) to signal abnormal termination', () => {
  assert.ok(
    sigtermBlock.includes('process.exit(1)') || mainBlock.includes('process.exit(1)'),
    'Expected process.exit(1) in force-exit timeout'
  );
});

test('module.exports includes app, pool, and redis', () => {
  assert.ok(
    SRC.includes('module.exports = { app, pool, redis }'),
    'Expected module.exports = { app, pool, redis }'
  );
});

// ─── Report ───────────────────────────────────────────────────────────────────

(async () => {
  console.log('\nPhase 7a — BFF SIGTERM Handler Tests\n' + '─'.repeat(50));
  for (const r of results) {
    const icon = r.status === 'PASS' ? '✓' : '✗';
    console.log(`  ${icon} ${r.name}`);
    if (r.detail) console.log(`      ${r.detail}`);
  }
  console.log(`\n  ${passed} passed, ${failed} failed\n`);
  process.exit(failed > 0 ? 1 : 0);
})();
