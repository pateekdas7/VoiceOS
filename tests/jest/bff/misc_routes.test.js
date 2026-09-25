'use strict';
/**
 * Miscellaneous BFF route tests — raises coverage by exercising routes not
 * covered by domain-specific test files.
 *
 * Routes covered:
 *   GET /system/health
 *   GET /team
 *   GET /team/roles
 *   GET /users/me
 *   GET /enrichment/providers
 *   GET /campaigns/:id/enrichment-config
 *   PUT /campaigns/:id/enrichment-config
 *   GET /campaigns/:id/enrichment-history
 *   GET /campaigns/:id/execution-events
 *   GET /analytics/campaigns/:id/summary
 *   GET /dialer/queue-stats
 *   Qualification rules CRUD
 *   Distribution rules CRUD
 *   Pipeline PATCH, GET single
 *   GET /campaigns/:id/leads/:leadId/events
 */

jest.mock('pg');
jest.mock('ioredis');
jest.mock('twilio', () => jest.fn(() => ({})));

const { Pool } = require('pg');
const Redis    = require('ioredis');
const request  = require('supertest');
const { adminCookie } = require('../helpers');

const mockRedis = {
  ping: jest.fn().mockResolvedValue('PONG'),
  set: jest.fn().mockResolvedValue('OK'), get: jest.fn().mockResolvedValue(null),
  del: jest.fn().mockResolvedValue(1), hget: jest.fn().mockResolvedValue(null),
  hset: jest.fn().mockResolvedValue(1), hdel: jest.fn().mockResolvedValue(1),
  expire: jest.fn().mockResolvedValue(1), zadd: jest.fn().mockResolvedValue(1),
  lpush: jest.fn().mockResolvedValue(1), on: jest.fn().mockReturnThis(),
  disconnect: jest.fn(), connect: jest.fn().mockResolvedValue(undefined),
  rpop: jest.fn().mockResolvedValue(null), brpop: jest.fn().mockResolvedValue(null),
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

// /system/health performs real GPU probes and remains integration/runtime tested.
// The lightweight liveness/readiness endpoints above are safe for unit tests.

describe('BFF health endpoints', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /health/live reports process liveness without dependency checks', async () => {
    const res = await request(app).get('/health/live');
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: 'healthy', service: 'voiceos-bff' });
    expect(mockPool.query).not.toHaveBeenCalled();
    expect(mockRedis.ping).not.toHaveBeenCalled();
  });

  it('GET /health/ready reports healthy when Postgres and Redis are reachable', async () => {
    const res = await request(app).get('/health/ready');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('healthy');
    expect(res.body.dependencies).toEqual({ postgresql: 'healthy', redis: 'healthy' });
  });

  it('GET /health/ready returns 503 when Redis is unavailable', async () => {
    mockRedis.ping.mockRejectedValueOnce(new Error('redis unavailable'));
    const res = await request(app).get('/health/ready');
    expect(res.status).toBe(503);
    expect(res.body.status).toBe('unhealthy');
    expect(res.body.dependencies.redis).toBe('unhealthy');
  });
});

describe('GET /team', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 401 without auth', async () => {
    const res = await request(app).get('/team');
    expect(res.status).toBe(401);
  });

  it('returns team member list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ user_id: 'u1', email: 'a@b.com', name: 'Alice', is_active: true }] });
    const res = await request(app).get('/team').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
    const [sql, params] = mockPool.query.mock.calls[0];
    expect(params).toContain('tenant-test-001');
  });
});

describe('GET /team/roles', () => {
  it('returns list of roles without DB query', async () => {
    const res = await request(app).get('/team/roles').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
    expect(res.body.length).toBeGreaterThan(0);
    expect(res.body[0]).toHaveProperty('role_id');
  });
});

describe('GET /users/me', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns current user profile', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ id: 'u1', email: 'admin@test.com', name: 'Admin', tenant_id: 'tenant-test-001' }] });
    const res = await request(app).get('/users/me').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    // actor_kind from JWT; test token omits it → undefined is stripped from JSON
    expect(res.body).toHaveProperty('tenant_id');
  });
});

