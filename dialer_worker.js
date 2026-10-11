/**
 * VoiceOS Dialer Worker
 *
 * Orchestrates the production call flow:
 *   Redis Queue → Schedule Verification → Twilio Outbound Call
 *   → Existing Python Conversation Runtime (STT/LLM/TTS)
 *
 * Responsibilities:
 *   - Consume voiceos:pending_calls:{tenant_id} Redis Lists (BRPOP, FIFO)
 *   - Verify campaign schedule before every call (timezone, calling window,
 *     campaign status — active/paused/disabled, concurrency limits)
 *   - Manage pipeline concurrency: exactly 1 active call per pipeline_id
 *   - Initiate outbound calls: simulation mode (dev) or Twilio REST (production)
 *   - Handle retries (voiceos:retry_calls:{tid}), callbacks (voiceos:callback_calls:{tid})
 *   - Publish call completion to voiceos:pipeline:completed:{pipeline_id}
 *     so each Pipeline loop proceeds to the next lead with zero idle time
 *   - Async background bookkeeping: Postgres lead status, execution events,
 *     pipeline counters — never blocks the next call
 *   - Worker heartbeat to worker_heartbeats table every 30 s
 *   - Graceful shutdown: drain active calls (up to 120 s), re-queue incomplete leads
 *   - Duplicate call prevention: Redis SET NX + TTL guard per call_sid
 *
 * DIALER_MODE=simulation (default) — no Twilio credentials needed. Simulates
 *   realistic call lifecycle: RINGING → IN_PROGRESS → outcome, random 15-45 s.
 * DIALER_MODE=production — requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
 *   TWILIO_FROM_NUMBER, PUBLIC_BFF_URL. Initiates real Twilio outbound calls
 *   that connect audio to the Python WebSocket server (deployment/cpu/app.py).
 *
 * Does NOT implement STT, LLM, TTS, ConversationEngine, MediaGateway, or any
 * AI logic. Those are invoked via the existing Python runtime on port 8010.
 */

'use strict';

const { Pool }      = require('pg');
const Redis         = require('ioredis');
const { EventEmitter } = require('events');
const crypto        = require('crypto');

// ─── Configuration ─────────────────────────────────────────────────────────

const WORKER_ID     = process.env.WORKER_ID || `worker-${crypto.randomBytes(4).toString('hex')}`;
const DIALER_MODE   = process.env.DIALER_MODE || 'simulation'; // 'simulation' | 'production'
const MAX_CALL_DURATION_S = parseInt(process.env.MAX_CALL_DURATION_S || '180');
const SIM_MIN_DURATION_S  = parseInt(process.env.SIM_MIN_DURATION_S  || '15');
const SIM_MAX_DURATION_S  = parseInt(process.env.SIM_MAX_DURATION_S  || '45');
const HEARTBEAT_INTERVAL_MS = 30_000;
const QUEUE_POLL_TIMEOUT_S  = 2;
const SCHEDULE_REQUEUE_DELAY_S = 600; // re-enqueue outside-window leads after 10 min
const MAX_RETRY_ATTEMPTS   = 3;
const RETRY_DELAY_S        = [60, 300, 900]; // fallback
// Disposition-specific retry backoff (seconds), indexed by attempt (0-based)
const RETRY_DELAY_BY_DISPOSITION = {
  BUSY:      [30,   120,   300],
  NO_ANSWER: [60,   300,   900],
  FAILED:    [300,  900,  1800],
  TIMEOUT:   [120,  600,  1800],
};
const MAX_SYSTEM_CONCURRENT_CALLS = parseInt(process.env.MAX_SYSTEM_CONCURRENT_CALLS || '200');
const CALLBACK_POLL_MS     = 30_000;
const IDLE_LOG_INTERVAL_MS = 60_000;

// Phase 3: Crash reconciliation thresholds
const RECONCILE_INITIATED_THRESHOLD_S   = 60;   // INITIATED+no callSid older than 60s → worker crashed before Twilio responded
const RECONCILE_IN_PROGRESS_THRESHOLD_S = 240;  // IN_PROGRESS older than 240s → call ended but worker crashed post-call
const RECONCILE_FORCE_THRESHOLD_S       = 360;  // Force-fail anything older than 6 min regardless of Twilio status

// SIM call outcome distribution (must sum to 100)
const SIM_OUTCOMES = [
  { disposition: 'COMPLETED',  weight: 55 },
  { disposition: 'NO_ANSWER',  weight: 25 },
  { disposition: 'BUSY',       weight: 10 },
  { disposition: 'FAILED',     weight: 10 },
];

// ─── Redis keys ─────────────────────────────────────────────────────────────

const K = {
  pendingQueue:      (tid)       => `voiceos:pending_calls:${tid}`,
  pendingQueuePri:   (tid, pri)  => `voiceos:pending_calls:${tid}:${pri}`,  // high/normal/low
  retryQueue:        (tid)       => `voiceos:retry_calls:${tid}`,
  callbackQueue:     (tid)       => `voiceos:callback_calls:${tid}`,
  activeCalls:       (tid)       => `voiceos:active_calls:${tid}`,
  activeCampaign:    (cid)       => `voiceos:active_calls:campaign:${cid}`,
  activeTenant:      (tid)       => `voiceos:active_calls:tenant:${tid}`,
  activeSystem:      ()          => 'voiceos:active_calls:system',
  pacingZset:        (cid)       => `voiceos:pacing:${cid}`,
  fromNumberIdx:     (prov, tid) => `voiceos:from_idx:${prov}:${tid}`,
  pipelineCompleted: (pid)       => `voiceos:pipeline:completed:${pid}`,
  callLock:          (sid)       => `voiceos:call:lock:${sid}`,
  workerHeartbeat:   (wid)       => `voiceos:worker:${wid}:alive`,
  reconcileLock:     (aid)       => `voiceos:reconcile:${aid}`,
};

// ─── DB / Redis clients ──────────────────────────────────────────────────────

const pool = process.env.POSTGRES_DSN
  ? new Pool({ connectionString: process.env.POSTGRES_DSN, max: parseInt(process.env.DIALER_PG_POOL_MAX || '10') })
  : new Pool({
      host:     process.env.POSTGRES_HOST || '127.0.0.1',
      port:     parseInt(process.env.POSTGRES_PORT || '5432'),
      database: process.env.POSTGRES_DB   || 'voiceos',
      user:     process.env.POSTGRES_USER || 'voiceos',
      password: process.env.POSTGRES_PASSWORD || '',
      max: parseInt(process.env.DIALER_PG_POOL_MAX || '10'),
    });
pool.on('error', err => log.warn('[pg] idle client error:', err.message));

// Two Redis clients: one for blocking ops (BRPOP), one for everything else.
const redis    = new Redis({ host: process.env.REDIS_HOST || '127.0.0.1', port: parseInt(process.env.REDIS_PORT||'6379'), password: process.env.REDIS_PASSWORD || undefined, maxRetriesPerRequest: 3, retryStrategy: n => Math.min(n * 200, 2000) });
const redisSub = new Redis({ host: process.env.REDIS_HOST || '127.0.0.1', port: parseInt(process.env.REDIS_PORT||'6379'), password: process.env.REDIS_PASSWORD || undefined, maxRetriesPerRequest: 3, retryStrategy: n => Math.min(n * 200, 2000) });
redis.on('error',    e => log.warn('Redis error', e.message));
redisSub.on('error', e => log.warn('RedisSub error', e.message));

// ─── Logger ─────────────────────────────────────────────────────────────────

const log = {
  info:  (...a) => console.log(  '[INFO]', new Date().toISOString(), ...a),
  warn:  (...a) => console.warn( '[WARN]', new Date().toISOString(), ...a),
  error: (...a) => console.error('[ERROR]',new Date().toISOString(), ...a),
  debug: (...a) => process.env.DEBUG && console.log('[DEBUG]', new Date().toISOString(), ...a),
};

// ─── Utility: pick weighted random outcome ──────────────────────────────────

function pickOutcome() {
  const total = SIM_OUTCOMES.reduce((s, o) => s + o.weight, 0);
  let r = Math.random() * total;
  for (const o of SIM_OUTCOMES) { r -= o.weight; if (r <= 0) return o.disposition; }
  return 'COMPLETED';
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ─── Schedule Verifier ───────────────────────────────────────────────────────
// Reads campaign settings from Postgres. Does NOT re-implement scheduling
// logic — only enforces the campaign window already stored in the DB.

class ScheduleVerifier {
  constructor(dbPool) { this._pool = dbPool; }

  async canCallNow(campaignId, tenantId) {
    const { rows } = await this._pool.query(
      `SELECT status, daily_start_hour, daily_end_hour, timezone,
              scheduled_start, scheduled_end, allowed_weekdays, excluded_dates,
              max_concurrent_calls, calls_per_minute
       FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2`,
      [campaignId, tenantId]
    );
    if (!rows.length) return { ok: false, reason: 'campaign_not_found' };

    const c = rows[0];
    if (c.status !== 'ACTIVE') return { ok: false, reason: `campaign_${c.status.toLowerCase()}` };

    const tz = c.timezone || 'Asia/Kolkata';
    const now = new Date();

    // Absolute window (optional)
    if (c.scheduled_start && now < new Date(c.scheduled_start)) {
      return { ok: false, reason: 'before_scheduled_start', scheduled_start: c.scheduled_start };
    }
    if (c.scheduled_end && now > new Date(c.scheduled_end)) {
      return { ok: false, reason: 'after_scheduled_end', scheduled_end: c.scheduled_end };
    }

    // Compute local YYYY-MM-DD, weekday (ISO 1=Mon..7=Sun), and hour in campaign tz
    const fmt = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', hour12: false, weekday: 'short',
    });
    const parts = fmt.formatToParts(now).reduce((o, p) => (o[p.type] = p.value, o), {});
    const localDate = `${parts.year}-${parts.month}-${parts.day}`;
    const localHour = parseInt(parts.hour, 10) % 24;
    const wkMap = { Mon:1, Tue:2, Wed:3, Thu:4, Fri:5, Sat:6, Sun:7 };
    const localWeekday = wkMap[parts.weekday] || 0;

    // Excluded date list
    const excluded = Array.isArray(c.excluded_dates) ? c.excluded_dates.map(d => (d instanceof Date ? d.toISOString().slice(0,10) : String(d).slice(0,10))) : [];
    if (excluded.includes(localDate)) {
      return { ok: false, reason: 'excluded_date', localDate };
    }

    // Allowed weekdays (defaults to all 7 in schema)
    const allowedDays = Array.isArray(c.allowed_weekdays) && c.allowed_weekdays.length
      ? c.allowed_weekdays.map(Number)
      : [1,2,3,4,5,6,7];
    if (!allowedDays.includes(localWeekday)) {
      return { ok: false, reason: 'weekday_disallowed', localWeekday, allowedDays };
    }

    // Daily hour window
    const start = c.daily_start_hour ?? 9;
    const end   = c.daily_end_hour   ?? 18;
    if (localHour < start || localHour > end) {
      return { ok: false, reason: 'outside_calling_window', localHour, window: `${start}-${end}` };
    }

    return {
      ok: true, localDate, localHour, localWeekday,
      maxConcurrentCalls: c.max_concurrent_calls != null ? c.max_concurrent_calls : 10,
      callsPerMinute:     c.calls_per_minute     != null ? c.calls_per_minute     : 30,
    };
  }

  async markLeadInCall(leadId) {
    await this._pool.query(
      `UPDATE leads SET queue_status='IN_CALL', status='CALLED', updated_at=now()
       WHERE lead_id=$1`,
      [leadId]
    );
  }

  async markLeadDone(leadId, disposition) {
    const status = disposition === 'COMPLETED' ? 'DONE' : 'FAILED';
    await this._pool.query(
      `UPDATE leads SET queue_status=$1, status='CALLED', updated_at=now() WHERE lead_id=$2`,
      [status, leadId]
    );
  }
}

