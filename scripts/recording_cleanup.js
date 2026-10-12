#!/usr/bin/env node
/**
 * VoiceOS Recording Cleanup
 * Runs daily. Finds call_runtime_records older than retention period,
 * deletes recordings from Twilio via API, cleans local dir, nullifies DB.
 */
'use strict';

const { Pool }  = require('pg');
const https     = require('https');
const fs        = require('fs');
const path      = require('path');

// Load .env
try {
  const envFile = fs.readFileSync('/opt/voiceos/.env', 'utf8');
  for (const line of envFile.split('\n')) {
    const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^['"]|['"]$/g, '');
  }
} catch (e) { /* .env not found — rely on environment */ }

const RETENTION_DAYS    = parseInt(process.env.RECORDING_RETENTION_DAYS || '90');
const LOCAL_RECORD_DIR  = process.env.CALL_RECORDING_DIR || '/opt/voiceos/recordings';
const TWILIO_ACCOUNT_SID = process.env.TWILIO_ACCOUNT_SID;
const TWILIO_AUTH_TOKEN  = process.env.TWILIO_AUTH_TOKEN;
const DRY_RUN            = process.env.DRY_RUN === '1';

const log = {
  info:  (...a) => console.log( '[INFO]',  new Date().toISOString(), ...a),
  warn:  (...a) => console.warn('[WARN]',  new Date().toISOString(), ...a),
  error: (...a) => console.error('[ERROR]', new Date().toISOString(), ...a),
};

const pool = new Pool({
  connectionString: process.env.POSTGRES_DSN,
  host:     process.env.POSTGRES_HOST || '127.0.0.1',
  port:     parseInt(process.env.POSTGRES_PORT || '5432'),
  database: process.env.POSTGRES_DB   || 'voiceos',
  user:     process.env.POSTGRES_USER || 'voiceos',
  password: process.env.POSTGRES_PASSWORD || '',
  max: 3,
});

// Delete a Twilio recording via REST API
function deleteTwilioRecording(recordingSid) {
  return new Promise((resolve, reject) => {
    if (!TWILIO_ACCOUNT_SID || !TWILIO_AUTH_TOKEN || !recordingSid) {
      resolve({ skipped: true });
      return;
    }
    const options = {
      hostname: 'api.twilio.com',
      port: 443,
      path: `/2010-04-01/Accounts/${TWILIO_ACCOUNT_SID}/Recordings/${recordingSid}.json`,
      method: 'DELETE',
      auth: `${TWILIO_ACCOUNT_SID}:${TWILIO_AUTH_TOKEN}`,
    };
    const req = https.request(options, (res) => {
      // 204 = deleted, 404 = already gone — both are OK
      if (res.statusCode === 204 || res.statusCode === 404) resolve({ deleted: true });
      else reject(new Error(`Twilio DELETE ${recordingSid} → HTTP ${res.statusCode}`));
    });
    req.on('error', reject);
    req.end();
  });
}

// Delete local recordings older than retention
function cleanLocalRecordings() {
  if (!fs.existsSync(LOCAL_RECORD_DIR)) return;
  const cutoff = Date.now() - RETENTION_DAYS * 86400 * 1000;
  let deleted = 0;
  const files = fs.readdirSync(LOCAL_RECORD_DIR);
  for (const f of files) {
    const fp = path.join(LOCAL_RECORD_DIR, f);
    try {
      const stat = fs.statSync(fp);
      if (stat.isFile() && stat.mtimeMs < cutoff) {
        if (!DRY_RUN) fs.unlinkSync(fp);
        deleted++;
      }
    } catch (e) { /* skip */ }
  }
  log.info(`Local recordings cleaned: ${deleted} files ${DRY_RUN ? '(dry-run)' : 'deleted'}`);
}

async function main() {
  log.info(`Recording cleanup started retention=${RETENTION_DAYS}d dry_run=${DRY_RUN}`);
  let total = 0, twilioDeleted = 0, errors = 0;

  try {
    // Find expired records with Twilio recording SIDs
    const { rows } = await pool.query(`
      SELECT record_id, call_sid, recording_sid, recording_url, tenant_id
      FROM call_runtime_records
      WHERE ended_at < NOW() - INTERVAL '${RETENTION_DAYS} days'
        AND recording_sid IS NOT NULL
      ORDER BY ended_at ASC
      LIMIT 500
    `);

    log.info(`Found ${rows.length} recordings to expire`);
    total = rows.length;

    for (const row of rows) {
      try {
        if (!DRY_RUN) {
          await deleteTwilioRecording(row.recording_sid);
          await pool.query(
            `UPDATE call_runtime_records
             SET recording_sid=NULL, recording_url=NULL
             WHERE record_id=$1`,
            [row.record_id]
          );
          twilioDeleted++;
        } else {
          log.info(`[DRY_RUN] Would delete recording_sid=${row.recording_sid} call_sid=${row.call_sid}`);
        }
      } catch (e) {
        errors++;
        log.warn(`Failed to delete recording_sid=${row.recording_sid}: ${e.message}`);
      }
    }
  } catch (e) {
    log.error('Cleanup query failed:', e.message);
  }

  cleanLocalRecordings();
  await pool.end();

  log.info(`Cleanup complete: total=${total} twilio_deleted=${twilioDeleted} errors=${errors}`);
  process.exit(errors > 0 ? 1 : 0);
}

main().catch(e => { log.error('Fatal:', e.message); process.exit(1); });
