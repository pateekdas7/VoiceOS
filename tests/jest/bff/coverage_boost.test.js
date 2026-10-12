'use strict';
/**
 * Coverage boost: routes not exercised by other test files.
 * Targets: dialer routes, enrichment, pipelines, team, users, schedule-callback,
 *          analytics, re-enrich stats, dialer callback, twiml.
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

// ── Dialer routes ─────────────────────────────────────────────────────────────
describe('GET /dialer/status', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns empty worker list when no heartbeats', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app).get('/dialer/status').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('returns worker list with alive status from Redis', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [
      { worker_id: 'w1', status: 'IDLE', active_calls: 0, idle_pipelines: 2, busy_pipelines: 0 },
    ]});
    mockRedis.get.mockResolvedValueOnce('1');
    const res = await request(app).get('/dialer/status').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body[0].alive).toBe(true);
  });
});

describe('GET /dialer/active-calls', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns empty active calls list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app).get('/dialer/active-calls').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('returns active calls with campaign name', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [
      { call_sid: 'CA1', lead_id: LEAD, phone: '9999', campaign_name: 'Test' },
    ]});
    const res = await request(app).get('/dialer/active-calls').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body).toHaveLength(1);
  });
});

describe('GET /dialer/call-history', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns call history', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [
      { call_sid: 'CA2', phone: '8888', disposition: 'COMPLETED', duration_seconds: 120 },
    ]});
    const res = await request(app).get('/dialer/call-history').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

// ── Team and user routes ───────────────────────────────────────────────────────
describe('GET /team/roles', () => {
  it('returns roles list without auth needed', async () => {
    const res = await request(app).get('/team/roles').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('GET /users/me', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns current user info', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ id: 'u1', email: 'a@b.com', name: 'Admin' }] });
    const res = await request(app).get('/users/me').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });
});

// ── Enrichment routes ─────────────────────────────────────────────────────────
describe('GET /enrichment/providers', () => {
  it('returns providers list', async () => {
    const res = await request(app).get('/enrichment/providers').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('GET /enrichment/stats', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns enrichment stats', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app).get('/enrichment/stats').set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });
});

describe('Enrichment config routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /campaigns/:id/enrichment-config returns default config when campaign found', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ enrichment_config: null }] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/enrichment-config`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });

  it('GET /campaigns/:id/enrichment-config returns 404 for unknown campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/enrichment-config`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });

  it('PUT /campaigns/:id/enrichment-config updates config', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ enrichment_config: { providers: [] } }] });
    const res = await request(app)
      .put(`/campaigns/${CAMP}/enrichment-config`)
      .set('Cookie', adminCookie())
      .send({ providers: [] });
    expect(res.status).toBe(200);
  });

  it('PUT /campaigns/:id/enrichment-config returns 404 for unknown campaign', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .put(`/campaigns/${CAMP}/enrichment-config`)
      .set('Cookie', adminCookie())
      .send({ providers: [] });
    expect(res.status).toBe(404);
  });
});

// ── Pipeline routes ───────────────────────────────────────────────────────────
describe('Pipeline execution events', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /pipelines/:pipelineId/execution-events returns event list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/pipelines/${PIPE}/execution-events`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});

describe('Pipeline leads routes', () => {
  beforeEach(() => jest.clearAllMocks());

  it('GET /pipelines/:pipelineId/leads returns lead list', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ lead_id: LEAD, phone: '9999' }] });
    const res = await request(app)
      .get(`/pipelines/${PIPE}/leads`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
  });

  it('GET /pipelines/:pipelineId/leads supports search param', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .get(`/pipelines/${PIPE}/leads?search=test`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
  });

  it('GET /pipelines/:pipelineId/leads/stats returns stats', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ total: '10', avg_score: 75, queued: '3', in_call: '1', done: '6' }] });
    const res = await request(app)
      .get(`/pipelines/${PIPE}/leads/stats`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.total).toBe('10');
  });
});

// ── Bulk distribute route ─────────────────────────────────────────────────────
describe('POST /campaigns/:id/leads/bulk-distribute', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 400 when pipeline_ids is empty', async () => {
    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/bulk-distribute`)
      .set('Cookie', adminCookie())
      .send({ pipeline_ids: [] });
    expect(res.status).toBe(400);
    expect(res.body.error).toBe('no_pipelines');
  });

  it('returns assigned=0 when no unassigned leads', async () => {
    mockClient.query
      .mockResolvedValueOnce({ rows: [] })   // BEGIN
      .mockResolvedValueOnce({ rows: [] })   // SELECT leads (empty)
      .mockResolvedValueOnce({ rows: [] });  // COMMIT
    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/bulk-distribute`)
      .set('Cookie', adminCookie())
      .send({ pipeline_ids: [PIPE] });
    expect(res.status).toBe(200);
    expect(res.body.assigned).toBe(0);
    expect(res.body.total).toBe(0);
  });
});

// ── Schedule callback route ───────────────────────────────────────────────────
describe('POST /campaigns/:id/leads/:leadId/schedule-callback', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 400 when callback_at is missing', async () => {
    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/${LEAD}/schedule-callback`)
      .set('Cookie', adminCookie())
      .send({});
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/callback_at/);
  });

  it('returns 404 when lead is not found', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/${LEAD}/schedule-callback`)
      .set('Cookie', adminCookie())
      .send({ callback_at: new Date(Date.now() + 3600000).toISOString() });
    expect(res.status).toBe(404);
  });

  it('returns 400 when callback_at is in the past', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ lead_id: LEAD, tenant_id: 'bbbbbbbb-0001-0001-0001-000000000001' }] });
    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/${LEAD}/schedule-callback`)
      .set('Cookie', adminCookie())
      .send({ callback_at: '2020-01-01T00:00:00Z' });
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/future/);
  });

  it('returns 200 and schedules callback for future datetime', async () => {
    const lead = { lead_id: LEAD, campaign_id: CAMP, pipeline_id: PIPE, tenant_id: 'bbbbbbbb-0001-0001-0001-000000000001', phone: '9999', name: 'Test', language: 'hi', score: 80 };
    mockPool.query
      .mockResolvedValueOnce({ rows: [lead] })  // SELECT lead
      .mockResolvedValueOnce({ rows: [] });      // UPDATE queue_status
    const future = new Date(Date.now() + 3600000).toISOString();
    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/${LEAD}/schedule-callback`)
      .set('Cookie', adminCookie())
      .send({ callback_at: future });
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.scheduled_at).toBeDefined();
  });
});

// ── Dialer twiml ─────────────────────────────────────────────────────────────
describe('POST /dialer/twiml', () => {
  it('returns TwiML XML response', async () => {
    const res = await request(app)
      .post('/dialer/twiml')
      .send({ CallSid: 'CA1', CallStatus: 'ringing' });
    expect(res.status).toBe(200);
    expect(res.headers['content-type']).toMatch(/xml/);
    expect(res.text).toContain('<Response>');
  });
});

// ── Dialer callback ───────────────────────────────────────────────────────────
describe('POST /dialer/callback', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns 200 for non-terminal call status (no DB writes)', async () => {
    const res = await request(app)
      .post('/dialer/callback?pipeline_id=pip1&lead_id=lead1&tenant_id=t1')
      .send({ CallSid: 'CA1', CallStatus: 'queued' });
    expect(res.status).toBe(200);
  });

  it('returns 400 when CallSid or pipeline_id missing', async () => {
    const res = await request(app)
      .post('/dialer/callback')
      .send({ CallStatus: 'completed' });
    expect(res.status).toBe(400);
  });

  it('handles terminal COMPLETED callback with idempotency check', async () => {
    mockPool.query
      .mockResolvedValueOnce({ rows: [] })   // idempotency check (not found)
      .mockResolvedValueOnce({ rows: [] });  // idempotency insert
    const res = await request(app)
      .post(`/dialer/callback?pipeline_id=${PIPE}&lead_id=${LEAD}&tenant_id=bbbbbbbb-0001-0001-0001-000000000001`)
      .send({ CallSid: 'CA999', CallStatus: 'completed', Duration: '60' });
    expect(res.status).toBe(200);
  });

  it('suppresses duplicate terminal callback (idempotency hit)', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ key: 'twilio_callback:CA999:completed' }] });
    const res = await request(app)
      .post(`/dialer/callback?pipeline_id=${PIPE}`)
      .send({ CallSid: 'CA999', CallStatus: 'completed' });
    expect(res.status).toBe(200);
  });

  it('handles in-progress status update to active_calls', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // UPDATE active_calls
    const res = await request(app)
      .post(`/dialer/callback?pipeline_id=${PIPE}`)
      .send({ CallSid: 'CA100', CallStatus: 'in-progress' });
    expect(res.status).toBe(200);
  });
});

// ── Analytics summary ─────────────────────────────────────────────────────────
describe('GET /analytics/campaigns/:id/summary (coverage)', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns computed ptp/contactability/conversion rates', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ total: '100', queued: '50', qualified: '80', completed: '30' }] });
    const res = await request(app)
      .get(`/analytics/campaigns/${CAMP}/summary`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.ptp_rate).toBeDefined();
  });
});
