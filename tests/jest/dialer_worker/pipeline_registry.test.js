'use strict';
/**
 * PipelineRegistry unit tests
 * Tests all DB-backed methods:
 *   - upsert — inserts or updates pipeline row
 *   - setBusy — marks pipeline BUSY with lead/callSid
 *   - setIdle — marks pipeline IDLE, clears lead/callSid
 *   - incrementStats — increments correct column per disposition
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

const { PipelineRegistry } = require('../../../dialer_worker');

function makePool() {
  return { query: jest.fn().mockResolvedValue({ rows: [] }) };
}

describe('PipelineRegistry.upsert()', () => {
  it('calls pool.query with INSERT ... ON CONFLICT', async () => {
    const pool = makePool();
    const reg  = new PipelineRegistry(pool);
    await reg.upsert({ id: 'p-001', campaignId: 'c-001', tenantId: 'tenant-test-001', name: 'Pipe 1', status: 'IDLE' });
    const [sql, params] = pool.query.mock.calls[0];
    expect(sql).toMatch(/INSERT INTO pipelines/i);
    expect(sql).toMatch(/ON CONFLICT/i);
    expect(params).toContain('p-001');
    expect(params).toContain('c-001');
    expect(params).toContain('tenant-test-001');
    expect(params).toContain('IDLE');
  });
});

describe('PipelineRegistry.setBusy()', () => {
  it("updates status to 'BUSY' with lead_id and call_sid", async () => {
    const pool = makePool();
    const reg  = new PipelineRegistry(pool);
    await reg.setBusy('p-001', 'lead-001', 'CA-abc123');
    const [sql, params] = pool.query.mock.calls[0];
    expect(sql).toMatch(/status='BUSY'/);
    expect(params).toContain('p-001');
    expect(params).toContain('lead-001');
    expect(params).toContain('CA-abc123');
  });
});

describe('PipelineRegistry.setIdle()', () => {
  it("updates status to 'IDLE' and nulls lead/callSid", async () => {
    const pool = makePool();
    const reg  = new PipelineRegistry(pool);
    await reg.setIdle('p-001');
    const [sql, params] = pool.query.mock.calls[0];
    expect(sql).toMatch(/status='IDLE'/);
    expect(sql).toMatch(/current_lead_id=NULL/);
    expect(sql).toMatch(/current_call_sid=NULL/);
    expect(params).toContain('p-001');
  });
});

describe('PipelineRegistry.incrementStats()', () => {
  it('increments calls_completed for COMPLETED disposition', async () => {
    const pool = makePool();
    const reg  = new PipelineRegistry(pool);
    await reg.incrementStats('p-001', 'COMPLETED', 45);
    const [sql, params] = pool.query.mock.calls[0];
    expect(sql).toContain('calls_completed');
    expect(params).toContain('p-001');
    expect(params).toContain(45);
  });

  it('increments calls_no_answer for NO_ANSWER disposition', async () => {
    const pool = makePool();
    const reg  = new PipelineRegistry(pool);
    await reg.incrementStats('p-001', 'NO_ANSWER', 0);
    const [sql] = pool.query.mock.calls[0];
    expect(sql).toContain('calls_no_answer');
  });

  it('increments calls_failed for other dispositions', async () => {
    const pool = makePool();
    const reg  = new PipelineRegistry(pool);
    await reg.incrementStats('p-001', 'TIMEOUT', 0);
    const [sql] = pool.query.mock.calls[0];
    expect(sql).toContain('calls_failed');
  });

  it('defaults durationS to 0 when not provided', async () => {
    const pool = makePool();
    const reg  = new PipelineRegistry(pool);
    await reg.incrementStats('p-001', 'COMPLETED');
    const [, params] = pool.query.mock.calls[0];
    expect(params[1]).toBe(0);
  });
});
