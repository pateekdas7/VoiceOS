'use strict';

const {
  validateCanonicalCallEvent,
  serializeCanonicalCallEvent,
  persistCanonicalCallEvent,
} = require('../../../telephony_event_boundary');

const baseEvent = {
  eventId: 'a'.repeat(64),
  eventType: 'telephony.call.lifecycle.completed',
  schemaVersion: '1.0',
  tenantId: '11111111-1111-1111-1111-111111111111',
  campaignId: '22222222-2222-2222-2222-222222222222',
  leadId: '33333333-3333-3333-3333-333333333333',
  callId: 'CA123',
  callAttemptId: '44444444-4444-4444-4444-444444444444',
  provider: 'twilio',
  providerCallSid: 'CA123',
  lifecycleState: 'COMPLETED',
  outcome: 'COMPLETED',
  durationSeconds: 12,
  recordingReference: null,
  callbackReference: null,
  eventTimestamp: '2026-09-25T08:00:00.000Z',
  correlationId: 'trace-1',
  sequenceNo: 40,
};

describe('W2 canonical telephony event boundary', () => {
  test('validates the complete canonical schema', () => {
    expect(validateCanonicalCallEvent(baseEvent)).toBe(true);
  });

  test('rejects malformed, unknown-version and unknown-event inputs', () => {
    expect(() => validateCanonicalCallEvent({ ...baseEvent, eventId: 'bad' })).toThrow('invalid canonical event id');
    expect(() => validateCanonicalCallEvent({ ...baseEvent, schemaVersion: '9.0' })).toThrow('unsupported canonical event schema version');
    expect(() => validateCanonicalCallEvent({ ...baseEvent, eventType: 'telephony.unknown' })).toThrow('unknown canonical event type');
  });

  test('serializes deterministically with snake-case downstream fields', () => {
    const first = serializeCanonicalCallEvent(baseEvent);
    const second = serializeCanonicalCallEvent({ ...baseEvent });
    expect(first).toBe(second);
    const parsed = JSON.parse(first);
    expect(parsed.event_id).toBe(baseEvent.eventId);
    expect(parsed.tenant_id).toBe(baseEvent.tenantId);
    expect(parsed.provider).toBe('twilio');
    expect(parsed.schema_version).toBe('1.0');
  });

  test('persists the event and outbox in the same database transaction boundary', async () => {
    const queries = [];
    const client = {
      query: async (sql, params) => {
        queries.push({ sql, params });
        if (sql.includes('INSERT INTO telephony_call_events')) return { rows: [{ event_id: baseEvent.eventId }] };
        return { rows: [] };
      },
    };
    const result = await persistCanonicalCallEvent(client, baseEvent);
    expect(result.created).toBe(true);
    expect(queries).toHaveLength(2);
    expect(queries[1].sql).toContain('INSERT INTO telephony_event_outbox');
    expect(queries[1].params[0]).toBe(baseEvent.eventId);
  });

  test('suppresses duplicate canonical identity without creating a second outbox row', async () => {
    const queries = [];
    const client = {
      query: async (sql, params) => {
        queries.push({ sql, params });
        return { rows: [] };
      },
    };
    const result = await persistCanonicalCallEvent(client, baseEvent);
    expect(result.duplicate).toBe(true);
    expect(queries).toHaveLength(1);
  });

  test('preserves tenant identity in the serialized event', () => {
    const a = JSON.parse(serializeCanonicalCallEvent(baseEvent));
    const b = JSON.parse(serializeCanonicalCallEvent({ ...baseEvent, tenantId: '55555555-5555-5555-5555-555555555555' }));
    expect(a.tenant_id).not.toBe(b.tenant_id);
  });
});
