'use strict';
/**
 * Coverage boost 4: upload route valid-lead success path.
 * Covers: processOneRow valid branch (lines 705-724), crmMatchPhones (732-752),
 *         pushToRedisQueue body (460-473), distributeLeadToPipeline fallback (451).
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

const CAMP      = 'aaaaaaaa-0001-0001-0001-000000000001';
const PIPE      = 'dddddddd-0001-0001-0001-000000000001';
const LEAD      = 'eeeeeeee-0001-0001-0001-000000000001';
const IMPORT_ID = 'cccccccc-0001-0001-0001-000000000001';

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

const defaultClient = {
  query: jest.fn().mockResolvedValue({ rows: [] }),
  release: jest.fn(),
};

const mockPool = {
  query: jest.fn().mockResolvedValue({ rows: [] }),
  connect: jest.fn().mockResolvedValue(defaultClient),
  end: jest.fn(),
  on: jest.fn(),
};
Pool.mockImplementation(() => mockPool);

const { app } = require('../../../bff');

// ── Upload: valid lead INSERT success path ────────────────────────────────────
describe('POST /campaigns/:id/leads/upload valid lead success path', () => {
  beforeEach(() => jest.clearAllMocks());

  it('processes a valid lead and returns valid=1 with import_id', async () => {
    const campaign = {
      campaign_id: CAMP,
      tenant_id: 'bbbbbbbb-0001-0001-0001-000000000001',
      name: 'Test Campaign',
      require_crm_match_before_dial: false,
      enrichment_config: null,
    };
    const lead = {
      lead_id: LEAD,
      campaign_id: CAMP,
      pipeline_id: PIPE,
      tenant_id: 'bbbbbbbb-0001-0001-0001-000000000001',
      phone: '9999999999',
      name: 'Test Lead',
      language: 'HINDI',
      score: 50,
      queue_status: 'PENDING',
    };

    // Exact query sequence through processOneRow valid path:
    //  1  campaign SELECT
    //  2  BEGIN
    //  3  INSERT lead_imports RETURNING import_id
    //  4  enrichLead: internal_crm SELECT leads
    //  5  logEnrichment: INSERT lead_enrichment_log (crm)
    //  6  enrichLead: internal_history SELECT events
    //  7  logEnrichment: INSERT lead_enrichment_log (history)
    //  8  qualifyLead: SELECT campaign_qualification_rules  → [] → qualified=true
    //  9  distributeLeadToPipeline: SELECT pipeline_distribution_rules → [] → fallback pipelineIds[0]
    // 10  INSERT leads ON CONFLICT DO NOTHING RETURNING *   ← key: returns the lead
    // 11+ logEvent IMPORT, SCORED, DISTRIBUTED; UPDATE queue_status='QUEUED'; logEvent QUEUED;
    //     UPDATE lead_imports last_processed_row; crmMatchPhones SELECT; UPDATE leads metadata;
    //     UPDATE lead_imports status='DONE'; COMMIT — all use default { rows: [] }
    const testClient = {
      query: jest.fn()
        .mockResolvedValueOnce({ rows: [campaign] })                // 1
        .mockResolvedValueOnce({ rows: [] })                        // 2 BEGIN
        .mockResolvedValueOnce({ rows: [{ import_id: IMPORT_ID }] }) // 3
        .mockResolvedValueOnce({ rows: [] })                        // 4 crm select
        .mockResolvedValueOnce({ rows: [] })                        // 5 logEnrich crm
        .mockResolvedValueOnce({ rows: [] })                        // 6 history select
        .mockResolvedValueOnce({ rows: [] })                        // 7 logEnrich history
        .mockResolvedValueOnce({ rows: [] })                        // 8 qualifyLead
        .mockResolvedValueOnce({ rows: [] })                        // 9 distributeLead
        .mockResolvedValueOnce({ rows: [lead] })                    // 10 INSERT leads ← key
        .mockResolvedValue({ rows: [] }),                           // 11+ all remaining
      release: jest.fn(),
    };
    mockPool.connect.mockResolvedValueOnce(testClient);

    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/upload`)
      .set('Cookie', adminCookie())
      .send({
        filename: 'test.csv',
        columns: ['phone', 'name'],
        rows: [{ phone: '9999999999', name: 'Test Lead' }],
        column_mapping: { phone: 'phone', name: 'name' },
        pipeline_ids: [PIPE],
      });

    expect(res.status).toBe(200);
    expect(res.body.valid).toBe(1);
    expect(res.body.duplicates).toBe(0);
    expect(res.body.import_id).toBe(IMPORT_ID);
    expect(res.body.processed[0].ok).toBe(true);
    expect(res.body.processed[0].phone).toBe('9999999999');
  });

  it('returns no_rows 400 when rows array is empty', async () => {
    const testClient = {
      query: jest.fn().mockResolvedValue({ rows: [] }),
      release: jest.fn(),
    };
    mockPool.connect.mockResolvedValueOnce(testClient);

    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/upload`)
      .set('Cookie', adminCookie())
      .send({ rows: [], pipeline_ids: [PIPE] });

    expect(res.status).toBe(400);
    expect(res.body.error).toBe('no_rows');
  });

  it('returns 404 when campaign not found during upload', async () => {
    const testClient = {
      query: jest.fn()
        .mockResolvedValueOnce({ rows: [] })    // campaign SELECT → not found
        .mockResolvedValue({ rows: [] }),
      release: jest.fn(),
    };
    mockPool.connect.mockResolvedValueOnce(testClient);

    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/upload`)
      .set('Cookie', adminCookie())
      .send({
        rows: [{ phone: '9999999999', name: 'Test' }],
        pipeline_ids: [PIPE],
      });

    expect(res.status).toBe(404);
    expect(res.body.error).toBe('campaign_not_found');
  });

  it('processes a lead that becomes duplicate (INSERT DO NOTHING returns empty)', async () => {
    const campaign = {
      campaign_id: CAMP,
      tenant_id: 'bbbbbbbb-0001-0001-0001-000000000001',
      require_crm_match_before_dial: false,
      enrichment_config: null,
    };

    const testClient = {
      query: jest.fn()
        .mockResolvedValueOnce({ rows: [campaign] })                // 1 campaign
        .mockResolvedValueOnce({ rows: [] })                        // 2 BEGIN
        .mockResolvedValueOnce({ rows: [{ import_id: IMPORT_ID }] }) // 3 INSERT imports
        .mockResolvedValueOnce({ rows: [] })                        // 4 crm
        .mockResolvedValueOnce({ rows: [] })                        // 5 logEnrich crm
        .mockResolvedValueOnce({ rows: [] })                        // 6 history
        .mockResolvedValueOnce({ rows: [] })                        // 7 logEnrich history
        .mockResolvedValueOnce({ rows: [] })                        // 8 qualifyLead
        .mockResolvedValueOnce({ rows: [] })                        // 9 distributeLead
        .mockResolvedValueOnce({ rows: [] })                        // 10 INSERT leads → duplicate
        .mockResolvedValue({ rows: [] }),
      release: jest.fn(),
    };
    mockPool.connect.mockResolvedValueOnce(testClient);

    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/upload`)
      .set('Cookie', adminCookie())
      .send({
        rows: [{ phone: '9999999999', name: 'Test' }],
        column_mapping: { phone: 'phone', name: 'name' },
        pipeline_ids: [PIPE],
      });

    expect(res.status).toBe(200);
    expect(res.body.duplicates).toBe(1);
    expect(res.body.valid).toBe(0);
  });

  it('rejects a lead with invalid phone (no valid 10-digit number)', async () => {
    const campaign = {
      campaign_id: CAMP,
      tenant_id: 'bbbbbbbb-0001-0001-0001-000000000001',
      require_crm_match_before_dial: false,
      enrichment_config: null,
    };

    const testClient = {
      query: jest.fn()
        .mockResolvedValueOnce({ rows: [campaign] })
        .mockResolvedValueOnce({ rows: [] })                        // BEGIN
        .mockResolvedValueOnce({ rows: [{ import_id: IMPORT_ID }] }) // INSERT imports
        .mockResolvedValue({ rows: [] }),
      release: jest.fn(),
    };
    mockPool.connect.mockResolvedValueOnce(testClient);

    const res = await request(app)
      .post(`/campaigns/${CAMP}/leads/upload`)
      .set('Cookie', adminCookie())
      .send({
        rows: [{ phone: 'NOTAPHONE', name: 'Bad Lead' }],
        column_mapping: { phone: 'phone', name: 'name' },
        pipeline_ids: [PIPE],
      });

    expect(res.status).toBe(200);
    expect(res.body.invalid).toBe(1);
    expect(res.body.valid).toBe(0);
  });
});