// ─── Execution Event Logger (reuses existing logEvent pattern from bff.js) ───

class EventLogger {
  constructor(dbPool) { this._pool = dbPool; }

  async log({ leadId, campaignId, pipelineId, tenantId, eventType, status = 'SUCCESS', message = '', metadata = {} }) {
    try {
      await this._pool.query(
        `INSERT INTO lead_execution_events
         (lead_id, campaign_id, pipeline_id, tenant_id, event_type, status, message, metadata)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)`,
        [leadId, campaignId, pipelineId, tenantId, eventType, status, message, JSON.stringify(metadata)]
      );
    } catch (e) {
      log.warn('EventLogger failed:', e.message);
    }
  }
}

// ─── Pipeline Registry (Postgres) ────────────────────────────────────────────

class PipelineRegistry {
  constructor(dbPool) { this._pool = dbPool; }

  async upsert(pipeline) {
    await this._pool.query(
      `INSERT INTO pipelines (pipeline_id, campaign_id, tenant_id, name, status)
       VALUES ($1,$2,$3,$4,$5)
       ON CONFLICT (pipeline_id) DO UPDATE SET status=$5, updated_at=now()`,
      [pipeline.id, pipeline.campaignId, pipeline.tenantId, pipeline.name, pipeline.status]
    );
  }

  async setBusy(pipelineId, leadId, callSid) {
    await this._pool.query(
      `UPDATE pipelines SET status='BUSY', current_lead_id=$2, current_call_sid=$3, updated_at=now()
       WHERE pipeline_id=$1`,
      [pipelineId, leadId, callSid]
    );
  }

  async setIdle(pipelineId) {
    await this._pool.query(
      `UPDATE pipelines SET status='IDLE', current_lead_id=NULL, current_call_sid=NULL, updated_at=now()
       WHERE pipeline_id=$1`,
      [pipelineId]
    );
  }

  async incrementStats(pipelineId, disposition, durationS) {
    const col = disposition === 'COMPLETED' ? 'calls_completed'
              : disposition === 'NO_ANSWER'  ? 'calls_no_answer'
              : 'calls_failed';
    await this._pool.query(
      `UPDATE pipelines SET ${col}=${col}+1, total_duration_s=total_duration_s+$2, updated_at=now()
       WHERE pipeline_id=$1`,
      [pipelineId, durationS || 0]
    );
  }
}

// ─── Active Call Tracker ─────────────────────────────────────────────────────

class ActiveCallTracker {
  constructor(dbPool) { this._pool = dbPool; }

  async open(callSid, lead) {
    await this._pool.query(
      `INSERT INTO active_calls
       (call_sid, lead_id, campaign_id, pipeline_id, tenant_id, phone, lead_name, language, status)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'INITIATING')
       ON CONFLICT (call_sid) DO NOTHING`,
      [callSid, lead.lead_id, lead.campaign_id, lead.pipeline_id,
       lead.tenant_id, lead.phone, lead.name, lead.language]
    );
    // Mirror in Redis for fast reads
    await redis.hset(K.activeCalls(lead.tenant_id), callSid, JSON.stringify({
      call_sid: callSid, lead_id: lead.lead_id, pipeline_id: lead.pipeline_id,
      phone: lead.phone, lead_name: lead.name, language: lead.language,
      campaign_id: lead.campaign_id, status: 'INITIATING', started_at: new Date().toISOString(),
    }));
    await redis.expire(K.activeCalls(lead.tenant_id), MAX_CALL_DURATION_S * 2);
  }

  async update(callSid, tenantId, { status, answeredAt, endedAt, durationS, disposition }) {
    await this._pool.query(
      `UPDATE active_calls SET
         status=$2,
         answered_at  = COALESCE(answered_at,  $3::timestamptz),
         ended_at     = COALESCE(ended_at,      $4::timestamptz),
         duration_seconds = COALESCE(duration_seconds, $5),
         disposition  = COALESCE(disposition,   $6)
       WHERE call_sid=$1`,
      [callSid, status,
       answeredAt || null, endedAt || null,
       durationS  || null, disposition || null]
    );
    // Update Redis mirror
    const raw = await redis.hget(K.activeCalls(tenantId), callSid);
    if (raw) {
      const entry = JSON.parse(raw);
      Object.assign(entry, { status, disposition, ended_at: endedAt });
      await redis.hset(K.activeCalls(tenantId), callSid, JSON.stringify(entry));
    }
  }

  async close(callSid, tenantId) {
    await redis.hdel(K.activeCalls(tenantId), callSid);
  }
}

// ─── Call Simulator (simulation mode) ───────────────────────────────────────
// Generates a fake call_sid, simulates RINGING → IN_PROGRESS → outcome.
// Signals completion to the Pipeline loop via voiceos:pipeline:completed:{pid}.

class CallSimulator {
  async initiate(lead) {
    const callSid = `SIM${crypto.randomBytes(8).toString('hex').toUpperCase()}`;
    log.info(`[SIM] Initiating simulated call sid=${callSid} phone=${lead.phone} pipeline=${lead.pipeline_id}`);

    const durationS = SIM_MIN_DURATION_S + Math.floor(
      Math.random() * (SIM_MAX_DURATION_S - SIM_MIN_DURATION_S)
    );
    const disposition = pickOutcome();
    const answeredAt  = disposition !== 'NO_ANSWER' && disposition !== 'BUSY'
      ? new Date(Date.now() + 4000).toISOString() : null;

    // Simulate async Twilio lifecycle: fire-and-forget, completes after durationS
    (async () => {
      await sleep(3000); // RINGING phase
      const statusPayload = {
        callSid, disposition,
        answeredAt,
        endedAt:     new Date(Date.now() + durationS * 1000).toISOString(),
        durationS:   disposition === 'COMPLETED' ? durationS : 0,
        pipelineId:  lead.pipeline_id,
        tenantId:    lead.tenant_id,
      };
      await sleep(durationS * 1000);
      // Signal completion to the waiting Pipeline loop
      await redis.lpush(
        K.pipelineCompleted(lead.pipeline_id),
        JSON.stringify(statusPayload)
      );
      await redis.expire(K.pipelineCompleted(lead.pipeline_id), 60);
      log.info(`[SIM] Call completed sid=${callSid} disposition=${disposition} duration=${durationS}s`);
    })().catch(e => log.warn('[SIM] simulator error:', e.message));

    return callSid;
  }
}

// ─── Twilio Dialer (production mode) ────────────────────────────────────────

class TwilioDialer {
  constructor() {
    const sid     = process.env.TWILIO_ACCOUNT_SID;
    const token   = process.env.TWILIO_AUTH_TOKEN;
    if (!sid || !token) throw new Error('TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN required in production mode');
    // Lazy require so simulation mode doesn't need twilio package
    const Twilio  = require('twilio');
    this._client  = new Twilio(sid, token);
    this._from    = process.env.TWILIO_FROM_NUMBER;
    this._bffUrl  = process.env.PUBLIC_BFF_URL; // e.g. https://bff.voiceos.ai
    this._wsUrl   = process.env.PUBLIC_WS_URL;  // e.g. wss://bff.voiceos.ai
    if (!this._from) throw new Error('TWILIO_FROM_NUMBER required');
    if (!this._bffUrl) throw new Error('PUBLIC_BFF_URL required (Twilio status callback)');
    if (!this._wsUrl) throw new Error('PUBLIC_WS_URL required (media stream endpoint)');
  }

  async initiate(lead) {
    // Point Twilio at media-gateway /voice so MG mints the admission_token itself.
    const mgHttp = this._wsUrl.replace(/^wss:/, 'https:').replace(/^ws:/, 'http:');
    const twimlUrl = `${mgHttp}/voice`;
    const callbackUrl = `${this._bffUrl}/dialer/callback?pipeline_id=${encodeURIComponent(lead.pipeline_id)}&lead_id=${encodeURIComponent(lead.lead_id)}&tenant_id=${encodeURIComponent(lead.tenant_id)}`;

    const recordingCallbackUrl = `${this._bffUrl}/dialer/recording-callback?pipeline_id=${encodeURIComponent(lead.pipeline_id)}&lead_id=${encodeURIComponent(lead.lead_id)}&tenant_id=${encodeURIComponent(lead.tenant_id)}`;
    const call = await this._client.calls.create({
      to:             `+91${lead.phone}`,
      from:           lead._caller_id || this._from,
      url:            twimlUrl,
      statusCallback: callbackUrl,
      statusCallbackMethod: 'POST',
      statusCallbackEvent:  ['initiated','ringing','answered','completed'],
      machineDetection: 'Enable',
      timeout:         30,
      record:          true,
      recordingStatusCallback:       recordingCallbackUrl,
      recordingStatusCallbackMethod: 'POST',
    });

    log.info(`[TWILIO] Call initiated sid=${call.sid} to=${lead.phone} pipeline=${lead.pipeline_id}`);
    return call.sid;
  }
}


// ─── Exotel Dialer ───────────────────────────────────────────────────────────

