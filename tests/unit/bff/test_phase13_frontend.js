'use strict';
/**
 * Phase 13 Verification Tests — Frontend Hardening
 *
 * 13a. Shared fetch-client: ApiError + 401 redirect + bff/webapi helpers
 * 13b. Error boundaries: app/error.tsx, app/admin/error.tsx, app/client/error.tsx
 * 13c. CRM route fix: listCustomers/createCustomer use webapiGet/webapiPost
 * 13d. Import status endpoint: GET /campaigns/:id/leads/imports/:importId in bff.js
 * 13e. CRM match indicator: crm_matched/crm_unmatched displayed in upload result
 * 13f. Analytics display: getDashboardSnapshot uses webapiGet; amount_collected_minor in type
 *
 * Run with: node tests/unit/bff/test_phase13_frontend.js
 * No database or network required — source inspection only.
 */

const assert = require('assert');
const path   = require('path');
const fs     = require('fs');

const ROOT     = path.resolve(__dirname, '../../..');
const BFF_SRC  = fs.readFileSync(path.join(ROOT, 'bff.js'), 'utf8');
const FE_ROOT  = path.join(ROOT, 'frontend');

function readFe(rel) {
  return fs.readFileSync(path.join(FE_ROOT, rel), 'utf8');
}

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

// ── 13a: Shared fetch-client ───────────────────────────────────────────────────

const fetchClient = readFe('lib/api/fetch-client.ts');

test('13a: fetch-client exports ApiError class', () => {
  assert(fetchClient.includes('export class ApiError'), 'ApiError not exported');
});

test('13a: fetch-client redirects to /login on 401', () => {
  assert(fetchClient.includes('/login?returnUrl='), '401 redirect to /login not found');
  assert(fetchClient.includes('res.status === 401'), '401 status check missing');
});

test('13a: fetch-client exports bffGet and bffPost', () => {
  assert(fetchClient.includes('export async function bffGet'), 'bffGet not exported');
  assert(fetchClient.includes('export async function bffPost'), 'bffPost not exported');
});

test('13a: fetch-client exports webapiGet and webapiPost', () => {
  assert(fetchClient.includes('export async function webapiGet'), 'webapiGet not exported');
  assert(fetchClient.includes('export async function webapiPost'), 'webapiPost not exported');
});

test('13a: campaigns.ts imports from fetch-client (no local ApiError)', () => {
  const src = readFe('lib/api/campaigns.ts');
  assert(src.includes("from \"@/lib/api/fetch-client\""), 'campaigns.ts does not import fetch-client');
  assert(!src.includes('class ApiError'), 'campaigns.ts still defines ApiError locally');
});

test('13a: clients.ts imports from fetch-client (no local ApiError)', () => {
  const src = readFe('lib/api/clients.ts');
  assert(src.includes("from \"@/lib/api/fetch-client\""), 'clients.ts does not import fetch-client');
  assert(!src.includes('class ApiError'), 'clients.ts still defines ApiError locally');
});

test('13a: hitl.ts imports from fetch-client, checks 401 before 404', () => {
  const src = readFe('lib/api/hitl.ts');
  assert(src.includes("from \"@/lib/api/fetch-client\""), 'hitl.ts does not import fetch-client');
  const idx401 = src.indexOf('401');
  const idx404 = src.indexOf('404');
  assert(idx401 < idx404, '401 check must come before 404 check in claimNextHITLItem');
});

test('13a: team.ts imports bffDel for DELETE endpoint', () => {
  const src = readFe('lib/api/team.ts');
  assert(src.includes('bffDel'), 'team.ts does not use bffDel for deactivateTeamMember');
});

test('13a: admin-ops.ts imports webapiGet/webapiPost from fetch-client', () => {
  const src = readFe('lib/api/admin-ops.ts');
  assert(src.includes("from \"@/lib/api/fetch-client\""), 'admin-ops.ts does not import fetch-client');
  assert(!src.includes('class ApiError'), 'admin-ops.ts still defines ApiError locally');
});

// ── 13b: Error boundaries ─────────────────────────────────────────────────────

test('13b: app/error.tsx exists and is "use client"', () => {
  const src = readFe('app/error.tsx');
  assert(src.includes('"use client"'), 'app/error.tsx missing "use client" directive');
});

test('13b: app/admin/error.tsx exists and is "use client"', () => {
  const src = readFe('app/admin/error.tsx');
  assert(src.includes('"use client"'), 'app/admin/error.tsx missing "use client" directive');
});

