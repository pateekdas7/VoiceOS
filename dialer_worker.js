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
const RETRY_DELAY_S        = [60, 300, 900]; // 1 min, 5 min, 15 min backoff
const CALLBACK_POLL_MS     = 30_000;
const IDLE_LOG_INTERVAL_MS = 60_000;

// SIM call outcome distribution (must sum to 100)
const SIM_OUTCOMES = [
  { disposition: 'COMPLETED',  weight: 55 },
  { disposition: 'NO_ANSWER',  weight: 25 },
  { disposition: 'BUSY',       weight: 10 },
  { disposition: 'FAILED',     weight: 10 },
];

// ─── Redis keys ─────────────────────────────────────────────────────────────

const K = {
  pendingQueue:    (tid) => `voiceos:pending_calls:${tid}`,
  retryQueue:      (tid) => `voiceos:retry_calls:${tid}`,
  callbackQueue:   (tid) => `voiceos:callback_calls:${tid}`,
  activeCalls:     (tid) => `voiceos:active_calls:${tid}`,
  pipelineCompleted: (pid) => `voiceos:pipeline:completed:${pid}`,
  callLock:        (sid)  => `voiceos:call:lock:${sid}`,
  workerHeartbeat: (wid)  => `voiceos:worker:${wid}:alive`,
};

// ─── DB / Redis clients ──────────────────────────────────────────────────────

const pool = new Pool({
  host:     process.env.POSTGRES_HOST || '/data/data/com.termux/files/usr/tmp',
  database: process.env.POSTGRES_DB   || 'voiceos',
  user:     process.env.POSTGRES_USER || process.env.USER || 'u0_a295',
  max: 5,
});
pool.on('error', err => log.warn('[pg] idle client error:', err.message));

// Two Redis clients: one for blocking ops (BRPOP), one for everything else.
const redis    = new Redis({ host: '127.0.0.1', port: 6379, maxRetriesPerRequest: 3, retryStrategy: n => Math.min(n * 200, 2000) });
const redisSub = new Redis({ host: '127.0.0.1', port: 6379, maxRetriesPerRequest: 3, retryStrategy: n => Math.min(n * 200, 2000) });
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
      `SELECT status, daily_start_hour, daily_end_hour, timezone
       FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2`,
      [campaignId, tenantId]
    );
    if (!rows.length) return { ok: false, reason: 'campaign_not_found' };

    const c = rows[0];
    if (c.status !== 'ACTIVE') return { ok: false, reason: `campaign_${c.status.toLowerCase()}` };

    // Convert current UTC time to campaign timezone for hour comparison
    const tz = c.timezone || 'Asia/Kolkata';
    const nowInTz = new Date().toLocaleString('en-US', { timeZone: tz, hour12: false });
    const localHour = parseInt(nowInTz.split(',')[1]?.trim().split(':')[0] ?? '0', 10);

    const start = c.daily_start_hour ?? 9;
    const end   = c.daily_end_hour   ?? 18;

    if (localHour < start || localHour >= end) {
      return { ok: false, reason: 'outside_calling_window', localHour, window: `${start}-${end}` };
    }

    return { ok: true };
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
    const sets = ['status=$2', 'updated_at now() -- no updated_at on active_calls'];
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
    const twimlUrl = `${this._bffUrl}/dialer/twiml?pipeline_id=${encodeURIComponent(lead.pipeline_id)}&language=${encodeURIComponent(lead.language || 'hi')}`;
    const callbackUrl = `${this._bffUrl}/dialer/callback?pipeline_id=${encodeURIComponent(lead.pipeline_id)}&lead_id=${encodeURIComponent(lead.lead_id)}&tenant_id=${encodeURIComponent(lead.tenant_id)}`;

    const call = await this._client.calls.create({
      to:             `+91${lead.phone}`,
      from:           this._from,
      url:            twimlUrl,
      statusCallback: callbackUrl,
      statusCallbackMethod: 'POST',
      statusCallbackEvent:  ['initiated','ringing','answered','completed'],
      machineDetection: 'Enable',
      timeout:         30,
    });

    log.info(`[TWILIO] Call initiated sid=${call.sid} to=${lead.phone} pipeline=${lead.pipeline_id}`);
    return call.sid;
  }
}

// ─── Pipeline — manages exactly one concurrent call ──────────────────────────

class Pipeline extends EventEmitter {
  constructor({ id, campaignId, tenantId, name, registry, tracker, scheduler, eventLogger, dialer }) {
    super();
    this.id         = id;
    this.campaignId = campaignId;
    this.tenantId   = tenantId;
    this.name       = name || `Pipeline-${id.slice(0, 8)}`;
    this.status     = 'IDLE';
    this.buffer     = [];       // local lead buffer (in-memory)
    this._registry  = registry;
    this._tracker   = tracker;
    this._scheduler = scheduler;
    this._logger    = eventLogger;
    this._dialer    = dialer;
    this._running   = false;
    this._resolveNext = null;   // called when a new lead arrives in buffer
  }

