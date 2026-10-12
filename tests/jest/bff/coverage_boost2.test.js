'use strict';
/**
 * Coverage boost 2: error paths, success paths, idempotency, metrics, catch-all.
 * Targets: assign-pipeline success, lead routes, idempotency hit, /metrics,
 *          catch-all 404, error handler, DB error paths in route catch blocks.
 */

jest.mock('pg');
jest.mock('ioredis');
jest.mock('twilio', () => {
  const mockFn = jest.fn(() => ({}));
  mockFn.validateRequest = jest.fn().mockReturnValue(true);
  return mockFn;
});

const { Pool } = require('pg');
const Redis    = require('ioredis');
const request  = require('supertest');
const { adminCookie } = require('../helpers');

const CAMP = 'aaaaaaaa-0001-0001-0001-000000000001';
const PIPE = 'dddddddd-0001-0001-0001-000000000001';
const LEAD = 'eeeeeeee-0001-0001-0001-000000000001';
const RULE = 'ffffffff-0001-0001-0001-000000000001';

const mockRedis = {
  ping: jest.fn().mockResolvedValue('PONG'),
  set: jest.fn().mockResolvedValue('OK'),
  get: jest.fn().mockResolvedValue(null),
  del: jest.fn().mockResolvedValue(1),
  hget: jest.fn().mockResolvedValue(null),
  hset: jest.fn().mockResolvedValue(1),
  hlen: jest.fn().mockResolvedValue(0),
  hdel: jest.fn().mockResolvedValue(1),
  expire: jest.fn().mockResolvedValue(1),
  ttl: jest.fn().mockResolvedValue(-1),
  exists: jest.fn().mockResolvedValue(0),
  incr: jest.fn().mockResolvedValue(1),
  zadd: jest.fn().mockResolvedValue(1),
  zcard: jest.fn().mockResolvedValue(0),
  lpush: jest.fn().mockResolvedValue(1),
  llen: jest.fn().mockResolvedValue(0),
  on: jest.fn().mockReturnThis(),
  disconnect: jest.fn(),
  connect: jest.fn().mockResolvedValue(undefined),
};
Redis.mockImplementation(() => mockRedis);

const mockClient = {
  query: jest.fn().mockResolvedValue({ rows: [] }),
  release: jest.fn(),
};

const mockPool = {
  query: jest.fn().mockResolvedValue({ rows: [] }),
  connect: jest.fn().mockResolvedValue(mockClient),
  end: jest.fn(),
  on: jest.fn(),
};
Pool.mockImplementation(() => mockPool);

const { app } = require('../../../bff');

// ── Prometheus metrics endpoint ────────────────────────────────────────────────
describe('GET /metrics', () => {
  it('returns prometheus metrics text', async () => {
    const res = await request(app).get('/metrics');
    expect(res.status).toBe(200);
    expect(res.headers['content-type']).toMatch(/text/);
  });
});

// ── Catch-all 404 ─────────────────────────────────────────────────────────────
describe('Catch-all 404 route', () => {
  it('returns 404 for unknown path', async () => {
    const res = await request(app).get('/this-route-does-not-exist-abc123');
    expect(res.status).toBe(404);
    expect(res.body.error).toBe('not_found');
  });
});

// ── Error handler: DB error triggers 500 ──────────────────────────────────────
describe('Error handler (500)', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 500 when DB throws in GET /campaigns/:id', async () => {
    mockPool.query.mockRejectedValueOnce(new Error('DB connection lost'));
    const res = await request(app)
      .get(`/campaigns/${CAMP}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(500);
  });

  it('returns 500 when DB throws in DELETE /campaigns/:id', async () => {
    mockPool.query.mockRejectedValueOnce(new Error('DB error'));
    const res = await request(app)
      .delete(`/campaigns/${CAMP}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(500);
  });

  it('returns 500 when DB throws in GET /campaigns', async () => {
    mockPool.query.mockRejectedValueOnce(new Error('DB error'));
    const res = await request(app)
      .get('/campaigns')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(500);
  });

  it('returns 500 when DB throws in GET /dialer/active-calls', async () => {
    mockPool.query.mockRejectedValueOnce(new Error('DB error'));
    const res = await request(app)
      .get('/dialer/active-calls')
      .set('Cookie', adminCookie());
    expect(res.status).toBe(500);
  });
});

// ── Idempotency cache hit ─────────────────────────────────────────────────────
describe('Idempotency middleware cache hit', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns cached response when Idempotency-Key already exists', async () => {
    const cached = JSON.stringify({ campaign_id: CAMP, name: 'Cached Campaign', status: 'DRAFT' });
    mockRedis.get.mockResolvedValueOnce(cached);
    const res = await request(app)
      .post('/campaigns')
      .set('Cookie', adminCookie())
      .set('Idempotency-Key', 'test-idem-001')
      .send({ name: 'New Campaign' });
    expect(res.status).toBe(200);
    expect(res.body.campaign_id).toBe(CAMP);
  });
});