test('13b: app/client/error.tsx exists and is "use client"', () => {
  const src = readFe('app/client/error.tsx');
  assert(src.includes('"use client"'), 'app/client/error.tsx missing "use client" directive');
});

test('13b: error boundaries expose a reset/refresh action', () => {
  const src = readFe('app/client/error.tsx');
  assert(src.includes('reset'), 'client error boundary does not expose reset()');
});

// ── 13c: CRM route fix ────────────────────────────────────────────────────────

test('13c: listCustomers uses webapiGet (not bffGet)', () => {
  const src = readFe('lib/api/client-ops.ts');
  assert(src.includes('webapiGet<Customer[]>("/crm/customers")'), 'listCustomers does not route to webapi');
  assert(!src.includes('bffGet<Customer[]>("/crm/customers")'), 'listCustomers still routes to bff');
});

test('13c: createCustomer uses webapiPost (not bffPost)', () => {
  const src = readFe('lib/api/client-ops.ts');
  assert(src.includes('webapiPost<Customer>("/crm/customers"'), 'createCustomer does not route to webapi');
});

// ── 13d: Import status endpoint ───────────────────────────────────────────────

test('13d: bff.js has GET /campaigns/:id/leads/imports/:importId route', () => {
  assert(
    BFF_SRC.includes("app.get('/campaigns/:id/leads/imports/:importId'"),
    'bff.js missing GET /campaigns/:id/leads/imports/:importId'
  );
});

test('13d: import status route checks tenant_id isolation', () => {
  const routeStart = BFF_SRC.indexOf("app.get('/campaigns/:id/leads/imports/:importId'");
  const routeEnd   = BFF_SRC.indexOf('\n});', routeStart) + 3;
  const routeSrc   = BFF_SRC.slice(routeStart, routeEnd);
  assert(routeSrc.includes('tenant_id'), 'import status route does not scope by tenant_id');
});

test('13d: campaigns.ts exports getImportStatus function', () => {
  const src = readFe('lib/api/campaigns.ts');
  assert(src.includes('export const getImportStatus'), 'getImportStatus not exported from campaigns.ts');
});

test('13d: campaign-leads-view polls import progress via setInterval', () => {
  const src = readFe('components/client/campaign-leads-view.tsx');
  assert(src.includes('setInterval'), 'campaign-leads-view missing progress polling');
  assert(src.includes('last_processed_row'), 'polling does not use last_processed_row');
});

// ── 13e: CRM match indicator ──────────────────────────────────────────────────

test('13e: campaign-leads-view captures crm_matched in upload result', () => {
  const src = readFe('components/client/campaign-leads-view.tsx');
  assert(src.includes('crm_matched'), 'crm_matched not captured in upload result');
  assert(src.includes('crm_unmatched'), 'crm_unmatched not captured in upload result');
});

test('13e: unmatched tooltip warns about "account not found"', () => {
  const src = readFe('components/client/campaign-leads-view.tsx');
  assert(src.includes('account not found'), 'CRM unmatched tooltip missing "account not found" message');
});

// ── 13f: Analytics display ────────────────────────────────────────────────────

test('13f: getDashboardSnapshot uses webapiGet (not bffGet)', () => {
  const src = readFe('lib/api/client-ops.ts');
  assert(src.includes('webapiGet<DashboardSnapshot>'), 'getDashboardSnapshot does not use webapiGet');
});

test('13f: DashboardSnapshot type includes amount_collected_minor', () => {
  const src = readFe('lib/api/client-ops.ts');
  assert(src.includes('amount_collected_minor'), 'DashboardSnapshot missing amount_collected_minor field');
});

test('13f: analytics-view displays amount_collected_minor as rupees', () => {
  const src = readFe('components/client/analytics-view.tsx');
  assert(src.includes('amount_collected_minor'), 'analytics-view does not reference amount_collected_minor');
  assert(src.includes('en-IN'), 'analytics-view not formatting rupees with en-IN locale');
});

// ── Summary ───────────────────────────────────────────────────────────────────

console.log('\nPhase 13 Frontend Hardening Tests');
console.log('══════════════════════════════════\n');
for (const r of results) {
  const icon = r.status === 'PASS' ? '✓' : '✗';
  console.log(`  ${icon} ${r.name}`);
  if (r.detail) console.log(`      → ${r.detail}`);
}
console.log(`\n  ${passed} passed, ${failed} failed out of ${passed + failed} total\n`);
process.exit(failed > 0 ? 1 : 0);
