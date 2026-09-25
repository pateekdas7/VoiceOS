'use strict';

const Redis = require('ioredis');
const { acquireProviderRateSlot } = require('../../../telephony_cps');

const REDIS_URL = process.env.REDIS_URL || 'redis://127.0.0.1:6379/15';
const TEST_PREFIX = `voiceos:w2:cps:test:${process.pid}:`;

function client() {
  return new Redis(REDIS_URL, {
    maxRetriesPerRequest: 1,
    retryStrategy: () => null,
  });
}

describe('W2 provider CPS limiter — real Redis integration', () => {
  let redis;

  beforeAll(async () => {
    redis = client();
    try {
      await redis.ping();
    } catch (error) {
      redis.disconnect();
      throw new Error(`NOT EXECUTED — REDIS RUNTIME UNAVAILABLE: ${error.message}`);
    }
  });

  afterEach(async () => {
    const keys = await redis.keys(`${TEST_PREFIX}*`);
    if (keys.length) await redis.del(keys);
  });

  afterAll(async () => {
    if (redis) await redis.quit().catch(() => redis.disconnect());
  });

  test('allows N and rejects N+1 for one tenant in one epoch-second', async () => {
    const nowMs = 1_900_000_000_000;
    const tenant = TEST_PREFIX + 'tenant-a';
    const limit = 3;
    const results = await Promise.all(
      Array.from({ length: limit + 1 }, () =>
        acquireProviderRateSlot({ redis, tenantId: tenant, limit, nowMs })
          .then(() => true)
          .catch((error) => error.code === 'TWILIO_RATE_LIMIT' ? false : Promise.reject(error))
      )
    );
    expect(results.filter(Boolean)).toHaveLength(limit);
    expect(results.filter((v) => v === false)).toHaveLength(1);
  });

  test('concurrent acquisition never exceeds the tenant allowance', async () => {
    const nowMs = 1_900_000_001_000;
    const limit = 5;
    const tenant = TEST_PREFIX + 'concurrent';
    const results = await Promise.all(
      Array.from({ length: 25 }, () =>
        acquireProviderRateSlot({ redis, tenantId: tenant, limit, nowMs })
          .then(() => true)
          .catch((error) => error.code === 'TWILIO_RATE_LIMIT' ? false : Promise.reject(error))
      )
    );
    expect(results.filter(Boolean)).toHaveLength(limit);
  });

  test('tenant allowances are isolated', async () => {
    const nowMs = 1_900_000_002_000;
    const limit = 2;
    const tenantA = TEST_PREFIX + 'a';
    const tenantB = TEST_PREFIX + 'b';
    await acquireProviderRateSlot({ redis, tenantId: tenantA, limit, nowMs });
    await acquireProviderRateSlot({ redis, tenantId: tenantA, limit, nowMs });
    await expect(acquireProviderRateSlot({ redis, tenantId: tenantA, limit, nowMs }))
      .rejects.toMatchObject({ code: 'TWILIO_RATE_LIMIT' });
    await expect(acquireProviderRateSlot({ redis, tenantId: tenantB, limit, nowMs }))
      .resolves.toMatchObject({ allowed: true, count: 1 });
  });

  test('epoch-second rollover starts a fresh allowance', async () => {
    const limit = 2;
    const tenant = TEST_PREFIX + 'rollover';
    await acquireProviderRateSlot({ redis, tenantId: tenant, limit, nowMs: 1_900_000_003_999 });
    await acquireProviderRateSlot({ redis, tenantId: tenant, limit, nowMs: 1_900_000_003_999 });
    await expect(acquireProviderRateSlot({ redis, tenantId: tenant, limit, nowMs: 1_900_000_003_999 }))
      .rejects.toMatchObject({ code: 'TWILIO_RATE_LIMIT' });
    await expect(acquireProviderRateSlot({ redis, tenantId: tenant, limit, nowMs: 1_900_000_004_000 }))
      .resolves.toMatchObject({ allowed: true, count: 1 });
  });

  test('Redis failure is fail-closed because limiter errors propagate', async () => {
    const broken = client();
    await expect(broken.ping()).rejects.toBeTruthy();
    await expect(acquireProviderRateSlot({
      redis: broken,
      tenantId: TEST_PREFIX + 'redis-failure',
      limit: 1,
      nowMs: 1_900_000_005_000,
    })).rejects.toBeTruthy();
    broken.disconnect();
  });
});
