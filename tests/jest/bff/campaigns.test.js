'use strict';
/**
 * Campaign route tests
 * - GET /campaigns — list with tenant scope
 * - POST /campaigns — create
 * - GET /campaigns/:id — fetch one
 * - PUT /campaigns/:id — update with tenant isolation
 * - DELETE /campaigns/:id — delete
 * - POST /campaigns/:id/:action — lifecycle transitions (start, pause, archive)
 * - GET /campaigns/:id/pipelines — list pipelines
 * - POST /campaigns/:id/pipelines — create pipeline
 */

jest.mock('pg');
jest.mock('ioredis');
jest.mock('twilio', () => jest.fn(() => ({})));

const { Pool } = require('pg');
const Redis    = require('ioredis');
const request  = require('supertest');
const { adminCookie, tenantBCookie } = require('../helpers');

const mockRedis = {
  ping: jest.fn().mockResolvedValue('PONG'),
  set: jest.fn().mockResolvedValue('OK'), get: jest.fn().mockResolvedValue(null),
  del: jest.fn().mockResolvedValue(1), hget: jest.fn().mockResolvedValue(null),
  hset: jest.fn().mockResolvedValue(1), hdel: jest.fn().mockResolvedValue(1),
  expire: jest.fn().mockResolvedValue(1), zadd: jest.fn().mockResolvedValue(1),
  lpush: jest.fn().mockResolvedValue(1), on: jest.fn().mockReturnThis(), disconnect: jest.fn(),
  connect: jest.fn().mockResolvedValue(undefined),
};
Redis.mockImplementation(() => mockRedis);

const mockPool = {
  query: jest.fn().mockResolvedValue({ rows: [] }),
  connect: jest.fn().mockResolvedValue({ query: jest.fn().mockResolvedValue({ rows: [] }), release: jest.fn() }),
  end: jest.fn(),
  on: jest.fn(),
};
Pool.mockImplementation(() => mockPool);

const { app } = require('../../../bff');

const CAMPAIGN = {
  campaign_id: 'c-001', tenant_id: 'tenant-test-001', name: 'Test Campaign',
  status: 'DRAFT', created_at: new Date().toISOString(),
};

describe('GET /campaigns', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 401 without auth', async () => {
    const res = await request(app).get('/campaigns');
    expect(res.status).toBe(401);
  });

  it('returns campaign list for authenticated user', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [CAMPAIGN] });
    const res = await request(app).get('/campaigns').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('scopes query to tenant_id — does not expose other tenant campaigns', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app).get('/campaigns').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    // Verify tenant_id is passed to the query
    const [sql, params] = mockPool.query.mock.calls[0];
    expect(params).toContain('tenant-test-001');
  });
});

describe('POST /campaigns', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 401 without auth', async () => {
    const res = await request(app).post('/campaigns').send({ name: 'New' });
    expect(res.status).toBe(401);
  });

  it('creates a campaign and returns it', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [CAMPAIGN] });
    const res = await request(app)
      .post('/campaigns')
      .set('Cookie', adminCookie())
      .send({ name: 'New Campaign', description: 'test', timezone: 'Asia/Kolkata' });
    expect(res.status).toBe(201);
    expect(res.body.campaign_id).toBeDefined();
  });

  it('returns 400 when name is missing', async () => {
    const res = await request(app)
      .post('/campaigns')
      .set('Cookie', adminCookie())
      .send({ description: 'no name' });
    expect(res.status).toBe(400);
  });
});

describe('GET /campaigns/:id', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 404 for unknown campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get('/campaigns/no-such-id')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });

  it('returns the campaign when found', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [CAMPAIGN] });
    const res = await request(app)
      .get('/campaigns/c-001')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.campaign_id).toBe('c-001');
  });
});

describe('PUT /campaigns/:id — tenant isolation', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 404 when tenant_id does not match (cross-tenant update blocked)', async () => {
    // Query returns 0 rows because tenant_id doesn't match
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .put('/campaigns/c-001')
      .set('Cookie', tenantBCookie())  // tenant B trying to update tenant A's campaign
      .send({ name: 'Hacked' });
    expect(res.status).toBe(404);
  });

  it('updates campaign when tenant matches', async () => {
    const updated = { ...CAMPAIGN, name: 'Updated' };
    mockPool.query.mockResolvedValueOnce({ rows: [updated] });
    const res = await request(app)
      .put('/campaigns/c-001')
      .set('Cookie', adminCookie())
      .send({ name: 'Updated' });
    expect(res.status).toBe(200);
    expect(res.body.name).toBe('Updated');
  });
});

describe('DELETE /campaigns/:id', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 404 for non-existent or cross-tenant campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .delete('/campaigns/no-such')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });

  it('deletes campaign and returns it', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [CAMPAIGN] });
    const res = await request(app)
      .delete('/campaigns/c-001')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });
});

describe('POST /campaigns/:id/:action — lifecycle', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 400 for unknown action', async () => {
    const res = await request(app)
      .post('/campaigns/c-001/nonexistent-action')
      .set('Cookie', adminCookie());
    // May be caught by the route or return 400
    expect([400, 404]).toContain(res.status);
  });

  it('transitions campaign to ACTIVE on start', async () => {
    const active = { ...CAMPAIGN, status: 'ACTIVE' };
    mockPool.query.mockResolvedValueOnce({ rows: [active] });
    const res = await request(app)
      .post('/campaigns/c-001/start')
      .set('Cookie', adminCookie())
      .send({});
    expect(res.status).toBe(200);
  });

  it('returns 409 on invalid transition (e.g., start already ACTIVE)', async () => {
    // Query returns 0 rows → transition guard rejected it
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .post('/campaigns/c-001/start')
      .set('Cookie', adminCookie())
      .send({});
    expect(res.status).toBe(409);
  });
});

describe('Campaign pipeline routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/pipelines returns 404 for unknown campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // campaign not found
    const res = await request(app)
      .get('/campaigns/c-001/pipelines')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });

  it('GET /campaigns/:id/pipelines returns pipeline list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [CAMPAIGN] }); // campaign found
    mockPool.query.mockResolvedValueOnce({ rows: [{ pipeline_id: 'p-001', name: 'Pipe 1' }] }); // pipelines
    const res = await request(app)
      .get('/campaigns/c-001/pipelines')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('POST /campaigns/:id/pipelines returns 404 for unknown campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // campaign not found
    const res = await request(app)
      .post('/campaigns/c-001/pipelines')
      .set('Cookie', adminCookie())
      .send({ name: 'Pipe A' });
    expect(res.status).toBe(404);
  });
});