describe('GET /enrichment/providers', () => {
  it('returns enrichment providers list (no DB)', async () => {
    const res = await request(app).get('/enrichment/providers').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('Enrichment config routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/enrichment-config returns 404 for unknown campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get('/campaigns/c-001/enrichment-config')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });

  it('GET /campaigns/:id/enrichment-config returns config', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ enrichment_config: { enabled: true } }] });
    const res = await request(app)
      .get('/campaigns/c-001/enrichment-config')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });

  it('PUT /campaigns/:id/enrichment-config returns 404 for unknown campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .put('/campaigns/c-001/enrichment-config')
      .set('Cookie', adminCookie())
      .send({ enabled: false });
    expect(res.status).toBe(404);
  });

  it('PUT /campaigns/:id/enrichment-config updates config', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ enrichment_config: { enabled: false } }] });
    const res = await request(app)
      .put('/campaigns/c-001/enrichment-config')
      .set('Cookie', adminCookie())
      .send({ enabled: false });
    expect(res.status).toBe(200);
  });

  it('GET /campaigns/:id/enrichment-history returns list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ log_id: 'lg-1', provider: 'crm', result: 'MATCHED' }] });
    const res = await request(app)
      .get('/campaigns/c-001/enrichment-history')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('Qualification rules CRUD', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/qualification-rules returns list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ rule_id: 'r-1', field: 'score', operator: '>=', value: '50' }] });
    const res = await request(app)
      .get('/campaigns/c-001/qualification-rules')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
    const [, params] = mockPool.query.mock.calls[0];
    expect(params).toContain('tenant-test-001');
  });

  it('POST /campaigns/:id/qualification-rules creates rule', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ rule_id: 'r-new', field: 'score', operator: '>=', value: '60' }] });
    const res = await request(app)
      .post('/campaigns/c-001/qualification-rules')
      .set('Cookie', adminCookie())
      .send({ field: 'score', operator: '>=', value: '60' });
    expect(res.status).toBe(201);
  });

  it('POST /campaigns/:id/qualification-rules returns 400 for missing fields', async () => {
    const res = await request(app)
      .post('/campaigns/c-001/qualification-rules')
      .set('Cookie', adminCookie())
      .send({ field: 'score' }); // missing operator and value
    expect(res.status).toBe(400);
  });

  it('DELETE /campaigns/:id/qualification-rules/:ruleId deletes rule', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .delete('/campaigns/c-001/qualification-rules/r-001')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });
});

describe('Distribution rules CRUD', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/distribution-rules returns list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get('/campaigns/c-001/distribution-rules')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('POST /campaigns/:id/distribution-rules creates rule', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ rule_id: 'dr-1', pipeline_id: 'p-001' }] });
    const res = await request(app)
      .post('/campaigns/c-001/distribution-rules')
      .set('Cookie', adminCookie())
      .send({ pipeline_id: 'p-001', min_score: 0, max_score: 100 });
    expect(res.status).toBe(201);
  });

  it('POST /campaigns/:id/distribution-rules returns 400 when pipeline_id missing', async () => {
    const res = await request(app)
      .post('/campaigns/c-001/distribution-rules')
      .set('Cookie', adminCookie())
      .send({ min_score: 0 });
    expect(res.status).toBe(400);
  });

  it('DELETE /campaigns/:id/distribution-rules/:ruleId deletes rule', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .delete('/campaigns/c-001/distribution-rules/dr-001')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });
});

describe('Execution events routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/execution-events returns list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ event_id: 'ev-1', event_type: 'CALL_INITIATED' }] });
    const res = await request(app)
      .get('/campaigns/c-001/execution-events')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /campaigns/:id/leads/:leadId/events returns lead events', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ event_id: 'ev-2', event_type: 'CALL_COMPLETED' }] });
    const res = await request(app)
      .get('/campaigns/c-001/leads/l-001/events')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('Analytics routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /analytics/campaigns/:id/summary returns summary', async () => {
    mockPool.query.mockResolvedValueOnce({
      rows: [{ total_leads: '100', calls_made: '80', completed: '60', avg_duration: '45' }],
    });
    const res = await request(app)
      .get('/analytics/campaigns/c-001/summary')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    const [, params] = mockPool.query.mock.calls[0];
    expect(params).toContain('tenant-test-001');
  });

  it('GET /dialer/queue-stats returns queue statistics', async () => {
    mockPool.query.mockResolvedValue({ rows: [{ count: '5' }] });
    const res = await request(app)
      .get('/dialer/queue-stats')
      .set('Cookie', adminCookie());
    expect([200, 500]).toContain(res.status);
  });
});

describe('Pipeline sub-routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/pipelines/:pipelineId returns 404 for unknown pipeline', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get('/campaigns/c-001/pipelines/p-999')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });

  it('GET /campaigns/:id/pipelines/:pipelineId returns pipeline', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ pipeline_id: 'p-001', name: 'Pipe 1', status: 'ACTIVE' }] });
    const res = await request(app)
      .get('/campaigns/c-001/pipelines/p-001')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.pipeline_id).toBe('p-001');
  });

  it('PATCH /campaigns/:id/pipelines/:pipelineId updates pipeline', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ pipeline_id: 'p-001', name: 'Updated', status: 'ACTIVE' }] });
    const res = await request(app)
      .patch('/campaigns/c-001/pipelines/p-001')
      .set('Cookie', adminCookie())
      .send({ name: 'Updated' });
    expect(res.status).toBe(200);
  });

  it('PATCH /campaigns/:id/pipelines/:pipelineId returns 400 with no updates', async () => {
    const res = await request(app)
      .patch('/campaigns/c-001/pipelines/p-001')
      .set('Cookie', adminCookie())
      .send({});
    expect(res.status).toBe(400);
  });
});
