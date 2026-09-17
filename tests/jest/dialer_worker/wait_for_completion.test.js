'use strict';
/**
 * Pipeline._waitForCompletion() unit tests
 *
 * The method polls Redis RPOP on the pipeline completion key until:
 *   - A payload matching the expected callSid is found → returns it
 *   - A mismatched callSid is found → puts it back (LPUSH) and keeps polling
 *   - Deadline elapses → returns TIMEOUT disposition
 *
 * NOTE: dialer_worker creates two Redis clients (redis + redisSub).
 * We use a single shared mockRedis so all internal redis.rpop calls
 * are intercepted by the same mock.
 */

jest.mock('pg');
jest.mock('ioredis');

const { Pool } = require('pg');
const Redis    = require('ioredis');

const mockRedis = {
  on:       jest.fn().mockReturnThis(),
  ping:     jest.fn().mockResolvedValue('PONG'),
  disconnect: jest.fn(),
  set:      jest.fn().mockResolvedValue('OK'),
  get:      jest.fn().mockResolvedValue(null),
  del:      jest.fn().mockResolvedValue(1),
  rpop:     jest.fn().mockResolvedValue(null),
  lpush:    jest.fn().mockResolvedValue(1),
  zadd:     jest.fn().mockResolvedValue(1),
  hset:     jest.fn().mockResolvedValue(1),
  hget:     jest.fn().mockResolvedValue(null),
  hdel:     jest.fn().mockResolvedValue(1),
  expire:   jest.fn().mockResolvedValue(1),
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

const { Pipeline, PipelineRegistry, ActiveCallTracker, ScheduleVerifier } = require('../../../dialer_worker');

function makePipeline(id = 'pipe-001') {
  const pool = { query: jest.fn().mockResolvedValue({ rows: [] }) };
  return new Pipeline({
    id,
    campaignId:  'camp-001',
    tenantId:    'tenant-test-001',
    name:        'Test Pipeline',
    registry:    new PipelineRegistry(pool),
    tracker:     new ActiveCallTracker(pool),
    scheduler:   new ScheduleVerifier(pool),
    eventLogger: { log: jest.fn().mockResolvedValue(undefined) },
    dialer:      { initiate: jest.fn().mockResolvedValue('CA-dummy') },
  });
}

const LEAD = { campaign_id: 'camp-001', tenant_id: 'tenant-test-001', lead_id: 'l-001' };

describe('Pipeline._waitForCompletion()', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns payload immediately when callSid matches', async () => {
    const pipeline = makePipeline();
    const payload  = {
      callSid:     'CA-match',
      disposition: 'COMPLETED',
      endedAt:     new Date().toISOString(),
      durationS:   30,
    };
    mockRedis.rpop = jest.fn().mockResolvedValueOnce(JSON.stringify(payload));

    const result = await pipeline._waitForCompletion('CA-match', LEAD);
    expect(result.callSid).toBe('CA-match');
    expect(result.disposition).toBe('COMPLETED');
  });

  it('re-pushes mismatched callSid payload and resolves on match', async () => {
    const pipeline = makePipeline();
    const stale    = { callSid: 'CA-stale', disposition: 'COMPLETED' };
    const match    = {
      callSid: 'CA-target', disposition: 'NO_ANSWER',
      endedAt: new Date().toISOString(), durationS: 0,
    };

    // First rpop: stale event; second rpop: matching event
    mockRedis.rpop = jest.fn()
      .mockResolvedValueOnce(JSON.stringify(stale))
      .mockResolvedValueOnce(JSON.stringify(match));

    const lead   = { ...LEAD, lead_id: 'l-002' };
    const result = await pipeline._waitForCompletion('CA-target', lead);

    expect(result.callSid).toBe('CA-target');
    expect(mockRedis.lpush).toHaveBeenCalledWith(
      expect.any(String),
      JSON.stringify(stale)
    );
  });

  it('returns TIMEOUT disposition when no matching event arrives before deadline', async () => {
    const pipeline = makePipeline();

    // rpop always returns null — simulates empty queue
    mockRedis.rpop = jest.fn().mockResolvedValue(null);

    // Force the deadline to be in the past after a few iterations
    const realNow = Date.now.bind(Date);
    let callCount = 0;
    const spy = jest.spyOn(Date, 'now').mockImplementation(() => {
      callCount++;
      // First call (deadline calculation) uses real time;
      // subsequent calls return time past the deadline.
      if (callCount === 1) return realNow();
      return realNow() + 1e9;
    });

    const lead   = { ...LEAD, lead_id: 'l-003' };
    const result = await pipeline._waitForCompletion('CA-never', lead);

    spy.mockRestore();
    expect(result.disposition).toBe('TIMEOUT');
    expect(result.callSid).toBe('CA-never');
  });
});
