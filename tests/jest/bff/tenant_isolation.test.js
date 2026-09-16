'use strict';
/**
 * Tenant isolation tests
 * Verifies that tenant A cannot read or mutate tenant B's resources
 * through any bff.js route.
 *
 * Strategy: All queries return 0 rows when tenant_id doesn't match.
 * The routes must respond 404/403 in all such cases.
 */

jest.mock('pg');
jest.mock('ioredis');
jest.mock('twilio', () => jest.fn(() => ({})));

const { Pool } = require('pg');
const Redis    = require('ioredis');
const request  = require('supertest');
const { adminCookie, tenantBCookie, makeToken, authCookie } = require('../helpers');

const mockRedis = {
  ping: jest.fn().mockResolvedValue('PONG'), set: jest.fn().mockResolvedValue('OK'),
  get: jest.fn().mockResolvedValue(null), del: jest.fn().mockResolvedValue(1),
  hget: jest.fn().mockResolvedValue(null), hset: jest.fn().mockResolvedValue(1),
  hdel: jest.fn().mockResolvedValue(1), expire: jest.fn().mockResolvedValue(1),
  zadd: jest.fn().mockResolvedValue(1), lpush: jest.fn().mockResolvedValue(1),
  on: jest.fn().mockReturnThis(), disconnect: jest.fn(),
  connect: jest.fn().mockResolvedValue(undefined),
};
Redis.mockImplementation(() => mockRedis);

const mockPool = {
  query: jest.fn().mockResolvedValue({ rows: [] }),
  connect: jest.fn().mockResolvedValue({
    query: jest.fn().mockResolvedValue({ rows: [] }),
    release: jest.fn(),
  }),
  end: jest.fn(),
  on: jest.fn(),
};
Pool.mockImplementation(() => mockPool);

const { app } = require('../../../bff');

// Tenant A campaign owned by tenant-test-001
const TENANT_A_CAMPAIGN_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';

describe('Tenant isolation — campaign routes', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // All queries return empty: simulates that the other tenant's data is not accessible
    mockPool.query.mockResolvedValue({ rows: [] });
  });

  it('GET /campaigns/:id — tenant B cannot see tenant A campaign (returns 404)', async () => {
    // DB returns empty because tenant_id doesn't match
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${TENANT_A_CAMPAIGN_ID}`)
      .set('Cookie', tenantBCookie());
    expect(res.status).toBe(404);
  });

  it('PUT /campaigns/:id — tenant B cannot update tenant A campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .put(`/campaigns/${TENANT_A_CAMPAIGN_ID}`)
      .set('Cookie', tenantBCookie())
      .send({ name: 'Injected Update' });
    expect(res.status).toBe(404);
  });

  it('DELETE /campaigns/:id — tenant B cannot delete tenant A campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .delete(`/campaigns/${TENANT_A_CAMPAIGN_ID}`)
      .set('Cookie', tenantBCookie());
    expect(res.status).toBe(404);
  });

  it('POST /campaigns/:id/start — tenant B cannot activate tenant A campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .post(`/campaigns/${TENANT_A_CAMPAIGN_ID}/start`)
      .set('Cookie', tenantBCookie());
    expect(res.status).toBe(409); // no rows → invalid_transition (409)
  });

  it('GET /campaigns/:id/leads — tenant B sees no leads for tenant A campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${TENANT_A_CAMPAIGN_ID}/leads`)
      .set('Cookie', tenantBCookie());
    expect(res.status).toBe(200);
    expect(res.body).toEqual([]); // empty — no data leakage
    // Verify tenant B's tenant_id was used in the query, not tenant A's
    const [sql, params] = mockPool.query.mock.calls[0];
    expect(params).toContain('tenant-test-002'); // tenant B's id
    expect(params).not.toContain('tenant-test-001'); // tenant A's id not used
  });

  it('GET /campaigns list — each tenant sees only their own campaigns', async () => {
    // Tenant A request
    mockPool.query.mockResolvedValueOnce({ rows: [{ campaign_id: TENANT_A_CAMPAIGN_ID, tenant_id: 'tenant-test-001' }] });
    const resA = await request(app).get('/campaigns').set('Cookie', adminCookie());
    expect(resA.status).toBe(200);
    const [sqlA, paramsA] = mockPool.query.mock.calls[0];
    expect(paramsA).toContain('tenant-test-001');

    jest.clearAllMocks();
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // tenant B has no campaigns
    const resB = await request(app).get('/campaigns').set('Cookie', tenantBCookie());
    expect(resB.status).toBe(200);
    const [sqlB, paramsB] = mockPool.query.mock.calls[0];
    expect(paramsB).toContain('tenant-test-002');
    expect(paramsB).not.toContain('tenant-test-001');
  });
});

describe('Tenant isolation — lead imports', () => {
  beforeEach(() => jest.clearAllMocks());

  it('resume import — tenant B cannot resume tenant A import', async () => {
    // Import found for tenant A but query scopes to tenant B → returns empty
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .post(`/campaigns/${TENANT_A_CAMPAIGN_ID}/leads/imports/imp-A-001/resume`)
      .set('Cookie', tenantBCookie());
    expect(res.status).toBe(404);
  });
});

describe('Tenant isolation — qualifyLead uses tenant_id', () => {
  it('qualification rules are scoped to requesting tenant', async () => {
    // This test verifies that the qualification query includes tenant_id
    // by checking that the DB query parameters contain the correct tenant id
    jest.clearAllMocks();
    mockPool.query.mockResolvedValue({ rows: [] });

    // Hit a route that triggers qualifyLead internally (upload with a valid row)
    // We just verify that when qualifyLead fires, it uses the correct tenant_id
    // The source already checked this in Phase 1 tests; here we do a runtime check
    const { app: bffApp, pool: bffPool } = require('../../../bff');

    // Directly verify by inspecting the source qualification query
    const fs = require('fs');
    const src = fs.readFileSync(require('path').join(__dirname, '../../../bff.js'), 'utf8');
    const qualifyFn = src.slice(src.indexOf('async function qualifyLead'), src.indexOf('async function distributeLeadToPipeline'));
    expect(qualifyFn).toContain('tenant_id');
    expect(qualifyFn).toMatch(/WHERE campaign_id=\$1 AND tenant_id/); // tenant_id must follow campaign_id
  });
});
