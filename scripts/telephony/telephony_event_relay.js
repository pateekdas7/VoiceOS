'use strict';

const { Pool } = require('pg');
const Redis = require('ioredis');

const MAX_ATTEMPTS = Math.max(1, Number(process.env.TELEPHONY_EVENT_MAX_ATTEMPTS || 5));
const BATCH_SIZE = Math.max(1, Math.min(100, Number(process.env.TELEPHONY_EVENT_RELAY_BATCH || 25)));
const BASE_RETRY_SECONDS = Math.max(1, Number(process.env.TELEPHONY_EVENT_RETRY_BASE_SECONDS || 5));
const STALE_LOCK_SECONDS = Math.max(30, Number(process.env.TELEPHONY_EVENT_STALE_LOCK_SECONDS || 300));

function retryDelaySeconds(attempts) {
  return Math.min(300, BASE_RETRY_SECONDS * (2 ** Math.max(0, attempts - 1)));
}

function validateOutboxPayload(row) {
  if (!row || !row.event_id || !row.tenant_id || !row.event_type || !row.schema_version || !row.payload) {
    throw new Error('malformed_outbox_event');
  }
  if (row.schema_version !== '1.0') throw new Error('unsupported_event_schema');
  if (!row.event_type.startsWith('telephony.call.lifecycle.')) throw new Error('unknown_event_type');
  const payload = typeof row.payload === 'string' ? JSON.parse(row.payload) : row.payload;
  if (payload.event_id !== row.event_id || payload.tenant_id !== String(row.tenant_id)) {
    throw new Error('outbox_identity_mismatch');
  }
  return payload;
}

async function claimBatch(client) {
  await client.query('BEGIN');
  const result = await client.query(
    `WITH candidates AS (
       SELECT event_id
       FROM telephony_event_outbox
       WHERE (
         status='PENDING' AND next_attempt_at <= NOW()
       ) OR (
         status='PROCESSING' AND locked_at < NOW() - ($1::int * INTERVAL '1 second')
       )
       ORDER BY next_attempt_at, created_at
       FOR UPDATE SKIP LOCKED
       LIMIT $2
     )
     UPDATE telephony_event_outbox o
     SET status='PROCESSING', locked_at=NOW()
     FROM candidates c
     WHERE o.event_id=c.event_id
     RETURNING o.*`,
    [STALE_LOCK_SECONDS, BATCH_SIZE]
  );
  await client.query('COMMIT');
  return result.rows;
}

async function markPublished(client, eventId) {
  await client.query(
    `UPDATE telephony_event_outbox
     SET status='PUBLISHED', published_at=NOW(), locked_at=NULL, last_error=NULL
     WHERE event_id=$1 AND status='PROCESSING'`,
    [eventId]
  );
}

async function markFailure(client, row, error) {
  const attempts = Number(row.attempts || 0) + 1;
  const failureCode = error && error.message ? String(error.message).slice(0, 120) : 'relay_failure';
  if (attempts >= MAX_ATTEMPTS) {
    await client.query('BEGIN');
    await client.query(
      `INSERT INTO telephony_event_dlq
       (event_id,tenant_id,event_type,schema_version,payload,attempts,failure_code,last_error)
       VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8)
       ON CONFLICT (event_id) DO UPDATE SET
         attempts=EXCLUDED.attempts, failure_code=EXCLUDED.failure_code,
         last_error=EXCLUDED.last_error, failed_at=NOW()`,
      [row.event_id, row.tenant_id, row.event_type, row.schema_version,
       JSON.stringify(row.payload), attempts, failureCode, String(error.message || error)]
    );
    await client.query(
      `UPDATE telephony_event_outbox
       SET status='DLQ', attempts=$2, locked_at=NULL, last_error=$3
       WHERE event_id=$1`,
      [row.event_id, attempts, String(error.message || error)]
    );
    await client.query('COMMIT');
    return 'DLQ';
  }

  await client.query(
    `UPDATE telephony_event_outbox
     SET status='PENDING', attempts=$2, next_attempt_at=NOW()+($3::int * INTERVAL '1 second'),
         locked_at=NULL, last_error=$4
     WHERE event_id=$1 AND status='PROCESSING'`,
    [row.event_id, attempts, retryDelaySeconds(attempts), String(error.message || error)]
  );
  return 'RETRY';
}

async function relayBatch({ pool, redis }) {
  const client = await pool.connect();
  try {
    const rows = await claimBatch(client);
    const results = { claimed: rows.length, published: 0, retried: 0, dlq: 0, malformed: 0 };
    for (const row of rows) {
      try {
        const payload = validateOutboxPayload(row);
        await redis.lpush(`voiceos:telephony:events:${row.tenant_id}`, JSON.stringify(payload));
        await markPublished(client, row.event_id);
        results.published += 1;
      } catch (error) {
        if (String(error.message || '').startsWith('malformed_') ||
            String(error.message || '').startsWith('unsupported_') ||
            String(error.message || '').startsWith('unknown_') ||
            String(error.message || '').startsWith('outbox_identity_')) {
          results.malformed += 1;
        }
        const outcome = await markFailure(client, row, error);
        if (outcome === 'DLQ') results.dlq += 1;
        else results.retried += 1;
      }
    }
    return results;
  } finally {
    client.release();
  }
}

async function runOnce() {
  const pool = new Pool({ connectionString: process.env.POSTGRES_DSN });
  const redis = new Redis(process.env.REDIS_URL || 'redis://localhost:6379');
  try {
    return await relayBatch({ pool, redis });
  } finally {
    await redis.quit();
    await pool.end();
  }
}

if (require.main === module) {
  runOnce()
    .then(result => {
      process.stdout.write(JSON.stringify({ service:'telephony-event-relay', result }) + '\n');
      process.exit(result.dlq > 0 ? 2 : 0);
    })
    .catch(error => {
      process.stderr.write(JSON.stringify({ service:'telephony-event-relay', error:error.message }) + '\n');
      process.exit(1);
    });
}

module.exports = {
  MAX_ATTEMPTS,
  retryDelaySeconds,
  validateOutboxPayload,
  claimBatch,
  markPublished,
  markFailure,
  relayBatch,
};
