'use strict';
/**
 * ActiveCallTracker unit tests
 * Tests all methods:
 *   - open  — inserts DB row + writes Redis hash entry with INITIATING status
 *   - update — updates DB row + mirrors changes in Redis
 *   - close  — removes entry from Redis hash
 *
 * NOTE: dialer_worker creates two Redis clients (redis + redisSub).
 * We use a single shared mockRedis object returned for ALL new Redis() calls
 * so that references captured inside the module point to the same mock.
 */

jest.mock('pg');
jest.mock('ioredis');

const { Pool } = require('pg');
const Redis    = require('ioredis');

// Single shared mock Redis — ALL new Redis(...) calls inside dialer_worker.js
// return this same object, regardless of which client (redis or redisSub).
const mockRedis = {
  on:       jest.fn().mockReturnThis(),
  ping:     jest.fn().mockResolvedValue('PONG'),
  disconnect: jest.fn(),
  set:      jest.fn().mockResolvedValue('OK'),
  get:      jest.fn().mockResolvedValue(null),
  del:      jest.fn().mockResolvedValue(1),
  lpush:    jest.fn().mockResolvedValue(1),
  zadd:     jest.fn().mockResolvedValue(1),
  hset:     jest.fn().mockResolvedValue(1),
  hget:     jest.fn().mockResolvedValue(null),
  hdel:     jest.fn().mockResolvedValue(1),
  expire:   jest.fn().mockResolvedValue(1),
  rpop:     jest.fn().mockResolvedValue(null),
  brpop:    jest.fn().mockResolvedValue(null),
  connect:  jest.fn().mockResolvedValue(undefined),
};
Redis.mockImplementation(() => mockRedis);

Pool.mockImplementation(() => ({
  query: jest.fn().mockResolvedValue({ rows: [] }),
  connect: jest.fn().mockResolvedValue({ query: jest.fn().mockResolvedValue({ rows: [] }), release: jest.fn() }),
  end: jest.fn(),
  on: jest.fn(),
}));

const { ActiveCallTracker } = require('../../../dialer_worker');

const SAMPLE_LEAD = {
  lead_id:     'lead-001',
  campaign_id: 'camp-001',
  pipeline_id: 'pipe-001',
  tenant_id:   'tenant-test-001',
  phone:       '9876543210',
  name:        'Test User',
  language:    'en-IN',
};

function makePool() {
  return { query: jest.fn().mockResolvedValue({ rows: [] }) };
}

describe('ActiveCallTracker.open()', () => {
  beforeEach(() => jest.clearAllMocks());

  it('inserts row into active_calls table', async () => {
    const pool    = makePool();
    const tracker = new ActiveCallTracker(pool);
    await tracker.open('CA-001', SAMPLE_LEAD);
    const [sql, params] = pool.query.mock.calls[0];
    expect(sql).toMatch(/INSERT INTO active_calls/i);
    expect(params).toContain('CA-001');
    expect(params).toContain('lead-001');
    expect(params).toContain('tenant-test-001');
  });

  it('writes entry to Redis hash with INITIATING status', async () => {
    const pool    = makePool();
    const tracker = new ActiveCallTracker(pool);
    await tracker.open('CA-001', SAMPLE_LEAD);
    expect(mockRedis.hset).toHaveBeenCalled();
    const hsetArgs    = mockRedis.hset.mock.calls[0];
    const storedValue = JSON.parse(hsetArgs[2]);
    expect(storedValue.status).toBe('INITIATING');
    expect(storedValue.call_sid).toBe('CA-001');
    expect(storedValue.lead_id).toBe('lead-001');
  });

  it('sets TTL on the Redis hash after open', async () => {
    const pool    = makePool();
    const tracker = new ActiveCallTracker(pool);
    await tracker.open('CA-001', SAMPLE_LEAD);
    expect(mockRedis.expire).toHaveBeenCalled();
  });

  it('uses ON CONFLICT DO NOTHING for idempotent inserts', async () => {
    const pool    = makePool();
    const tracker = new ActiveCallTracker(pool);
    await tracker.open('CA-001', SAMPLE_LEAD);
    const [sql] = pool.query.mock.calls[0];
    expect(sql).toMatch(/ON CONFLICT.*DO NOTHING/i);
  });
});

describe('ActiveCallTracker.update()', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockRedis.hget = jest.fn().mockResolvedValue(
      JSON.stringify({ call_sid: 'CA-002', lead_id: 'lead-001', status: 'INITIATING' })
    );
  });

  it('updates the DB row with new status and fields', async () => {
    const pool    = makePool();
    const tracker = new ActiveCallTracker(pool);
    await tracker.update('CA-002', 'tenant-test-001', {
      status:      'COMPLETED',
      answeredAt:  new Date().toISOString(),
      endedAt:     new Date().toISOString(),
      durationS:   45,
      disposition: 'COMPLETED',
    });
    const [sql, params] = pool.query.mock.calls[0];
    expect(sql).toMatch(/UPDATE active_calls/i);
    expect(params).toContain('CA-002');
    expect(params).toContain('COMPLETED');
  });

  it('updates Redis mirror when entry exists', async () => {
    const pool    = makePool();
    const tracker = new ActiveCallTracker(pool);
    await tracker.update('CA-002', 'tenant-test-001', {
      status: 'COMPLETED', disposition: 'COMPLETED',
      endedAt: new Date().toISOString(),
    });
    expect(mockRedis.hset).toHaveBeenCalled();
  });

  it('skips Redis update gracefully when entry does not exist', async () => {
    mockRedis.hget = jest.fn().mockResolvedValue(null);
    const pool    = makePool();
    const tracker = new ActiveCallTracker(pool);
    await expect(
      tracker.update('CA-999', 'tenant-test-001', { status: 'COMPLETED' })
    ).resolves.not.toThrow();
  });
});

describe('ActiveCallTracker.close()', () => {
  beforeEach(() => jest.clearAllMocks());

  it('removes entry from Redis hash', async () => {
    const pool    = makePool();
    const tracker = new ActiveCallTracker(pool);
    await tracker.close('CA-001', 'tenant-test-001');
    expect(mockRedis.hdel).toHaveBeenCalled();
    const hdel = mockRedis.hdel.mock.calls[0];
    expect(hdel[1]).toBe('CA-001');
  });
});
