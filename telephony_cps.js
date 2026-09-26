'use strict';

async function acquireProviderRateSlot({ redis, tenantId, limit, nowMs = Date.now(), onLimit = () => {} }) {
  const cps = Math.max(1, Number.parseInt(String(limit), 10) || 1);
  const bucket = Math.floor(nowMs / 1000);
  const key = `voiceos:twilio:cps:${tenantId}:${bucket}`;
  const count = await redis.incr(key);
  if (count === 1) await redis.pexpire(key, 2000);
  if (count > cps) {
    onLimit();
    const err = new Error('TWILIO_RATE_LIMIT');
    err.code = 'TWILIO_RATE_LIMIT';
    throw err;
  }
  return { allowed: true, bucket, count };
}

module.exports = { acquireProviderRateSlot };
