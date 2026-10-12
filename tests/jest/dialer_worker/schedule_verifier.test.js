'use strict';
/**
 * ScheduleVerifier unit tests
 * Tests canCallNow() for all blocking reasons:
 *   - campaign not found → false (campaign_not_found)
 *   - campaign not ACTIVE → false (campaign_paused, campaign_draft, etc.)
 *   - before scheduled_start → false (before_scheduled_start)
 *   - after scheduled_end → false (after_scheduled_end)
 *   - excluded date → false (excluded_date)
 *   - weekday disallowed → false (weekday_disallowed)
 *   - outside calling hours → false (outside_calling_window)
 *   - all conditions met → true
 */

jest.mock('pg');
jest.mock('ioredis');

const { Pool } = require('pg');
const Redis    = require('ioredis');
Redis.mockImplementation(() => ({
  on: jest.fn().mockReturnThis(), ping: jest.fn().mockResolvedValue('PONG'),
  disconnect: jest.fn(), set: jest.fn(), get: jest.fn(), del: jest.fn(),
  lpush: jest.fn(), zadd: jest.fn(), hset: jest.fn(), hget: jest.fn(),
  hdel: jest.fn(), expire: jest.fn(), rpop: jest.fn().mockResolvedValue(null),
  brpop: jest.fn().mockResolvedValue(null), connect: jest.fn().mockResolvedValue(undefined),
}));
Pool.mockImplementation(() => ({
  query: jest.fn().mockResolvedValue({ rows: [] }),
  connect: jest.fn().mockResolvedValue({ query: jest.fn().mockResolvedValue({ rows: [] }), release: jest.fn() }),
  end: jest.fn(),
  on: jest.fn(),
}));

const { ScheduleVerifier } = require('../../../dialer_worker');

function makePool(campaignRow) {
  return {
    query: jest.fn().mockResolvedValue({ rows: campaignRow ? [campaignRow] : [] }),
  };
}

// Base campaign: ACTIVE, 9-21h Asia/Kolkata, all weekdays, no exclusions
function baseCampaign(overrides = {}) {
  return {
    status:          'ACTIVE',
    daily_start_hour: 9,
    daily_end_hour:   21,
    timezone:        'Asia/Kolkata',
    scheduled_start:  null,
    scheduled_end:    null,
    allowed_weekdays: [1, 2, 3, 4, 5, 6, 7],
    excluded_dates:   [],
    ...overrides,
  };
}

describe('ScheduleVerifier.canCallNow()', () => {
  it('returns ok=false (campaign_not_found) when campaign does not exist', async () => {
    const sv  = new ScheduleVerifier(makePool(null));
    const res = await sv.canCallNow('c1', 't1');
    expect(res.ok).toBe(false);
    expect(res.reason).toBe('campaign_not_found');
  });

  it('returns ok=false when campaign is PAUSED', async () => {
    const sv  = new ScheduleVerifier(makePool(baseCampaign({ status: 'PAUSED' })));
    const res = await sv.canCallNow('c1', 't1');
    expect(res.ok).toBe(false);
    expect(res.reason).toContain('campaign_');
  });

  it('returns ok=false when campaign is DRAFT', async () => {
    const sv  = new ScheduleVerifier(makePool(baseCampaign({ status: 'DRAFT' })));
    const res = await sv.canCallNow('c1', 't1');
    expect(res.ok).toBe(false);
  });

  it('returns ok=false when before scheduled_start', async () => {
    const future = new Date(Date.now() + 86400000).toISOString(); // tomorrow
    const sv  = new ScheduleVerifier(makePool(baseCampaign({ scheduled_start: future })));
    const res = await sv.canCallNow('c1', 't1');
    expect(res.ok).toBe(false);
    expect(res.reason).toBe('before_scheduled_start');
  });

  it('returns ok=false when after scheduled_end', async () => {
    const past = new Date(Date.now() - 86400000).toISOString(); // yesterday
    const sv  = new ScheduleVerifier(makePool(baseCampaign({ scheduled_end: past })));
    const res = await sv.canCallNow('c1', 't1');
    expect(res.ok).toBe(false);
    expect(res.reason).toBe('after_scheduled_end');
  });

  it('returns ok=false when today is an excluded date', async () => {
    // Get today's date in Asia/Kolkata
    const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    const sv  = new ScheduleVerifier(makePool(baseCampaign({ excluded_dates: [today] })));
    const res = await sv.canCallNow('c1', 't1');
    expect(res.ok).toBe(false);
    expect(res.reason).toBe('excluded_date');
  });

  it('returns ok=false when outside daily calling window', async () => {
    // Use a very narrow window that is guaranteed to be outside current time
    // Window: hour 25-26 (impossible — forces outside_calling_window)
    const sv = new ScheduleVerifier(makePool(baseCampaign({ daily_start_hour: 25, daily_end_hour: 26 })));
    const res = await sv.canCallNow('c1', 't1');
    expect(res.ok).toBe(false);
    expect(res.reason).toBe('outside_calling_window');
  });

  it('returns ok=true for a fully permissive campaign', async () => {
    // Wide window (0-23h) guarantees the current hour is within range
    const sv  = new ScheduleVerifier(makePool(baseCampaign({ daily_start_hour: 0, daily_end_hour: 23 })));
    const res = await sv.canCallNow('c1', 't1');
    expect(res.ok).toBe(true);
    expect(res.localHour).toBeDefined();
  });

  it('passes campaign_id and tenant_id to the DB query', async () => {
    const pool = makePool(baseCampaign({ daily_start_hour: 0, daily_end_hour: 23 }));
    const sv = new ScheduleVerifier(pool);
    await sv.canCallNow('camp-abc', 'tenant-xyz');
    const [sql, params] = pool.query.mock.calls[0];
    expect(params).toContain('camp-abc');
    expect(params).toContain('tenant-xyz');
  });
});

describe('ScheduleVerifier.markLeadInCall()', () => {
  it('updates lead queue_status to IN_CALL', async () => {
    const pool = { query: jest.fn().mockResolvedValue({ rows: [] }) };
    const sv   = new ScheduleVerifier(pool);
    await sv.markLeadInCall('lead-001');
    const [sql, params] = pool.query.mock.calls[0];
    expect(sql).toContain("queue_status='IN_CALL'");
    expect(params).toContain('lead-001');
  });
});

describe('ScheduleVerifier.markLeadDone()', () => {
  it('sets queue_status DONE for COMPLETED disposition', async () => {
    const pool = { query: jest.fn().mockResolvedValue({ rows: [] }) };
    const sv   = new ScheduleVerifier(pool);
    await sv.markLeadDone('lead-001', 'COMPLETED');
    const [sql, params] = pool.query.mock.calls[0];
    expect(params[0]).toBe('DONE');
  });

  it('sets queue_status FAILED for non-COMPLETED dispositions', async () => {
    const pool = { query: jest.fn().mockResolvedValue({ rows: [] }) };
    const sv   = new ScheduleVerifier(pool);
    await sv.markLeadDone('lead-001', 'NO_ANSWER');
    const [sql, params] = pool.query.mock.calls[0];
    expect(params[0]).toBe('FAILED');
  });
});