class ExotelDialer {
  constructor() {
    this._accountSid = process.env.EXOTEL_ACCOUNT_SID;
    this._apiKey     = process.env.EXOTEL_API_KEY;
    this._apiToken   = process.env.EXOTEL_API_TOKEN;
    this._from       = process.env.EXOTEL_FROM_NUMBER;
    this._bffUrl     = process.env.PUBLIC_BFF_URL;
    this._wsUrl      = process.env.PUBLIC_WS_URL;
    if (!this._accountSid || !this._apiKey || !this._apiToken) {
      throw new Error('EXOTEL_ACCOUNT_SID, EXOTEL_API_KEY, EXOTEL_API_TOKEN required');
    }
    if (!this._from)   throw new Error('EXOTEL_FROM_NUMBER required');
    if (!this._bffUrl) throw new Error('PUBLIC_BFF_URL required');
    if (!this._wsUrl)  throw new Error('PUBLIC_WS_URL required');
  }

  async initiate(lead) {
    const https       = require('https');
    const querystring = require('querystring');
    const callbackUrl = `${this._bffUrl}/dialer/callback/exotel?pipeline_id=${encodeURIComponent(lead.pipeline_id)}&lead_id=${encodeURIComponent(lead.lead_id)}&tenant_id=${encodeURIComponent(lead.tenant_id)}`;
    // ExoML served by voice-runtime at /voice/exotel
    const mgHttp   = this._wsUrl.replace(/^wss:/, 'https:').replace(/^ws:/, 'http:');
    const exomlUrl = `${mgHttp}/voice/exotel`;

    const params = querystring.stringify({
      From:           lead._caller_id || this._from,
      To:             `0${lead.phone}`,
      CallerId:       lead._caller_id || this._from,
      Url:            exomlUrl,
      StatusCallback: callbackUrl,
      Record:         'true',
    });

    return new Promise((resolve, reject) => {
      const auth    = Buffer.from(`${this._apiKey}:${this._apiToken}`).toString('base64');
      const options = {
        hostname: 'api.exotel.com',
        port:     443,
        path:     `/v1/Accounts/${this._accountSid}/Calls/connect`,
        method:   'POST',
        headers:  {
          'Authorization':  `Basic ${auth}`,
          'Content-Type':   'application/x-www-form-urlencoded',
          'Content-Length': Buffer.byteLength(params),
        },
      };
      const req = https.request(options, (res) => {
        let body = '';
        res.on('data', d => body += d);
        res.on('end', () => {
          try {
            const data = JSON.parse(body);
            const sid  = data.Call && data.Call.Sid;
            if (!sid) return reject(new Error(`Exotel missing Sid: ${body.slice(0, 200)}`));
            log.info(`[EXOTEL] Call initiated sid=${sid} to=${lead.phone} pipeline=${lead.pipeline_id}`);
            resolve(`exotel:${sid}`);
          } catch (e) {
            reject(new Error(`Exotel parse error: ${e.message}`));
          }
        });
      });
      req.on('error', reject);
      req.write(params);
      req.end();
    });
  }
}

// ─── Plivo Dialer ────────────────────────────────────────────────────────────

class PlivoDialer {
  constructor() {
    this._authId    = process.env.PLIVO_AUTH_ID;
    this._authToken = process.env.PLIVO_AUTH_TOKEN;
    this._from      = process.env.PLIVO_FROM_NUMBER;
    this._bffUrl    = process.env.PUBLIC_BFF_URL;
    this._wsUrl     = process.env.PUBLIC_WS_URL;
    if (!this._authId || !this._authToken) {
      throw new Error('PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN required');
    }
    if (!this._from)   throw new Error('PLIVO_FROM_NUMBER required');
    if (!this._bffUrl) throw new Error('PUBLIC_BFF_URL required');
    if (!this._wsUrl)  throw new Error('PUBLIC_WS_URL required');
    const plivo  = require('plivo');
    this._client = new plivo.Client(this._authId, this._authToken);
  }

  async initiate(lead) {
    const callbackUrl = `${this._bffUrl}/dialer/callback/plivo?pipeline_id=${encodeURIComponent(lead.pipeline_id)}&lead_id=${encodeURIComponent(lead.lead_id)}&tenant_id=${encodeURIComponent(lead.tenant_id)}`;
    const mgHttp      = this._wsUrl.replace(/^wss:/, 'https:').replace(/^ws:/, 'http:');
    const answerUrl   = `${mgHttp}/voice/plivo`;

    const response = await this._client.calls.create(
      lead._caller_id || this._from,
      `+91${lead.phone}`,
      answerUrl,
      {
        statusCallbackUrl:    callbackUrl,
        statusCallbackMethod: 'POST',
        machineDetection:     'true',
        recordCallbackUrl:    callbackUrl,
      }
    );
    const sid = response.requestUuid;
    log.info(`[PLIVO] Call initiated uuid=${sid} to=${lead.phone} pipeline=${lead.pipeline_id}`);
    return `plivo:${sid}`;
  }
}

// ─── Telnyx Dialer ───────────────────────────────────────────────────────────

class TelnxDialer {
  constructor() {
    this._apiKey       = process.env.TELNYX_API_KEY;
    this._connectionId = process.env.TELNYX_CONNECTION_ID;
    this._from         = process.env.TELNYX_FROM_NUMBER;
    this._bffUrl       = process.env.PUBLIC_BFF_URL;
    if (!this._apiKey)       throw new Error('TELNYX_API_KEY required');
    if (!this._connectionId) throw new Error('TELNYX_CONNECTION_ID required');
    if (!this._from)         throw new Error('TELNYX_FROM_NUMBER required');
    if (!this._bffUrl)       throw new Error('PUBLIC_BFF_URL required');
    this._telnyx = require('telnyx')(this._apiKey);
  }

  async initiate(lead) {
    const callbackUrl = `${this._bffUrl}/dialer/callback/telnyx?pipeline_id=${encodeURIComponent(lead.pipeline_id)}&lead_id=${encodeURIComponent(lead.lead_id)}&tenant_id=${encodeURIComponent(lead.tenant_id)}`;

    const call = await this._telnyx.calls.create({
      connection_id:      this._connectionId,
      to:                 `+91${lead.phone}`,
      from:               lead._caller_id || this._from,
      webhook_url:        callbackUrl,
      webhook_url_method: 'POST',
      record_audio:       true,
      record_channels:    'dual',
    });

    const sid = call.data && (call.data.call_control_id || call.data.call_session_id);
    if (!sid) throw new Error('Telnyx missing call_control_id in response');
    log.info(`[TELNYX] Call initiated id=${sid} to=${lead.phone} pipeline=${lead.pipeline_id}`);
    return `telnyx:${sid}`;
  }
}

// ─── Multi-Provider Dialer ───────────────────────────────────────────────────
// Tries providers in priority order; falls over to next on error.
// Configured via TELEPHONY_PROVIDER_ORDER (comma-separated, default "twilio").

class MultiProviderDialer {
  constructor() {
    const order = (process.env.TELEPHONY_PROVIDER_ORDER || 'twilio')
      .split(',').map(s => s.trim().toLowerCase()).filter(Boolean);

    this._providers = [];
    for (const name of order) {
      try {
        this._providers.push({ name, dialer: MultiProviderDialer._build(name) });
        log.info(`[MultiProvider] Registered provider: ${name}`);
      } catch (e) {
        log.warn(`[MultiProvider] Provider ${name} skipped (${e.message})`);
      }
    }

    if (this._providers.length === 0) {
      throw new Error('No telephony providers available — check credentials in .env');
    }
  }

  static _build(name) {
    switch (name) {
      case 'twilio': return new TwilioDialer();
      case 'exotel': return new ExotelDialer();
      case 'plivo':  return new PlivoDialer();
      case 'telnyx': return new TelnxDialer();
      default: throw new Error(`Unknown provider: ${name}`);
    }
  }

  async initiate(lead) {
    const errors = [];
    for (const { name, dialer } of this._providers) {
      try {
        const sid = await dialer.initiate(lead);
        if (name !== this._providers[0].name) {
          log.warn(`[MultiProvider] Failover used provider=${name} lead=${lead.lead_id}`);
        }
        return sid;
      } catch (e) {
        log.warn(`[MultiProvider] Provider ${name} failed lead=${lead.lead_id}: ${e.message}`);
        errors.push(`${name}: ${e.message}`);
      }
    }
    throw new Error(`All providers failed — ${errors.join('; ')}`);
  }
}

// ─── Phase 3: Crash Reconciler ──────────────────────────────────────────────
// On every worker startup, scans for call_attempts left INITIATED or IN_PROGRESS
// by workers that have since died (heartbeat key gone). For each stale attempt:
//   1. Claims it with Redis NX + atomic DB UPDATE to RECONCILING (multi-worker safe)
//   2. In production, checks Twilio for the real call outcome before deciding disposition
//   3. Repairs lead queue_status and schedules a retry if retries remain
//   4. Resets the pipeline to IDLE and removes the active_calls row
//   5. Writes a recovery_log entry for observability

class CrashReconciler {
  constructor(dbPool, redisClient) {
    this._pool  = dbPool;
    this._redis = redisClient;
    // Twilio client for production status checks — null in simulation mode
    this._twilio = null;
    if (DIALER_MODE === 'production' && process.env.TWILIO_ACCOUNT_SID && process.env.TWILIO_AUTH_TOKEN) {
      try {
        const Twilio = require('twilio');
        this._twilio = new Twilio(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN);
      } catch (e) {
        log.warn('[Reconciler] Twilio client unavailable — will skip Twilio status checks:', e.message);
      }
    }
  }

  async reconcile() {
    log.info(`[Reconciler:${WORKER_ID}] Starting crash reconciliation scan`);
    const scanStart = Date.now();
    let reconciled = 0;
    let skipped    = 0;
    let errors     = 0;

    try {
      const stale = await this._findStaleAttempts();
      log.info(`[Reconciler:${WORKER_ID}] Found ${stale.length} stale attempt(s) to evaluate`);

      for (const attempt of stale) {
        try {
          const result = await this._reconcileAttempt(attempt);
          if (result === 'reconciled') reconciled++;
          else skipped++;
        } catch (e) {
          errors++;
          log.error(`[Reconciler:${WORKER_ID}] Failed to reconcile attempt=${attempt.attempt_id}:`, e.message);
        }
      }
    } catch (e) {
      log.error(`[Reconciler:${WORKER_ID}] Scan failed:`, e.message);
    }

    const durationMs = Date.now() - scanStart;
    log.info(`[Reconciler:${WORKER_ID}] Done — reconciled=${reconciled} skipped=${skipped} errors=${errors} duration=${durationMs}ms`);
  }

