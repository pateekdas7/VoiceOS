'use strict';
/**
 * Coverage boost 3: campaign CRUD success paths, rule CRUD, pipeline CRUD,
 *                   lifecycle transitions, suggest-mapping, system health.
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

const CAMP  = 'aaaaaaaa-0001-0001-0001-000000000001';
const PIPE  = 'dddddddd-0001-0001-0001-000000000001';
const RULE  = 'ffffffff-0001-0001-0001-000000000001';
const RULE2 = 'ffffffff-0002-0002-0002-000000000002';

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

// ── POST /campaigns create ────────────────────────────────────────────────────
describe('POST /campaigns create success', () => {
  beforeEach(() => jest.clearAllMocks());

  it('creates and returns new campaign (201)', async () => {
    const camp = { campaign_id: CAMP, name: 'New Camp', status: 'DRAFT' };
    mockPool.query.mockResolvedValueOnce({ rows: [camp] }); // INSERT RETURNING *
    const res = await request(app)
      .post('/campaigns')
      .set('Cookie', adminCookie())
      .send({ name: 'New Camp' });
    expect([200, 201]).toContain(res.status);
    expect(res.body.campaign_id).toBe(CAMP);
  });
});

// ── PUT /campaigns/:id update ─────────────────────────────────────────────────
describe('PUT /campaigns/:id', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns updated campaign on success', async () => {
    const camp = { campaign_id: CAMP, name: 'Updated', status: 'DRAFT' };
    mockPool.query.mockResolvedValueOnce({ rows: [camp] }); // UPDATE RETURNING *
    const res = await request(app)
      .put(`/campaigns/${CAMP}`)
      .set('Cookie', adminCookie())
      .send({ name: 'Updated' });
    expect(res.status).toBe(200);
    expect(res.body.name).toBe('Updated');
  });

  it('returns 404 when campaign not found', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] });
    const res = await request(app)
      .put(`/campaigns/${CAMP}`)
      .set('Cookie', adminCookie())
      .send({ name: 'Updated' });
    expect(res.status).toBe(404);
  });
});

// ── DELETE qualification rule ─────────────────────────────────────────────────
describe('DELETE /campaigns/:id/qualification-rules/:ruleId', () => {
  beforeEach(() => jest.clearAllMocks());

  it('deletes rule and returns { ok: true }', async () => {
    const res = await request(app)
      .delete(`/campaigns/${CAMP}/qualification-rules/${RULE}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });
});

// ── Distribution rules ────────────────────────────────────────────────────────
describe('POST /campaigns/:id/distribution-rules', () => {
  beforeEach(() => jest.clearAllMocks());

  it('creates distribution rule', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [{ rule_id: RULE, pipeline_id: PIPE }] });
    const res = await request(app)
      .post(`/campaigns/${CAMP}/distribution-rules`)
      .set('Cookie', adminCookie())
      .send({ pipeline_id: PIPE, min_score: 60, max_score: 100, priority: 1 });
    expect([200, 201]).toContain(res.status);
  });

  it('returns 400 when pipeline_id missing', async () => {
    const res = await request(app)
      .post(`/campaigns/${CAMP}/distribution-rules`)
      .set('Cookie', adminCookie())
      .send({ min_score: 60, max_score: 100 });
    expect(res.status).toBe(400);
    expect(res.body.error).toBe('pipeline_id_required');
  });
});

describe('DELETE /campaigns/:id/distribution-rules/:ruleId', () => {
  beforeEach(() => jest.clearAllMocks());

  it('deletes distribution rule and returns { ok: true }', async () => {
    const res = await request(app)
      .delete(`/campaigns/${CAMP}/distribution-rules/${RULE2}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });
});

// ── Pipeline CRUD ─────────────────────────────────────────────────────────────
describe('POST /campaigns/:id/pipelines create', () => {
  beforeEach(() => jest.clearAllMocks());

  it('creates pipeline when campaign exists', async () => {
    const pipe = { pipeline_id: PIPE, campaign_id: CAMP, name: 'P1', status: 'ACTIVE' };
    mockPool.query
      .mockResolvedValueOnce({ rows: [{ campaign_id: CAMP }] }) // campaign check
      .mockResolvedValueOnce({ rows: [pipe] });                  // INSERT
    const res = await request(app)
      .post(`/campaigns/${CAMP}/pipelines`)
      .set('Cookie', adminCookie())
      .send({ name: 'P1' });
    expect([200, 201]).toContain(res.status);
    expect(res.body.pipeline_id).toBe(PIPE);
  });

  it('returns 404 when campaign not found', async () => {
    mockPool.query.mockResolvedValueOnce({ rows: [] }); // campaign check fails
    const res = await request(app)
      .post(`/campaigns/${CAMP}/pipelines`)
      .set('Cookie', adminCookie())
      .send({ name: 'P1' });
    expect(res.status).toBe(404);
  });
});

describe('GET /campaigns/:id/pipelines/:pipelineId', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns single pipeline', async () => {
    const pipe = { pipeline_id: PIPE, campaign_id: CAMP, name: 'P1', status: 'ACTIVE' };
    mockPool.query.mockResolvedValueOnce({ rows: [pipe] });
    const res = await request(app)
      .get(`/campaigns/${CAMP}/pipelines/${PIPE}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(200);
    expect(res.body.pipeline_id).toBe(PIPE);
  });

  it('returns 404 when pipeline not found', async () => {
    const res = await request(app)
      .get(`/campaigns/${CAMP}/pipelines/${PIPE}`)
      .set('Cookie', adminCookie());
    expect(res.status).toBe(404);
  });
});

describe('PATCH /campaigns/:id/pipelines/:pipelineId success', () => {
  beforeEach(() => jest.clearAllMocks());

  it('updates pipeline name and returns updated record', async () => {
    const pipe = { pipeline_id: PIPE, campaign_id: CAMP, name: 'Renamed', status: 'ACTIVE' };
    mockPool.query.mockResolvedValueOnce({ rows: [pipe] }); // UPDATE RETURNING
    const res = await request(app)
      .patch(`/campaigns/${CAMP}/pipelines/${PIPE}`)
      .set('Cookie', adminCookie())
      .send({ name: 'Renamed' });
    expect(res.status).toBe(200);
    expect(res.body.name).toBe('Renamed');
  });

  it('returns 404 when pipeline not found on PATCH', async () => {
    const res = await request(app)
      .patch(`/campaigns/${CAMP}/pipelines/${PIPE}`)
      .set('Cookie', adminCookie())
      .send({ name: 'Renamed' });
    expect(res.status).toBe(404);
  });
});

// ── Campaign lifecycle transitions ────────────────────────────────────────────
describe('Campaign lifecycle transitions', () => {
  beforeEach(() => jest.clearAllMocks());

  it('POST /campaigns/:id/start transitions campaign to ACTIVE', async () => {
    const camp = { campaign_id: CAMP, status: 'ACTIVE' };
    mockPool.query.mockResolvedValueOnce({ rows: [camp] }); // UPDATE RETURNING
    const res = await request(app)
      .post(`/campaigns/${CAMP}/start`)
      .set('Cookie', adminCookie())
      .send({});
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ACTIVE');
  });

  it('POST /campaigns/:id/pause returns 409 for invalid transition', async () => {
    // default mock returns { rows: [] } — no campaign in valid state
    const res = await request(app)
      .post(`/campaigns/${CAMP}/pause`)
      .set('Cookie', adminCookie())
      .send({});
    expect(res.status).toBe(409);
    expect(res.body.error).toBe('invalid_transition');
  });

  it('POST /campaigns/:id/unknown-action returns 400', async () => {
    const res = await request(app)
      .post(`/campaigns/${CAMP}/unknown-action`)
      .set('Cookie', adminCookie())
      .send({});
    expect(res.status).toBe(400);
    expect(res.body.error).toBe('unknown_action');
  });
});

// ── Lead suggest-mapping ──────────────────────────────────────────────────────
describe('POST /campaigns/:id/leads/suggest-mapping', () => {
  it('returns suggested column mapping', async () => {
    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/suggest-mapping`)
      .set('Cookie', adminCookie())
      .send({ columns: ['First Name', 'Phone Number', 'Email'] });
    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('suggested_mapping');
  });
});

// ── GET /system/health ────────────────────────────────────────────────────────
describe('GET /system/health', () => {
  let origFetch;
  beforeAll(() => {
    origFetch = global.fetch;
    // Return ok:false so clearTimeout IS called (no dangling timers)
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 503 });
  });
  afterAll(() => {
    global.fetch = origFetch;
  });

  it('returns component health array with Redis healthy', async () => {
    const res = await request(app).get('/system/health');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body)).toBe(true);
    const redisComp = res.body.find(c => c.component === 'Redis');
    expect(redisComp).toBeDefined();
    expect(redisComp.status).toBe('healthy');
    const sttComp = res.body.find(c => c.component === 'STT');
    expect(sttComp.status).toBe('degraded');
  });
});