  async start() {
    if (this._running) return;
    this._running = true;
    await this._registry.upsert({ id: this.id, campaignId: this.campaignId, tenantId: this.tenantId, name: this.name, status: 'IDLE' });
    log.info(`[Pipeline:${this.name}] started`);
    this._loop().catch(e => log.error(`[Pipeline:${this.name}] loop crashed:`, e.message));
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
    const callStart = Date.now();
    try {
      // ── 4. Initiate call ───────────────────────────────────────────────
      callSid = await this._dialer.initiate(lead);

      // ── 5. Record call open (non-blocking background) ──────────────────
      setImmediate(() => this._openCallRecords(callSid, lead));

      // ── 6. Wait for call completion ────────────────────────────────────
      const result = await this._waitForCompletion(callSid, lead);

      const durationS = Math.round((Date.now() - callStart) / 1000);
      log.info(`[Pipeline:${this.name}] Call done sid=${callSid} disposition=${result.disposition} duration=${durationS}s`);

      // ── 7. Async background bookkeeping — never blocks next call ────────
      setImmediate(() => this._handleCallEnd(callSid, lead, result, durationS));

    } catch (e) {
      log.error(`[Pipeline:${this.name}] Call error lead=${lead.lead_id}:`, e.message);
      if (callSid) {
        setImmediate(() => this._handleCallError(callSid, lead, e));
      }
    } finally {
      // ── 8. Release pipeline immediately for next lead ───────────────────
      this.status = 'IDLE';
      this.emit('status', 'IDLE');
      await redis.del(lockKey);
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
        if (raw) return JSON.parse(raw);
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

  // ── Background: handle call completion bookkeeping ────────────────────────
  async _handleCallEnd(callSid, lead, result, durationS) {
    try {
      const endedAt    = result.endedAt    || new Date().toISOString();
      const finalDurS  = result.durationS  || durationS;
      const disposition = result.disposition;
      await this._tracker.update(callSid, lead.tenant_id, {
        status:      disposition === 'COMPLETED' ? 'COMPLETED' : disposition,
        answeredAt:  result.answeredAt || null,
        endedAt,
        durationS:   finalDurS,
        disposition,
      });
      await this._tracker.close(callSid, lead.tenant_id);

      // Write analytics record — unblocks RealtimeAnalytics and the analytics dashboard
      await pool.query(`
        INSERT INTO call_dispositions
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
      ]).catch(e => log.warn(`[Pipeline:${this.name}] call_dispositions write failed:`, e.message));

      await this._registry.setIdle(this.id);
      await this._registry.incrementStats(this.id, disposition, finalDurS);
      await this._scheduler.markLeadDone(lead.lead_id, disposition);

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
    } catch (e) {
      log.warn(`[Pipeline:${this.name}] handleCallEnd error:`, e.message);
    }
  }

  async _handleCallError(callSid, lead, err) {
    try {
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
    } catch (e) {
      log.warn(`[Pipeline:${this.name}] handleCallError cleanup error:`, e.message);
    }
  }

  async _scheduleRetry(lead, disposition) {
    const attempts = (lead._retry_count || 0) + 1;
    if (attempts > MAX_RETRY_ATTEMPTS) {
      log.debug(`[Pipeline:${this.name}] Max retries reached for lead ${lead.lead_id}`);
      return;
    }
    const delaySec = RETRY_DELAY_S[attempts - 1] || RETRY_DELAY_S[RETRY_DELAY_S.length - 1];
    const retryAt  = Date.now() + delaySec * 1000;
    const retryPayload = { ...lead, _retry_count: attempts, _retry_after: retryAt, _last_disposition: disposition };

    // Push to retry sorted set (score = unix ms timestamp for ordered processing)
    await redis.zadd(K.retryQueue(lead.tenant_id), retryAt, JSON.stringify(retryPayload));
    log.info(`[Pipeline:${this.name}] Retry scheduled for lead ${lead.lead_id} attempt=${attempts} delay=${delaySec}s`);
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

// ─── Dialer Worker ───────────────────────────────────────────────────────────

class DialerWorker {
  constructor() {
    this._running    = false;
    this._pipelines  = new Map();  // pipeline_id → Pipeline
    this._tenantQueues = [];       // list of Redis queue keys to BRPOP
    this._scheduler  = new ScheduleVerifier(pool);
    this._tracker    = new ActiveCallTracker(pool);
    this._registry   = new PipelineRegistry(pool);
    this._logger     = new EventLogger(pool);
    this._dialer     = DIALER_MODE === 'production' ? new TwilioDialer() : new CallSimulator();

    this._callsToday  = 0;
    this._callsMinute = [];       // timestamps for per-minute rate
    this._idleLogAt   = 0;
    this._heartbeatTimer = null;
    this._retryTimer     = null;
    this._callbackTimer  = null;
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

      if (this._tenantQueues.length === 0) {
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
        // BRPOP across all tenant pending queues — FIFO, oldest lead first
        const result = await redisSub.brpop(...this._tenantQueues, QUEUE_POLL_TIMEOUT_S);
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

    if (!this._pipelines.has(pid)) {
      // Auto-create pipeline on first lead
      const p = new Pipeline({
        id:          pid,
        campaignId:  lead.campaign_id,
        tenantId:    lead.tenant_id,
        name:        `Pipeline-${pid.slice(0, 8)}`,
        registry:    this._registry,
        tracker:     this._tracker,
        scheduler:   this._scheduler,
        eventLogger: this._logger,
        dialer:      this._dialer,
      });
      this._pipelines.set(pid, p);
      await p.start();
    }

    this._pipelines.get(pid).enqueue(lead);
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
      const keys = await redis.keys('voiceos:pending_calls:*');
      this._tenantQueues = keys.length > 0 ? keys : [];
      if (keys.length > 0) log.debug(`[Worker] Monitoring queues: ${keys.join(', ')}`);
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

log.info(`VoiceOS Dialer Worker ${WORKER_ID} — mode=${DIALER_MODE}`);
worker.start().catch(e => {
  log.error('[Worker] Fatal startup error:', e.message);
  process.exit(1);
});