  // Find call_attempts that are stale and whose originating worker appears dead.
  async _findStaleAttempts() {
    const { rows } = await this._pool.query(`
      SELECT attempt_id, tenant_id, campaign_id, lead_id, pipeline_id,
             call_sid, worker_id, status, initiated_at
      FROM call_attempts
      WHERE status IN ('INITIATED', 'IN_PROGRESS')
        AND worker_id != $1
        AND (
          (status = 'INITIATED'   AND call_sid IS NULL
           AND initiated_at < NOW() - ($2 || ' seconds')::INTERVAL)
          OR
          (status = 'INITIATED'   AND call_sid IS NOT NULL
           AND initiated_at < NOW() - ($4 || ' seconds')::INTERVAL)
          OR
          (status = 'IN_PROGRESS'
           AND initiated_at < NOW() - ($3 || ' seconds')::INTERVAL)
        )
      ORDER BY initiated_at ASC
      LIMIT 50
    `, [WORKER_ID, RECONCILE_INITIATED_THRESHOLD_S, RECONCILE_IN_PROGRESS_THRESHOLD_S, RECONCILE_FORCE_THRESHOLD_S]);

    // Filter out attempts from workers whose heartbeat is still alive.
    // Cache per-worker result so Redis is only queried once per worker per scan.
    const workerAlive = new Map();
    const filtered = [];
    for (const row of rows) {
      if (!workerAlive.has(row.worker_id)) {
        const exists = await this._redis.exists(K.workerHeartbeat(row.worker_id));
        workerAlive.set(row.worker_id, exists === 1);
      }
      if (!workerAlive.get(row.worker_id)) filtered.push(row);
    }
    return filtered;
  }

  async _reconcileAttempt(attempt) {
    // Multi-worker safety: Redis NX lock first, then atomic DB UPDATE.
    // Both are required: Redis prevents the thundering herd, DB prevents
    // the race if two workers claim the Redis lock at the same millisecond.
    const claimKey = K.reconcileLock(attempt.attempt_id);
    const claimed  = await this._redis.set(claimKey, WORKER_ID, 'EX', 120, 'NX');
    if (!claimed) return 'skipped';

    const { rows: claimedRows } = await this._pool.query(
      `UPDATE call_attempts SET status='RECONCILING'
       WHERE attempt_id=$1 AND status IN ('INITIATED','IN_PROGRESS')
       RETURNING *`,
      [attempt.attempt_id]
    );
    if (!claimedRows.length) {
      await this._redis.del(claimKey);
      return 'skipped';
    }

    const originalStatus = attempt.status;
    const startedAt  = new Date().toISOString();
    const startMs    = Date.now();
    const failureClass = (!attempt.call_sid || attempt.status === 'INITIATED')
      ? 'CRASH_INITIATED' : 'CRASH_IN_PROGRESS';

    try {
      let disposition = 'FAILED';

      if (attempt.call_sid && this._twilio) {
        const ageS = (Date.now() - new Date(attempt.initiated_at).getTime()) / 1000;
        let twilioDisposition;
        let twilioCheckFailed = false;

        try {
          twilioDisposition = await this._checkTwilioStatus(attempt.call_sid);
        } catch (e) {
          twilioCheckFailed = true;
          log.warn(`[Reconciler] Twilio status check failed for sid=${attempt.call_sid}:`, e.message);
        }

        if (twilioCheckFailed) {
          if (ageS < RECONCILE_FORCE_THRESHOLD_S) {
            // Cannot confirm outcome — conservative skip rather than risk killing a live call
            await this._pool.query(
              `UPDATE call_attempts SET status='IN_PROGRESS' WHERE attempt_id=$1`,
              [attempt.attempt_id]
            );
            await this._redis.del(claimKey);
            log.warn(`[Reconciler] Cannot confirm Twilio status for sid=${attempt.call_sid}, age=${Math.round(ageS)}s — skipping`);
            return 'skipped';
          }
          log.warn(`[Reconciler] Force-failing attempt=${attempt.attempt_id} past ${RECONCILE_FORCE_THRESHOLD_S}s threshold`);
        } else if (twilioDisposition === 'active') {
          // Call is still live on Twilio — revert claim and leave it running
          await this._pool.query(
            `UPDATE call_attempts SET status='IN_PROGRESS' WHERE attempt_id=$1`,
            [attempt.attempt_id]
          );
          await this._redis.del(claimKey);
          log.info(`[Reconciler] attempt=${attempt.attempt_id} sid=${attempt.call_sid} is still active on Twilio — skipping`);
          return 'skipped';
        } else {
          disposition = twilioDisposition;
        }
      }

      await this._repairLeadState(attempt, disposition);

      if (attempt.pipeline_id) {
        await this._pool.query(
          `UPDATE pipelines
           SET status='IDLE', current_lead_id=NULL, current_call_sid=NULL, updated_at=now()
           WHERE pipeline_id=$1 AND (current_call_sid=$2 OR status='BUSY')`,
          [attempt.pipeline_id, attempt.call_sid || '']
        );
      }

      if (attempt.call_sid) {
        await this._pool.query(
          `DELETE FROM active_calls WHERE call_sid=$1`,
          [attempt.call_sid]
        ).catch(e => log.warn('[Reconciler] active_calls delete failed:', e.message));
        await this._redis.hdel(K.activeCalls(attempt.tenant_id), attempt.call_sid)
          .catch(e => log.warn('[Reconciler] Redis activeCalls hdel failed:', e.message));
      }

      await this._pool.query(
        `UPDATE call_attempts
         SET status=$1, disposition=$2, ended_at=NOW(), error_message='worker_crash_reconciled'
         WHERE attempt_id=$3`,
        [disposition, disposition, attempt.attempt_id]
      );

      const durationMs = Date.now() - startMs;
      await this._pool.query(
        `INSERT INTO recovery_log
           (tenant_id, call_id, failure_class, strategy_name, outcome, detail, started_at, completed_at, duration_ms)
         VALUES ($1,$2,$3,'crash_reconciliation','success',$4::jsonb,$5,NOW(),$6)`,
        [
          attempt.tenant_id,
          attempt.call_sid || attempt.attempt_id,
          failureClass,
          JSON.stringify({
            attempt_id:           attempt.attempt_id,
            original_status:      originalStatus,
            crashed_worker_id:    attempt.worker_id,
            reconciler_worker_id: WORKER_ID,
            disposition,
            lead_id:              attempt.lead_id,
            tenant_id:            attempt.tenant_id,
          }),
          startedAt,
          durationMs,
        ]
      ).catch(e => log.warn('[Reconciler] recovery_log write failed:', e.message));

      log.info(`[Reconciler:${WORKER_ID}] Reconciled attempt=${attempt.attempt_id} disposition=${disposition} failureClass=${failureClass}`);
      return 'reconciled';

    } catch (e) {
      // Restore original status so the next scan can retry reconciliation
      await this._pool.query(
        `UPDATE call_attempts SET status=$1 WHERE attempt_id=$2`,
        [originalStatus, attempt.attempt_id]
      ).catch(() => {});
      await this._redis.del(claimKey);
      throw e;
    }
  }

  // Returns 'active' if the call is still live, a terminal disposition string if ended.
  // Throws on API errors that are not 404 (caller decides how to handle conservatively).
  // Multi-provider: SIDs are prefixed "twilio:", "exotel:", "plivo:", "telnyx:".
  async _checkTwilioStatus(callSid) {
    const TWILIO_ACTIVE   = new Set(['queued', 'ringing', 'in-progress']);
    const TWILIO_TERMINAL = {
      completed:  'COMPLETED',
      busy:       'BUSY',
      failed:     'FAILED',
      'no-answer': 'NO_ANSWER',
      canceled:   'FAILED',
    };
    // Non-Twilio providers cannot be checked via Twilio API — fail conservatively
    if (callSid && (callSid.startsWith('exotel:') || callSid.startsWith('plivo:') || callSid.startsWith('telnyx:'))) {
      log.warn(`[Reconciler] Non-Twilio sid=${callSid} — cannot verify status, marking FAILED`);
      return 'FAILED';
    }
    // Strip "twilio:" prefix if present (added by MultiProviderDialer)
    const rawSid = (callSid && callSid.startsWith('twilio:')) ? callSid.slice(7) : callSid;
    try {
      const call = await this._twilio.calls(rawSid).fetch();
      if (TWILIO_ACTIVE.has(call.status)) return 'active';
      return TWILIO_TERMINAL[call.status] || 'FAILED';
    } catch (e) {
      if (e.status === 404 || e.code === 20404) return 'FAILED'; // call not found = definitively over
      throw e;
    }
  }

  async _repairLeadState(attempt, disposition) {
    const { rows } = await this._pool.query(
      `SELECT COUNT(*)::int AS cnt FROM call_attempts
       WHERE lead_id=$1 AND tenant_id=$2`,
      [attempt.lead_id, attempt.tenant_id]
    );
    const totalAttempts = rows[0].cnt;

    // Always update leads to reflect the call ended (mirroring _handleCallEnd behaviour)
    await this._pool.query(
      `UPDATE leads SET queue_status='FAILED', status='CALLED', updated_at=now()
       WHERE lead_id=$1`,
      [attempt.lead_id]
    );

    if (totalAttempts > MAX_RETRY_ATTEMPTS) {
      log.info(`[Reconciler] Lead ${attempt.lead_id} exhausted retries (${totalAttempts}/${MAX_RETRY_ATTEMPTS}) → FAILED`);
      return;
    }

    // Retries remain — reconstruct lead payload and push to retry sorted set
    const { rows: leadRows } = await this._pool.query(
      `SELECT lead_id, phone, name, language FROM leads WHERE lead_id=$1 LIMIT 1`,
      [attempt.lead_id]
    );

    if (leadRows.length) {
      const delaySec = RETRY_DELAY_S[Math.min(totalAttempts - 1, RETRY_DELAY_S.length - 1)];
      const retryAt  = Date.now() + delaySec * 1000;
      const retryPayload = {
        lead_id:           attempt.lead_id,
        phone:             leadRows[0].phone,
        name:              leadRows[0].name,
        language:          leadRows[0].language,
        tenant_id:         attempt.tenant_id,
        campaign_id:       attempt.campaign_id,
        pipeline_id:       attempt.pipeline_id,
        _retry_count:      totalAttempts,
        _retry_after:      retryAt,
        _last_disposition: disposition,
        _reconciled:       true,
      };
      await this._redis.zadd(K.retryQueue(attempt.tenant_id), retryAt, JSON.stringify(retryPayload));
      log.info(`[Reconciler] Lead ${attempt.lead_id} queued for retry in ${delaySec}s (attempt #${totalAttempts})`);
    } else {
      log.warn(`[Reconciler] Lead ${attempt.lead_id} not found in DB — cannot schedule retry`);
    }
  }
}