// ── Lead stats route ──────────────────────────────────────────────────────────
describe('GET /campaigns/:id/leads/stats', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns lead statistics for campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{
      total: '200', valid: '180', rejected: '20', duplicates: '5',
      assigned: '150', queued: '50', in_call: '10', done: '90', avg_score: '72',
    }]});
    const res = await request(app)
      .get(`/campaigns/${CAMP}/leads/stats`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.total).toBe('200');
  });
});

// ── Lead list with filters ────────────────────────────────────────────────────
describe('GET /campaigns/:id/leads with filters', () => {
  beforeEach(() => jest.clearAllMocks());

  it('filters by status', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/leads?status=ASSIGNED`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });

  it('filters by pipeline_id=unassigned', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/leads?pipeline_id=unassigned`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });

  it('filters by search term', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/leads?search=John`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });

  it('filters by specific pipeline_id', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/leads?pipeline_id=${PIPE}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });
});

// ── Assign-pipeline success path ──────────────────────────────────────────────
describe('POST /campaigns/:id/leads/:leadId/assign-pipeline success', () => {
  beforeEach(() => jest.clearAllMocks());

  it('assigns lead to pipeline and queues it', async () => {
    const lead = {
      lead_id: LEAD, campaign_id: CAMP, pipeline_id: PIPE,
      tenant_id: 'bbbbbbbb-0001-0001-0001-000000000001',
      phone: '9999999999', name: 'Test Lead', language: 'hi', score: 80,
      queue_status: 'PENDING',
    };
    // Sequence: BEGIN, UPDATE leads RETURNING *, logEvent INSERT, UPDATE queue_status, logEvent INSERT, COMMIT
    mockClient.query
      .mockResolvedValueOnce({ rows: [] })      // BEGIN
      .mockResolvedValueOnce({ rows: [lead] }); // UPDATE RETURNING * (lead found)
    // rest use default { rows: [] }
    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/${LEAD}/assign-pipeline`)
      .set('Cookie', adminCookie())
      .send({ pipeline_id: PIPE });
    expect(res.status).toBe(200);
    expect(res.body.lead_id).toBe(LEAD);
  });
});

// ── Import list and single import routes ─────────────────────────────────────
describe('Import routes coverage', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/leads/imports returns list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ import_id: 'cccccccc-0001-0001-0001-000000000001', filename: 'leads.csv' }] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/leads/imports`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /campaigns/:id/leads/imports/:importId returns single import', async () => {
    const importId = 'cccccccc-0001-0001-0001-000000000001';
    mockPool.query.mockResolvedValueOnce({ rows: [{ import_id: importId, filename: 'leads.csv', status: 'DONE' }] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/leads/imports/${importId}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.import_id).toBe(importId);
  });

  it('GET /campaigns/:id/leads/imports/:importId returns 404 for missing import', async () => {
    const importId = 'cccccccc-9999-9999-9999-000000000999';
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/leads/imports/${importId}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });
});

// ── Qualification and distribution rules ─────────────────────────────────────
describe('Qualification and distribution rule routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/qualification-rules returns list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ rule_id: RULE }] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/qualification-rules`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });

  it('POST /campaigns/:id/qualification-rules creates rule', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ rule_id: RULE, field: 'score', operator: 'gte', value: '70' }] });
    const res = await request(app)
      .post(`/campaigns/${CAMP}/qualification-rules`)
      .set('Cookie', adminCookie())
      .send({ field: 'score', operator: 'gte', value: '70', priority: 1 });
    expect([200, 201]).toContain(res.status);
  });

  it('GET /campaigns/:id/distribution-rules returns list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/distribution-rules`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });
});

// ── Campaign lifecycle and pipeline routes ────────────────────────────────────
describe('Campaign lifecycle and pipeline routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/pipelines returns list', async () => {
    mockPool.query
      .mockResolvedValueOnce({ rows: [{ campaign_id: CAMP }] }) // campaign check
      .mockResolvedValueOnce({ rows: [] });                     // pipelines list
    const res = await request(app)
      .get(`/campaigns/${CAMP}/pipelines`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });

  it('GET /campaigns/:id/pipelines returns 404 for unknown campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // campaign not found
    const res = await request(app)
      .get(`/campaigns/${CAMP}/pipelines`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });

  it('PATCH /campaigns/:id/pipelines/:pipelineId returns 400 with no updates', async () => {
    const res = await request(app)
      .patch(`/campaigns/${CAMP}/pipelines/${PIPE}`)
      .set('Cookie', adminCookie())
      .send({});
    expect(res.status).toBe(400);
  });
});

// ── Lead events with execution-events route ───────────────────────────────────
describe('Execution events with lead_id filter', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/execution-events with lead_id filter', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/execution-events?lead_id=${LEAD}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });
});
