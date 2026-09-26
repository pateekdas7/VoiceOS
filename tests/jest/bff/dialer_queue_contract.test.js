'use strict';

jest.mock('pg', () => ({
  Pool: jest.fn(() => ({ on: jest.fn(), query: jest.fn(), connect: jest.fn(), end: jest.fn() })),
}));
jest.mock('ioredis', () => jest.fn(() => ({
  on: jest.fn(), disconnect: jest.fn(), set: jest.fn(), get: jest.fn(), del: jest.fn(),
  incr: jest.fn().mockResolvedValue(1), pexpire: jest.fn().mockResolvedValue(1),
})));
const mockTwilioCallsCreate = jest.fn().mockResolvedValue({ sid: 'CA-test' });
jest.mock('twilio', () => jest.fn(() => ({ calls: { create: mockTwilioCallsCreate } })));

const {
  pendingCallsKey,
  buildPendingCallJob,
  parsePendingCallJob,
} = require('../../../dialer_queue_contract');
const { K, TwilioDialer, renderTelephonyMetrics, _telephonyMetricCounters } = require('../../../dialer_worker');

describe('BFF and W2 worker dial-job boundary', () => {
  const lead = {
    lead_id: 'lead-a',
    campaign_id: 'campaign-a',
    pipeline_id: 'pipeline-a',
    tenant_id: 'tenant-a',
    phone: '+919876543210',
    name: 'Caller',
    language: 'hi',
    score: 80,
  };

  test('producer and worker resolve the same tenant queue key', () => {
    expect(K.pendingQueue(lead.tenant_id)).toBe(pendingCallsKey(lead.tenant_id));
  });

  test('tenant, campaign and lead identity survive producer serialization and consumer parsing', () => {
    const payload = buildPendingCallJob(lead);
    const consumed = parsePendingCallJob(JSON.stringify(payload), pendingCallsKey(lead.tenant_id));
    expect(consumed).toMatchObject({
      tenant_id: lead.tenant_id,
      campaign_id: lead.campaign_id,
      lead_id: lead.lead_id,
      pipeline_id: lead.pipeline_id,
    });
  });

  test('rejects a job placed on another tenant queue', () => {
    const payload = JSON.stringify(buildPendingCallJob(lead));
    expect(() => parsePendingCallJob(payload, pendingCallsKey('tenant-b')))
      .toThrow('dial_job_tenant_queue_mismatch');
  });

  test('rejects a job without durable business identity', () => {
    expect(() => buildPendingCallJob({ ...lead, campaign_id: null }))
      .toThrow('campaign_id');
  });

  test('worker exposes bounded telephony counters in Prometheus text format', () => {
    const key = JSON.stringify(['voiceos_telephony_retry_attempts_total', { reason: 'provider_retry' }]);
    _telephonyMetricCounters.set(key, 2);
    const rendered = renderTelephonyMetrics();
    expect(rendered).toContain('voiceos_dialer_worker_up 1\n');
    expect(rendered).toContain('voiceos_telephony_retry_attempts_total{reason="provider_retry"} 2');
    expect(rendered).not.toContain('\\n');
    _telephonyMetricCounters.delete(key);
  });

  test('W2 caller ID selection works without a global caller ID fallback', async () => {
    const names = ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_FROM_NUMBER',
      'PUBLIC_BFF_URL', 'PUBLIC_WS_URL', 'ALLOW_GLOBAL_TWILIO_FROM'];
    const saved = Object.fromEntries(names.map(name => [name, process.env[name]]));
    Object.assign(process.env, {
      TWILIO_ACCOUNT_SID: 'AC-test',
      TWILIO_AUTH_TOKEN: 'token',
      PUBLIC_BFF_URL: 'https://bff.example.test',
      PUBLIC_WS_URL: 'wss://media.example.test',
    });
    delete process.env.TWILIO_FROM_NUMBER;
    delete process.env.ALLOW_GLOBAL_TWILIO_FROM;
    mockTwilioCallsCreate.mockClear();
    const pool = {
      query: jest.fn().mockResolvedValue({ rows: [{ e164_number: '+14155550123' }] }),
    };
    try {
      const dialer = new TwilioDialer(pool);
      await dialer.initiate(lead);
      expect(pool.query.mock.calls[0][1]).toEqual([lead.tenant_id, lead.campaign_id]);
      expect(mockTwilioCallsCreate).toHaveBeenCalledWith(expect.objectContaining({
        from: '+14155550123',
        url: 'https://media.example.test/voice',
        statusCallback: expect.stringContaining('/dialer/callback?'),
      }));
    } finally {
      for (const name of names) {
        if (saved[name] === undefined) delete process.env[name];
        else process.env[name] = saved[name];
      }
    }
  });
});