// ─── Pipeline — manages exactly one concurrent call ──────────────────────────

class Pipeline extends EventEmitter {
  constructor({ id, campaignId, tenantId, name, registry, tracker, scheduler, eventLogger, dialer, dncChecker, capacityGuard, pacingEnforcer }) {
    super();
    this.id         = id;
    this.campaignId = campaignId;
    this.tenantId   = tenantId;
    this.name       = name || `Pipeline-${id.slice(0, 8)}`;
    this.status     = 'IDLE';
    this.buffer     = [];       // local lead buffer (in-memory)
    this._registry  = registry;
    this._tracker   = tracker;
    this._scheduler       = scheduler;
    this._logger          = eventLogger;
    this._dialer          = dialer;
    this._dnc             = dncChecker       || null;
    this._capacityGuard   = capacityGuard    || null;
    this._pacingEnforcer  = pacingEnforcer   || null;
    this._running         = false;
    this._resolveNext = null;   // called when a new lead arrives in buffer
  }

  async start() {
    if (this._running) return;
    this._running = true;
    await this._registry.upsert({ id: this.id, campaignId: this.campaignId, tenantId: this.tenantId, name: this.name, status: 'IDLE' });
    log.info(`[Pipeline:${this.name}] started`);
    this._loop().catch(e => { log.error(`[Pipeline:${this.name}] loop crashed:`, e.message); this._running = false; });
  }

  // Called by main consumer — routes lead into this pipeline's buffer
  enqueue(lead) {
    this.buffer.push(lead);
    if (this._resolveNext) {
      this._resolveNext();
      this._resolveNext = null;
    }
  }

  get bufferSize() { return this.buffer.length; }

  async shutdown() {
    this._running = false;
    if (this._resolveNext) { this._resolveNext(); this._resolveNext = null; }
  }

  // ── Core pipeline loop: zero idle time between calls ──────────────────────
  async _loop() {
    while (this._running) {
      // Wait for work if buffer is empty
      if (this.buffer.length === 0) {
        await new Promise(resolve => { this._resolveNext = resolve; });
        if (!this._running) break;
        if (this.buffer.length === 0) continue;
      }

      const lead = this.buffer.shift();
      await this._processLead(lead);
      // No await, no sleep — immediately loops to pick next lead
    }
    log.info(`[Pipeline:${this.name}] stopped`);
  }

  async _processLead(lead) {
    // ── 1. Schedule verification ─────────────────────────────────────────
    const schedule = await this._scheduler.canCallNow(lead.campaign_id, lead.tenant_id);
    if (!schedule.ok) {
      log.info(`[Pipeline:${this.name}] Campaign schedule blocked: ${schedule.reason} — requeueing lead ${lead.lead_id}`);
      await this._requeueDelayed(lead, schedule.reason);
      return;
    }

    // ── 1b. DNC check — block before any lock or DB write ───────────────
    if (this._dnc) {
      try {
        const blocked = await this._dnc.isBlocked(lead.phone, lead.tenant_id);
        if (blocked) {
          log.warn(`[Pipeline:${this.name}] DNC block for lead=${lead.lead_id} phone=${lead.phone}`);
          await pool.query(
            `UPDATE leads SET queue_status='DNC_BLOCKED', updated_at=NOW() WHERE lead_id=$1`,
            [lead.lead_id]
          ).catch(e => log.warn('[DNC] leads update failed:', e.message));
          await this._logger.log({
            leadId: lead.lead_id, campaignId: lead.campaign_id,
            pipelineId: this.id, tenantId: lead.tenant_id,
            eventType: 'CALL_DNC_BLOCKED', status: 'SKIP',
            message: `Lead blocked by DNC registry phone=${lead.phone}`,
            metadata: { phone: lead.phone },
          }).catch(() => {});
          return;
        }
      } catch (e) {
        log.warn(`[Pipeline:${this.name}] DNC check error — allowing call: ${e.message}`);
      }
    }

    // ── 1c. Capacity check — back-pressure before lock ─────────────────
    let _capacityAcquired = false;
    if (this._capacityGuard && schedule.ok) {
      // Fetch tenant max from DB (best-effort; fall back to 0 = no limit)
      let tenantMax = 0;
      try {
        const tr = await pool.query(
          `SELECT max_concurrent_calls FROM tenants WHERE tenant_id=$1`,
          [lead.tenant_id]
        );
        tenantMax = tr.rows[0]?.max_concurrent_calls ?? 50;
      } catch {}

      const ok = await this._capacityGuard.acquire(
        lead,
        schedule.maxConcurrentCalls,
        tenantMax
      );
      if (!ok) {
        // Requeue after 5s so the slot can free up
        const retryAt = Date.now() + 5_000;
        await redis.zadd(K.retryQueue(lead.tenant_id), retryAt, JSON.stringify({
          ...lead, _retry_after: retryAt, _schedule_blocked: 'capacity_limit',
        })).catch(() => {});
        return;
      }
      _capacityAcquired = true;
    }

    // ── 1d. Pacing check — calls-per-minute sliding window ───────────────
    if (this._pacingEnforcer && schedule.callsPerMinute > 0) {
      const ok = await this._pacingEnforcer.canCall(lead.campaign_id, schedule.callsPerMinute);
      if (!ok) {
        if (_capacityAcquired) await this._capacityGuard.release(lead);
        const retryAt = Date.now() + 2_000;
        await redis.zadd(K.retryQueue(lead.tenant_id), retryAt, JSON.stringify({
          ...lead, _retry_after: retryAt, _schedule_blocked: 'pacing_limit',
        })).catch(() => {});
        return;
      }
    }

    // ── 2. Duplicate call prevention ─────────────────────────────────────
    const lockKey = K.callLock(`${lead.lead_id}:${lead.campaign_id}`);
    const locked = await redis.set(lockKey, WORKER_ID, 'EX', MAX_CALL_DURATION_S * 2, 'NX');
    if (!locked) {
      log.warn(`[Pipeline:${this.name}] Duplicate call prevented for lead ${lead.lead_id}`);
      return;
    }

    // ── 3. Mark pipeline BUSY ─────────────────────────────────────────────
    this.status = 'BUSY';
    this.emit('status', 'BUSY');

    let callSid;
    let attemptId;
    const callStart = Date.now();
    try {
      // ── 2e. Insert call_attempt record BEFORE Twilio call ─────────────
      // Provides a durable trace even if the worker crashes after initiation.
      const ar = await pool.query(
        `INSERT INTO call_attempts
           (tenant_id, campaign_id, lead_id, pipeline_id, worker_id, status)
         VALUES ($1,$2,$3,$4,$5,'INITIATED') RETURNING attempt_id`,
        [lead.tenant_id, lead.campaign_id, lead.lead_id, lead.pipeline_id, WORKER_ID]
      );
      attemptId = ar.rows[0].attempt_id;

      // ── 4. Initiate call ───────────────────────────────────────────────
      callSid = await this._dialer.initiate(lead);

      // ── 2e. Back-fill call_sid now that Twilio has accepted the call ───
      await pool.query(
        `UPDATE call_attempts SET call_sid=$1, status='IN_PROGRESS' WHERE attempt_id=$2`,
        [callSid, attemptId]
      );

      // ── 5. Record call open (synchronous — must complete before next call)
      await this._openCallRecords(callSid, lead);

      // ── 6. Wait for call completion (with callSid validation) ──────────
      const result = await this._waitForCompletion(callSid, lead);

      const durationS = Math.round((Date.now() - callStart) / 1000);
      log.info(`[Pipeline:${this.name}] Call done sid=${callSid} disposition=${result.disposition} duration=${durationS}s`);

      // ── 7. Synchronous post-call bookkeeping — lock held until complete ─
      await this._handleCallEnd(callSid, lead, result, durationS, attemptId);

    } catch (e) {
      log.error(`[Pipeline:${this.name}] Call error lead=${lead.lead_id}:`, e.message);
      if (callSid) {
        try {
          await this._handleCallError(callSid, lead, e, attemptId);
        } catch (cleanupErr) {
          log.error(`[Pipeline:${this.name}] Cleanup also failed:`, cleanupErr.message);
        }
      } else if (attemptId) {
        // Twilio initiation failed before callSid was returned
        await pool.query(
          `UPDATE call_attempts SET status='FAILED', ended_at=NOW(), error_message=$1 WHERE attempt_id=$2`,
          [e.message, attemptId]
        ).catch(dbErr => log.warn('[call_attempts] failed to mark FAILED:', dbErr.message));
      }
    } finally {
      // ── 8. Release lock + capacity counters after all synchronous work ──
      this.status = 'IDLE';
      this.emit('status', 'IDLE');
      await redis.del(lockKey);
      if (_capacityAcquired && this._capacityGuard) {
        await this._capacityGuard.release(lead);
      }
    }
  }

  // ── Wait for completion signal via non-blocking RPOP polling ──────────────
  // Uses polling (200ms intervals) to avoid blocking the Redis connection —
  // BRPOP would block the shared client, preventing the simulator's LPUSH from
  // executing on the same connection. Polling keeps both directions free.
  async _waitForCompletion(callSid, lead) {
    const completedKey = K.pipelineCompleted(this.id);
    const deadline = Date.now() + (MAX_CALL_DURATION_S + 5) * 1000;

    while (Date.now() < deadline) {
      try {
        const raw = await redis.rpop(completedKey);
        if (raw) {
          const payload = JSON.parse(raw);
          if (payload.callSid !== callSid) {
            // Event belongs to a different call on this pipeline — put it back and keep polling.
            // This prevents a stale/delayed callback from resolving the wrong call.
            await redis.lpush(completedKey, raw);
            await sleep(200);
            continue;
          }
          return payload;
        }
      } catch (e) {
        log.warn(`[Pipeline:${this.name}] rpop error:`, e.message);
      }
      await sleep(200); // 200ms poll — ≤200ms idle gap between call end and next call start
    }
    return { callSid, disposition: 'TIMEOUT', endedAt: new Date().toISOString(), durationS: MAX_CALL_DURATION_S };
  }

