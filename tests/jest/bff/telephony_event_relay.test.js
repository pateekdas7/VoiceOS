'use strict';

const {
  retryDelaySeconds,
  validateOutboxPayload,
  markFailure,
  relayBatch,
} = require('../../../scripts/telephony/telephony_event_relay');

const row = {
  event_id: 'a'.repeat(64),
  tenant_id: '11111111-1111-1111-1111-111111111111',
  event_type: 'telephony.call.lifecycle.completed',
  schema_version: '1.0',
  payload: {
    event_id: 'a'.repeat(64),
    tenant_id: '11111111-1111-1111-1111-111111111111',
    event_type: 'telephony.call.lifecycle.completed',
  },
  attempts: 0,
};

describe('W2 canonical event relay', () => {
  test('uses bounded exponential retry delay', () => {
    expect(retryDelaySeconds(1)).toBe(5);
    expect(retryDelaySeconds(2)).toBe(10);
    expect(retryDelaySeconds(20)).toBeLessThanOrEqual(300);
  });

  test('rejects malformed and cross-tenant payload identity', () => {
    expect(() => validateOutboxPayload(row)).not.toThrow();
    expect(() => validateOutboxPayload({ ...row, payload: { ...row.payload, tenant_id: 'other' } })).toThrow('outbox_identity_mismatch');
    expect(() => validateOutboxPayload({ ...row, schema_version: '9.0' })).toThrow('unsupported_event_schema');
  });

  test('moves an exhausted event to the durable DLQ', async () => {
    const queries = [];
    const client = {
      query: async (sql, params) => {
        queries.push({ sql, params });
        return { rows: [] };
      },
    };
    const exhausted = { ...row, attempts: 4 };
    const outcome = await markFailure(client, exhausted, new Error('redis unavailable'));
    expect(outcome).toBe('DLQ');
    expect(queries.some(q => q.sql.includes('INSERT INTO telephony_event_dlq'))).toBe(true);
    expect(queries.some(q => q.sql.includes("status='DLQ'"))).toBe(true);
  });

  test('requeues a retryable delivery failure with bounded backoff', async () => {
    const queries = [];
    const client = {
      query: async (sql, params) => {
        queries.push({ sql, params });
        return { rows: [] };
      },
    };
    const outcome = await markFailure(client, row, new Error('redis unavailable'));
    expect(outcome).toBe('RETRY');
    const update = queries.find(q => q.sql.includes("status='PENDING'"));
    expect(update).toBeTruthy();
    expect(update.params[2]).toBe(5);
  });

  test('isolates downstream delivery to a tenant-specific queue', async () => {
    const published = [];
    const redis = { lpush: async (key, payload) => published.push({ key, payload }) };
    const client = {
      connect: undefined,
      query: async (sql) => {
        if (sql.includes('SELECT event_id') || sql.includes('UPDATE telephony_event_outbox')) return { rows: [] };
        return { rows: [] };
      },
      release: () => {},
    };
    const pool = { connect: async () => client };
    const result = await relayBatch({ pool, redis });
    expect(result.claimed).toBe(0);
    expect(published).toHaveLength(0);
  });
});