  // ── Background: open records (non-blocking, called via setImmediate) ──────
  async _openCallRecords(callSid, lead) {
    try {
      await this._tracker.open(callSid, lead);
      await this._registry.setBusy(this.id, lead.lead_id, callSid);
      await this._scheduler.markLeadInCall(lead.lead_id);
      await this._logger.log({
        leadId: lead.lead_id, campaignId: lead.campaign_id,
        pipelineId: this.id, tenantId: lead.tenant_id,
        eventType: 'CALL_INITIATED', message: `Call initiated sid=${callSid}`,
        metadata: { call_sid: callSid, mode: DIALER_MODE },
      });
    } catch (e) {
      log.warn(`[Pipeline:${this.name}] openCallRecords error:`, e.message);
    }
  }

  // ── Synchronous post-call bookkeeping — awaited before lock is released ──
  async _handleCallEnd(callSid, lead, result, durationS, attemptId) {
    const endedAt    = result.endedAt    || new Date().toISOString();
    const finalDurS  = result.durationS  || durationS;
    const disposition = result.disposition;

    // Update call_attempts to final status (critical — used by Phase 3 reconciliation)
    if (attemptId) {
      await pool.query(
        `UPDATE call_attempts
         SET status=$1, disposition=$2, duration_s=$3, ended_at=$4
         WHERE attempt_id=$5`,
        [disposition, disposition, finalDurS, endedAt, attemptId]
      );
    }

    await this._tracker.update(callSid, lead.tenant_id, {
      status:      disposition === 'COMPLETED' ? 'COMPLETED' : disposition,
      answeredAt:  result.answeredAt || null,
      endedAt,
      durationS:   finalDurS,
      disposition,
    });
    await this._tracker.close(callSid, lead.tenant_id);

    // Analytics write — best-effort (failure must not block lead status or retry)
    await pool.query(`
      INSERT INTO call_runtime_records
        (call_sid, lead_id, campaign_id, tenant_id, disposition,
         duration_seconds, started_at, ended_at, metadata)
      VALUES ($1,$2,$3,$4,$5,$6,
              (SELECT started_at FROM active_calls WHERE call_sid=$1),
              $7, $8)
      ON CONFLICT (call_sid) DO UPDATE SET
        disposition      = EXCLUDED.disposition,
        duration_seconds = EXCLUDED.duration_seconds,
        ended_at         = EXCLUDED.ended_at,
        metadata         = EXCLUDED.metadata
    `, [
      callSid, lead.lead_id, lead.campaign_id, lead.tenant_id,
      disposition, finalDurS, endedAt,
      JSON.stringify({ pipeline_id: this.id, retry_count: lead._retry_count || 0 }),
    ]).catch(e => log.warn(`[Pipeline:${this.name}] call_runtime_records write failed:`, e.message));

    await this._registry.setIdle(this.id);
    await this._registry.incrementStats(this.id, disposition, finalDurS);
    await this._scheduler.markLeadDone(lead.lead_id, disposition);

    // Phase 4: enqueue CRM sync task for this call's disposition
    if (lead.lead_id && lead.tenant_id) {
      pool.query(
        `INSERT INTO crm_sync_log (tenant_id, entity_type, entity_id, sync_status)
         VALUES ($1, 'disposition', $2::uuid, 'PENDING')
         ON CONFLICT (tenant_id, entity_type, entity_id) DO NOTHING`,
        [lead.tenant_id, lead.lead_id]
      ).catch(e => log.warn('[CRMSync] sync_log insert failed:', e.message));
    }

    await this._logger.log({
      leadId: lead.lead_id, campaignId: lead.campaign_id,
      pipelineId: this.id, tenantId: lead.tenant_id,
      eventType: 'CALL_COMPLETED',
      status: disposition === 'COMPLETED' ? 'SUCCESS' : 'FAILURE',
      message: `Call ended disposition=${disposition} duration=${finalDurS}s`,
      metadata: { call_sid: callSid, disposition, duration_s: finalDurS },
    });

    // Schedule retry if disposition warrants it
    if (['NO_ANSWER', 'BUSY', 'FAILED'].includes(disposition)) {
      await this._scheduleRetry(lead, disposition);
    }
  }

  async _handleCallError(callSid, lead, err, attemptId) {
    if (attemptId) {
      await pool.query(
        `UPDATE call_attempts SET status='FAILED', ended_at=NOW(), error_message=$1 WHERE attempt_id=$2`,
        [err.message, attemptId]
      ).catch(dbErr => log.warn('[call_attempts] failed to mark FAILED:', dbErr.message));
    }
    await this._tracker.update(callSid, lead.tenant_id, {
      status: 'FAILED', endedAt: new Date().toISOString(), disposition: 'FAILED',
    });
    await this._tracker.close(callSid, lead.tenant_id);
    await this._registry.setIdle(this.id);
    await this._scheduler.markLeadDone(lead.lead_id, 'FAILED');
    await this._logger.log({
      leadId: lead.lead_id, campaignId: lead.campaign_id,
      pipelineId: this.id, tenantId: lead.tenant_id,
      eventType: 'CALL_ERROR', status: 'FAILURE',
      message: err.message,
      metadata: { call_sid: callSid, error: err.message },
    });
    await this._scheduleRetry(lead, 'FAILED');
  }

  async _scheduleRetry(lead, disposition) {
    const attempts = (lead._retry_count || 0) + 1;
    if (attempts > MAX_RETRY_ATTEMPTS) {
      log.debug(`[Pipeline:${this.name}] Max retries reached for lead ${lead.lead_id}`);
      return;
    }
    // Disposition-specific backoff: BUSY 30/120/300s, NO_ANSWER 60/300/900s, FAILED 300/900/1800s
    const schedule = RETRY_DELAY_BY_DISPOSITION[disposition] || RETRY_DELAY_S;
    const delaySec = schedule[Math.min(attempts - 1, schedule.length - 1)];
    const retryAt  = Date.now() + delaySec * 1000;
    const retryPayload = {
      ...lead,
      _retry_count:      attempts,
      _retry_after:      retryAt,
      _last_disposition: disposition,
    };
    await redis.zadd(K.retryQueue(lead.tenant_id), retryAt, JSON.stringify(retryPayload));
    log.info(`[Pipeline:${this.name}] Retry scheduled lead=${lead.lead_id} attempt=${attempts} delay=${delaySec}s disposition=${disposition}`);
  }

  async _requeueDelayed(lead, reason) {
    const retryAt = Date.now() + SCHEDULE_REQUEUE_DELAY_S * 1000;
    const payload = { ...lead, _schedule_blocked: reason, _retry_after: retryAt };
    await redis.zadd(K.retryQueue(lead.tenant_id), retryAt, JSON.stringify(payload));
    await this._logger.log({
      leadId: lead.lead_id, campaignId: lead.campaign_id,
      pipelineId: this.id, tenantId: lead.tenant_id,
      eventType: 'CALL_DEFERRED', message: `Schedule blocked: ${reason}`,
      metadata: { reason, retry_after: new Date(retryAt).toISOString() },
    }).catch(() => {});
  }
}


// ─── Telephony Config Cache ──────────────────────────────────────────────────
// Caches per-tenant caller IDs from tenant_telephony_config table.
// Refreshes every 5 minutes per tenant. Falls back to global env-var defaults.

class TelephonyConfigCache {
  constructor(dbPool, redisClient) {
    this._pool       = dbPool;
    this._redis      = redisClient;
    this._cache      = new Map(); // tenantId → { provider → first_number }
    this._allNums    = new Map(); // tenantId → { provider → [number, ...] }
    this._loadedAt   = new Map(); // tenantId → timestamp
    this._TTL        = 5 * 60 * 1000;
  }

  async getCallerNumber(tenantId, provider) {
    const now = Date.now();
    if (!this._loadedAt.get(tenantId) || now - this._loadedAt.get(tenantId) > this._TTL) {
      await this._refresh(tenantId);
    }
    return (this._cache.get(tenantId) || {})[provider] || null;
  }

  async _refresh(tenantId) {
    try {
      const { rows } = await this._pool.query(
        `SELECT provider, from_number FROM tenant_telephony_config
         WHERE tenant_id=$1 AND is_active=TRUE ORDER BY created_at ASC`,
        [tenantId]
      );
      const cfg = {};  // provider → first from_number
      const all = {};  // provider → [from_number, ...]
      for (const r of rows) {
        if (!cfg[r.provider]) cfg[r.provider] = r.from_number;
        all[r.provider] = all[r.provider] || [];
        all[r.provider].push(r.from_number);
      }
      this._cache.set(tenantId, cfg);
      this._allNums.set(tenantId, all);
      this._loadedAt.set(tenantId, Date.now());
    } catch (e) {
      log.debug(`[TelephonyConfigCache] Refresh skipped for tenant=${tenantId}: ${e.message}`);
    }
  }

  // Round-robin selection when multiple numbers are configured for a provider
  async getRotatedCallerNumber(tenantId, provider) {
    const now = Date.now();
    if (!this._loadedAt.get(tenantId) || now - this._loadedAt.get(tenantId) > this._TTL) {
      await this._refresh(tenantId);
    }
    const nums = (this._allNums.get(tenantId) || {})[provider] || [];
    if (!nums.length) return null;
    if (nums.length === 1) return nums[0];
    // INCR gives a monotonically increasing counter — mod for round-robin
    const idx = await this._redis.incr(K.fromNumberIdx(provider, tenantId)).catch(() => 0);
    return nums[idx % nums.length];
  }
}

// ─── DNC Checker ─────────────────────────────────────────────────────────────
// Checks phone numbers against the DNC registry before every call.
// Uses Redis as a write-through cache (1-hour TTL) to avoid DB hit per call.

class DNCChecker {
  constructor(dbPool, redisClient) {
    this._pool  = dbPool;
    this._redis = redisClient;
    this._TTL   = 3600; // 1 hour Redis cache TTL
  }

  // Returns true if the phone is blocked (DNC or global registry)
  async isBlocked(phone, tenantId) {
    const normalized = phone.replace(/\D/g, '').replace(/^91/, '').slice(-10);
    if (!normalized || normalized.length < 10) return false;

    const cacheKey = `voiceos:dnc:${normalized}:${tenantId}`;
    const globalKey = `voiceos:dnc:${normalized}:global`;

    // Check Redis cache first
    const [cached, globalCached] = await Promise.all([
      this._redis.get(cacheKey).catch(() => null),
      this._redis.get(globalKey).catch(() => null),
    ]);
    if (cached !== null) return cached === '1';
    if (globalCached !== null && globalCached === '1') return true;

    // DB lookup: global block OR tenant-specific block
    try {
      const { rows } = await this._pool.query(
        `SELECT 1 FROM dnc_numbers
         WHERE phone = $1
           AND (tenant_id IS NULL OR tenant_id = $2)
           AND (expires_at IS NULL OR expires_at > NOW())
         LIMIT 1`,
        [normalized, tenantId]
      );
      const blocked = rows.length > 0;
      // Cache the result
      await this._redis.setex(cacheKey, this._TTL, blocked ? '1' : '0').catch(() => {});
      return blocked;
    } catch (e) {
      // Table may not exist yet — fail open (allow call)
      log.debug(`[DNCChecker] DB check failed for phone=${normalized}: ${e.message}`);
      return false;
    }
  }

  // Explicitly add a number to DNC and invalidate cache
  async block(phone, tenantId, source = 'manual', reason = '') {
    const normalized = phone.replace(/\D/g, '').replace(/^91/, '').slice(-10);
    await this._pool.query(
      `INSERT INTO dnc_numbers (phone, tenant_id, source, reason)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (phone, tenant_id) DO UPDATE SET
         source = EXCLUDED.source, reason = EXCLUDED.reason, added_at = NOW()`,
      [normalized, tenantId || null, source, reason]
    );
    await this._redis.del(`voiceos:dnc:${normalized}:${tenantId}`).catch(() => {});
  }
}


// ─── Capacity Guard ────────────────────────────────────────────────────────────
// Tracks active call counts at system / tenant / campaign levels via Redis INCR.
// acquire() returns false if any limit is exceeded — lead is requeued with a short
// delay rather than dropped, so no calls are lost.

class CapacityGuard {
  constructor(redisClient) {
    this._redis = redisClient;
  }

  async acquire(lead, campaignMaxCalls, tenantMaxCalls) {
    const sysKey = K.activeSystem();
    const tenKey = K.activeTenant(lead.tenant_id);
    const camKey = K.activeCampaign(lead.campaign_id);

    const results = await this._redis.pipeline()
      .get(sysKey).get(tenKey).get(camKey).exec();
    const [sysCount, tenCount, camCount] = results.map(([, val]) => parseInt(val || '0'));

    if (sysCount >= MAX_SYSTEM_CONCURRENT_CALLS) {
      log.warn(`[CapacityGuard] System cap: ${sysCount}/${MAX_SYSTEM_CONCURRENT_CALLS}`);
      return false;
    }
    if (tenantMaxCalls > 0 && tenCount >= tenantMaxCalls) {
      log.warn(`[CapacityGuard] Tenant cap: tenant=${lead.tenant_id} ${tenCount}/${tenantMaxCalls}`);
      return false;
    }
    if (campaignMaxCalls > 0 && camCount >= campaignMaxCalls) {
      log.warn(`[CapacityGuard] Campaign cap: campaign=${lead.campaign_id} ${camCount}/${campaignMaxCalls}`);
      return false;
    }

    const expiry = MAX_CALL_DURATION_S + 60;
    await this._redis.pipeline()
      .incr(sysKey).expire(sysKey, expiry)
      .incr(tenKey).expire(tenKey, expiry)
      .incr(camKey).expire(camKey, expiry)
      .exec();
    return true;
  }

  async release(lead) {
    try {
      await this._redis.pipeline()
        .decr(K.activeSystem())
        .decr(K.activeTenant(lead.tenant_id))
        .decr(K.activeCampaign(lead.campaign_id))
        .exec();
    } catch (e) {
      log.warn('[CapacityGuard] release error:', e.message);
    }
  }
}

// ─── Pacing Enforcer ──────────────────────────────────────────────────────────
// Sliding-window rate limiter per campaign using a Redis sorted set.
// Each member is a unique timestamp string; score is also the timestamp.
// Window = last 60 seconds; enforces calls_per_minute.

class PacingEnforcer {
  constructor(redisClient) {
    this._redis = redisClient;
  }

  async canCall(campaignId, callsPerMinute) {
    if (!callsPerMinute || callsPerMinute <= 0) return true;
    const key = K.pacingZset(campaignId);
    const now  = Date.now();
    const winStart = now - 60_000;

    // Remove stale, add current, count — all in one pipeline
    const pipe = this._redis.pipeline();
    pipe.zremrangebyscore(key, '-inf', winStart);
    pipe.zadd(key, now, `${now}:${Math.random().toString(36).slice(2)}`);
    pipe.zcard(key);
    pipe.expire(key, 90);
    const res = await pipe.exec();
    const count = res[2][1]; // zcard result

    if (count > callsPerMinute) {
      // Back out the entry we just added — we're not making the call
      await this._redis.zremrangebyscore(key, now, now + 1).catch(() => {});
      log.debug(`[PacingEnforcer] Pacing block campaign=${campaignId}: ${count}/${callsPerMinute} cpm`);
      return false;
    }
    return true;
  }
}

// ─── Lead Scheduler Job ────────────────────────────────────────────────────────
// Scans the database every 10 seconds for PENDING leads in active campaigns,
// claims them atomically (FOR UPDATE SKIP LOCKED) by flipping queue_status to
// QUEUED, then pushes them onto the appropriate Redis priority queue.
// This decouples lead ingestion from the dialer — leads appear in the DB first,
// the scheduler drains them into Redis for the worker to consume.

class LeadSchedulerJob {
  constructor(dbPool, redisClient) {
    this._pool   = dbPool;
    this._redis  = redisClient;
    this._timer  = null;
    this._active = false;
  }

  start() {
    this._active = true;
    this._timer  = setInterval(
      () => this._scan().catch(e => log.warn('[LeadScheduler] scan error:', e.message)),
      10_000
    );
    log.info('[LeadScheduler] Started — scanning PENDING leads every 10s');
    // Run immediately on startup
    this._scan().catch(e => log.warn('[LeadScheduler] initial scan error:', e.message));
  }

  stop() {
    this._active = false;
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  }

  async _scan() {
    if (!this._active) return;
    try {
      // Claim up to 100 PENDING leads atomically — FOR UPDATE SKIP LOCKED prevents
      // double-claiming when multiple workers run simultaneously.
      const { rows } = await this._pool.query(`
        UPDATE leads l
           SET queue_status = 'QUEUED', updated_at = NOW()
          FROM campaigns c
         WHERE l.campaign_id = c.campaign_id
           AND l.queue_status = 'PENDING'
           AND c.status = 'ACTIVE'
           AND l.lead_id IN (
               SELECT l2.lead_id
                 FROM leads l2
                 JOIN campaigns c2 ON l2.campaign_id = c2.campaign_id
                WHERE l2.queue_status = 'PENDING'
                  AND c2.status = 'ACTIVE'
                ORDER BY l2.priority ASC, l2.created_at ASC
                LIMIT 100
                FOR UPDATE OF l2 SKIP LOCKED
               )
        RETURNING l.lead_id, l.phone, l.name, l.language,
                  l.campaign_id, l.tenant_id, l.priority,
                  l.metadata
      `);

      if (!rows.length) return;
      log.info(`[LeadScheduler] Claiming ${rows.length} leads`);

      for (const row of rows) {
        const priLabel = (row.priority || 2) <= 1 ? 'high'
                       : (row.priority || 2) >= 3 ? 'low'
                       : 'normal';
        const queueKey = K.pendingQueuePri(row.tenant_id, priLabel);
        const payload  = JSON.stringify({
          lead_id:     row.lead_id,
          phone:       row.phone,
          name:        row.name        || '',
          language:    row.language    || 'en',
          campaign_id: row.campaign_id,
          tenant_id:   row.tenant_id,
          pipeline_id: `camp-${row.campaign_id}`,
          priority:    row.priority    || 2,
          metadata:    row.metadata    || {},
        });
        await this._redis.lpush(queueKey, payload).catch(e =>
          log.warn(`[LeadScheduler] lpush failed lead=${row.lead_id}:`, e.message)
        );
      }
    } catch (e) {
      log.warn('[LeadScheduler] _scan error:', e.message);
    }
  }
}

// ─── Dialer Worker ───────────────────────────────────────────────────────────

class DialerWorker {
  constructor() {
    this._running    = false;
    this._pipelines  = new Map();  // pipeline_id → Pipeline
    this._tenantQueues    = [];     // flat list (legacy — kept for heartbeat/stats)
    this._tenantPriQueues = [];     // priority-ordered BRPOP list (high → normal → low)
    this._scheduler  = new ScheduleVerifier(pool);
    this._tracker    = new ActiveCallTracker(pool);
    this._registry   = new PipelineRegistry(pool);
    this._logger     = new EventLogger(pool);
    this._dialer          = DIALER_MODE === 'production' ? new MultiProviderDialer() : new CallSimulator();
    this._telConfig       = new TelephonyConfigCache(pool, redis);
    this._dnc             = new DNCChecker(pool, redis);
    this._capacityGuard   = new CapacityGuard(redis);
    this._pacingEnforcer  = new PacingEnforcer(redis);
    this._schedulerJob    = new LeadSchedulerJob(pool, redis);

    this._callsToday  = 0;
    this._callsMinute = [];       // timestamps for per-minute rate
    this._idleLogAt   = 0;
    this._heartbeatTimer = null;
    this._retryTimer     = null;
    this._callbackTimer  = null;
    this._reconciler     = new CrashReconciler(pool, redis);
  }

  async start() {
    log.info(`[Worker:${WORKER_ID}] Starting in ${DIALER_MODE} mode`);
    this._running = true;

    // Discover all tenant queues
    await this._refreshQueues();

    // Start heartbeat
    this._heartbeatTimer = setInterval(() => this._heartbeat(), HEARTBEAT_INTERVAL_MS);
    await this._heartbeat();

    // Start retry consumer (polls sorted set every 30s)
    this._retryTimer = setInterval(() => this._drainRetryQueues(), HEARTBEAT_INTERVAL_MS);

    // Start callback consumer
    this._callbackTimer = setInterval(() => this._drainCallbackQueues(), CALLBACK_POLL_MS);

    // Phase 3: Crash reconciliation — runs once on startup before accepting new leads
    await this._reconciler.reconcile();

    // W3: Lead scheduler job — polls DB for PENDING leads and enqueues them
    this._schedulerJob.start();

    // Start main consumer loop
    log.info(`[Worker:${WORKER_ID}] Consumer loop started — monitoring ${this._tenantQueues.length} queue(s)`);
    await this._consumerLoop();
  }

  async stop() {
    log.info(`[Worker:${WORKER_ID}] Shutdown initiated — draining active calls`);
    this._running = false;

    clearInterval(this._heartbeatTimer);
    clearInterval(this._retryTimer);
    clearInterval(this._callbackTimer);
    this._schedulerJob.stop();

    // Signal all pipelines to stop accepting new leads
    for (const p of this._pipelines.values()) await p.shutdown();

    // Wait for active calls to complete (up to 120s)
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      const busyPipelines = [...this._pipelines.values()].filter(p => p.status === 'BUSY');
      if (busyPipelines.length === 0) break;
      log.info(`[Worker:${WORKER_ID}] Waiting for ${busyPipelines.length} active call(s)...`);
      await sleep(2000);
    }

    await this._updateWorkerStatus('STOPPED');
    log.info(`[Worker:${WORKER_ID}] Shutdown complete`);
  }

  // ── Main consumer loop ────────────────────────────────────────────────────
  async _consumerLoop() {
    let refreshAt = Date.now() + 60_000;

    while (this._running) {
      // Periodically refresh tenant queue list
      if (Date.now() >= refreshAt) {
        await this._refreshQueues();
        refreshAt = Date.now() + 60_000;
      }

      if (this._tenantPriQueues.length === 0) {
        // No queues yet — wait and retry
        if (Date.now() - this._idleLogAt > IDLE_LOG_INTERVAL_MS) {
          log.info(`[Worker:${WORKER_ID}] No active queues — waiting for leads`);
          this._idleLogAt = Date.now();
        }
        await sleep(5000);
        await this._refreshQueues();
        continue;
      }

      try {
        // Priority BRPOP: try high → normal → low queues in order.
        // _tenantPriQueues is ordered: all-high keys first, then normal, then low.
        const result = await redisSub.brpop(...this._tenantPriQueues, QUEUE_POLL_TIMEOUT_S);
        if (!result) continue; // timeout — loop continues

        const [queueKey, payload] = result;
        const lead = JSON.parse(payload);

        log.info(`[Worker:${WORKER_ID}] Dequeued lead=${lead.lead_id} phone=${lead.phone} pipeline=${lead.pipeline_id}`);

        // Track throughput
        this._callsMinute.push(Date.now());
        this._callsToday++;

        await this._routeToPipeline(lead);
      } catch (e) {
        if (this._running) log.error(`[Worker:${WORKER_ID}] Consumer error:`, e.message);
        await sleep(1000);
      }
    }
  }

  // ── Route a lead to its assigned pipeline ─────────────────────────────────
  async _routeToPipeline(lead) {
    const pid = lead.pipeline_id || `default-${lead.tenant_id}`;

    let p = this._pipelines.get(pid);
    if (!p) {
      p = new Pipeline({
        id:              pid,
        campaignId:      lead.campaign_id,
        tenantId:        lead.tenant_id,
        name:            `Pipeline-${pid.slice(0, 8)}`,
        registry:        this._registry,
        tracker:         this._tracker,
        scheduler:       this._scheduler,
        eventLogger:     this._logger,
        dialer:          this._dialer,
        dncChecker:      this._dnc,
        capacityGuard:   this._capacityGuard,
        pacingEnforcer:  this._pacingEnforcer,
      });
      try {
        await p.start();
      } catch (e) {
        log.error(`[Worker:${WORKER_ID}] Pipeline ${pid} failed to start: ${e.message}`);
        throw e;
      }
      this._pipelines.set(pid, p);
    } else if (!p._running) {
      log.warn(`[Worker:${WORKER_ID}] Pipeline ${pid} was dead — restarting`);
      try {
        await p.start();
      } catch (e) {
        log.error(`[Worker:${WORKER_ID}] Pipeline ${pid} restart failed: ${e.message}`);
        throw e;
      }
    }

    // Enrich lead with per-tenant caller ID — round-robin across configured numbers
    try {
      const provider  = (process.env.TELEPHONY_PROVIDER_ORDER || 'twilio').split(',')[0].trim();
      const callerNum = await this._telConfig.getRotatedCallerNumber(lead.tenant_id, provider);
      if (callerNum) lead._caller_id = callerNum;
    } catch (e) {
      log.debug(`[Worker] telConfig lookup failed: ${e.message}`);
    }
    p.enqueue(lead);
  }

  // ── Retry queue consumer ──────────────────────────────────────────────────
  // Reads from sorted sets (score = scheduled_ms), processes overdue entries
  async _drainRetryQueues() {
    if (!this._running) return;
    try {
      const keys = await redis.keys('voiceos:retry_calls:*');
      const now  = Date.now();
      for (const key of keys) {
        // ZRANGEBYSCORE: members with score <= now (overdue)
        const items = await redis.zrangebyscore(key, 0, now, 'LIMIT', 0, 10);
        for (const item of items) {
          const removed = await redis.zrem(key, item);
          if (!removed) continue; // Another worker got it
          const lead = JSON.parse(item);
          log.info(`[Worker:${WORKER_ID}] Retrying lead=${lead.lead_id} attempt=${lead._retry_count}`);
          await this._routeToPipeline(lead);
        }
      }
    } catch (e) {
      log.warn('[Worker] drainRetryQueues error:', e.message);
    }
  }

  // ── Callback queue consumer ───────────────────────────────────────────────
  async _drainCallbackQueues() {
    if (!this._running) return;
    try {
      const keys = await redis.keys('voiceos:callback_calls:*');
      const now  = Date.now();
      for (const key of keys) {
        const items = await redis.zrangebyscore(key, 0, now, 'LIMIT', 0, 5);
        for (const item of items) {
          const removed = await redis.zrem(key, item);
          if (!removed) continue;
          const lead = JSON.parse(item);
          log.info(`[Worker:${WORKER_ID}] Processing callback for lead=${lead.lead_id}`);
          await this._routeToPipeline(lead);
        }
      }
    } catch (e) {
      log.warn('[Worker] drainCallbackQueues error:', e.message);
    }
  }

  // ── Refresh tenant queue list ─────────────────────────────────────────────
  async _refreshQueues() {
    try {
      // Collect priority queues (may not exist yet — BRPOP skips missing keys)
      const allKeys = await redis.keys('voiceos:pending_calls:*');
      // Separate legacy flat keys from priority-suffixed keys
      const high   = allKeys.filter(k => k.endsWith(':high'));
      const normal = allKeys.filter(k => k.endsWith(':normal'));
      const low    = allKeys.filter(k => k.endsWith(':low'));
      const legacy = allKeys.filter(k => !k.endsWith(':high') && !k.endsWith(':normal') && !k.endsWith(':low'));

      // BRPOP order: high first, then normal, then low, then legacy (migration compat)
      this._tenantPriQueues = [...high, ...normal, ...low, ...legacy];
      this._tenantQueues    = allKeys; // heartbeat/stats still uses the flat list

      if (allKeys.length > 0) {
        log.debug(`[Worker] Queues — high:${high.length} normal:${normal.length} low:${low.length} legacy:${legacy.length}`);
      }
    } catch (e) {
      log.warn('[Worker] refreshQueues error:', e.message);
    }
  }

  // ── Worker heartbeat ──────────────────────────────────────────────────────
  async _heartbeat() {
    try {
      const now = Date.now();
      const oneMinuteAgo = now - 60_000;
      this._callsMinute = this._callsMinute.filter(t => t > oneMinuteAgo);
      const cpm = this._callsMinute.length;

      const busyPipelines = [...this._pipelines.values()].filter(p => p.status === 'BUSY').length;
      const idlePipelines = this._pipelines.size - busyPipelines;
      const queueLengths  = await this._getQueueLengths();

      await pool.query(
        `INSERT INTO worker_heartbeats
         (worker_id, status, active_calls, idle_pipelines, busy_pipelines,
          queues_monitored, calls_today, calls_per_minute, last_heartbeat, metadata)
         VALUES ($1,'RUNNING',$2,$3,$4,$5::text[],$6,$7,now(),$8::jsonb)
         ON CONFLICT (worker_id) DO UPDATE SET
           status='RUNNING', active_calls=$2, idle_pipelines=$3, busy_pipelines=$4,
           queues_monitored=$5::text[], calls_today=$6, calls_per_minute=$7,
           last_heartbeat=now(), metadata=$8::jsonb`,
        [WORKER_ID, busyPipelines, idlePipelines, busyPipelines,
         this._tenantQueues, this._callsToday, cpm,
         JSON.stringify({ mode: DIALER_MODE, queues: queueLengths, pid: process.pid })]
      );

      // TTL key in Redis so BFF can quickly detect if worker is alive
      await redis.set(K.workerHeartbeat(WORKER_ID), '1', 'EX', 90);

      log.info(`[Worker:${WORKER_ID}] Heartbeat — pipelines: ${this._pipelines.size} (busy=${busyPipelines}) calls_today=${this._callsToday} cpm=${cpm}`);
    } catch (e) {
      log.warn('[Worker] heartbeat error:', e.message);
    }
  }

  async _updateWorkerStatus(status) {
    try {
      await pool.query(
        `UPDATE worker_heartbeats SET status=$2, last_heartbeat=now() WHERE worker_id=$1`,
        [WORKER_ID, status]
      );
    } catch {}
  }

  async _getQueueLengths() {
    const lengths = {};
    try {
      for (const key of this._tenantQueues) {
        lengths[key] = await redis.llen(key);
      }
    } catch {}
    return lengths;
  }

  // ── Status snapshot (for BFF /dialer/status endpoint) ────────────────────
  status() {
    const pipelines = [...this._pipelines.values()].map(p => ({
      id:         p.id,
      name:       p.name,
      status:     p.status,
      buffer:     p.bufferSize,
    }));
    return {
      worker_id:       WORKER_ID,
      mode:            DIALER_MODE,
      running:         this._running,
      pipelines,
      calls_today:     this._callsToday,
      queues:          this._tenantQueues,
    };
  }
}

// ─── Singleton worker instance ────────────────────────────────────────────────

const worker = new DialerWorker();

// ─── Graceful shutdown ────────────────────────────────────────────────────────

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  log.info(`[Worker] Received ${signal} — shutting down gracefully`);
  await worker.stop();
  await pool.end();
  redis.disconnect();
  redisSub.disconnect();
  process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));
process.on('uncaughtException', (e) => {
  log.error('[Worker] Uncaught exception:', e.message, e.stack);
  // Don't crash — log and continue
});
process.on('unhandledRejection', (reason) => {
  log.warn('[Worker] Unhandled rejection:', reason);
});

// ─── Start ────────────────────────────────────────────────────────────────────

if (require.main === module) {
  log.info(`VoiceOS Dialer Worker ${WORKER_ID} — mode=${DIALER_MODE}`);
  worker.start().catch(e => {
    log.error('[Worker] Fatal startup error:', e.message);
    process.exit(1);
  });
}

module.exports = { ScheduleVerifier, PipelineRegistry, ActiveCallTracker, CrashReconciler, Pipeline, DialerWorker };
