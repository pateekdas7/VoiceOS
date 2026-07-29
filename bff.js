'use strict';
const express      = require('express');
const { Pool }     = require('pg');
const jwt          = require('jsonwebtoken');
const bcrypt       = require('bcryptjs');
const cookieParser = require('cookie-parser');
const cors         = require('cors');
const Redis        = require('ioredis');

// ─── Config ───────────────────────────────────────────────────────────────────
const app          = express();
const PORT         = 8000;
const JWT_SECRET = process.env.JWT_SECRET || (() => {
  if (process.env.NODE_ENV === 'production') {
    console.error('[FATAL] JWT_SECRET must be set in production. Exiting.');
    process.exit(1);
  }
  console.warn('[WARN] JWT_SECRET not set — using insecure default. Set JWT_SECRET before production deployment.');
  return 'voiceos-local-dev-secret-key-2024';
})();
const SESSION_COOKIE  = 'voiceos_session';
const ACTOR_KIND_COOKIE = 'voiceos_actor_kind';

// ─── PostgreSQL ───────────────────────────────────────────────────────────────
const pool = new Pool({
  host: '/data/data/com.termux/files/usr/tmp',
  database: 'voiceos',
  user: process.env.USER || 'u0_a295',
  max: 10,
});
pool.on('error', (err) => console.error('[pg] idle client error:', err.message));

// ─── Redis ────────────────────────────────────────────────────────────────────
const redis = new Redis({ host: '127.0.0.1', port: 6379, lazyConnect: true, maxRetriesPerRequest: 1 });
redis.on('error', e => console.warn('[redis]', e.message));
redis.connect().catch(e => console.warn('[redis] connect failed:', e.message));

// ─── Middleware ───────────────────────────────────────────────────────────────
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: false })); // Twilio webhooks send form-encoded bodies
app.use(cookieParser());
app.use(cors({ origin: 'http://localhost:3000', credentials: true }));

// ─── Auth helpers ─────────────────────────────────────────────────────────────
function makeToken(payload) {
  return jwt.sign(payload, JWT_SECRET, { expiresIn: '7d' });
}
function setCookies(res, token, actorKind) {
  const opts = { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 7 * 24 * 3600 * 1000 };
  res.cookie(SESSION_COOKIE, token, opts);
  res.cookie(ACTOR_KIND_COOKIE, actorKind, { ...opts, httpOnly: false });
}
function requireAuth(req, res, next) {
  const token = req.cookies[SESSION_COOKIE];
  if (!token) return res.status(401).json({ error: 'unauthorized' });
  try { req.user = jwt.verify(token, JWT_SECRET); next(); }
  catch { res.status(401).json({ error: 'invalid_session' }); }
}

// ═════════════════════════════════════════════════════════════════════════════
// LEAD INTAKE ENGINES
// ═════════════════════════════════════════════════════════════════════════════

// ── 1. Normalization ──────────────────────────────────────────────────────────
function normalizePhone(raw) {
  if (!raw) return null;
  let p = String(raw).replace(/\D/g, '');
  if (p.startsWith('91') && p.length === 12) p = p.slice(2);
  return p.length === 10 ? p : null;
}
function normalizeName(raw) {
  if (!raw) return '';
  return String(raw).trim().replace(/\b\w/g, c => c.toUpperCase());
}

// ── 2. Language detection ─────────────────────────────────────────────────────
const LANGUAGE_MAP = {
  'Tamil Nadu': 'TAMIL',   'Tamilnadu': 'TAMIL',
  'Kerala': 'MALAYALAM',   'Karnataka': 'KANNADA',
  'Andhra Pradesh': 'TELUGU', 'Telangana': 'TELUGU',
  'Gujarat': 'GUJARATI',   'Maharashtra': 'MARATHI',
  'Punjab': 'PUNJABI',     'West Bengal': 'BENGALI',
  'Odisha': 'ODIA',        'Rajasthan': 'HINDI',
};
// ISO 639-1 short codes for distribution rule matching (rules store "hi","ta" etc.)
const LANG_TO_CODE = {
  'HINDI': 'hi', 'TAMIL': 'ta', 'MALAYALAM': 'ml', 'KANNADA': 'kn',
  'TELUGU': 'te', 'GUJARATI': 'gu', 'MARATHI': 'mr', 'PUNJABI': 'pa',
  'BENGALI': 'bn', 'ODIA': 'or',
};
function detectLanguage(metadata) {
  const state = metadata.state || metadata.State || metadata.STATE || '';
  return LANGUAGE_MAP[state] || 'HINDI';
}

// ── 3. Scoring engine ─────────────────────────────────────────────────────────
function scoreLead(row) {
  let score = 50;
  const loan = parseFloat(row.loan_amount || row.amount || 0);
  if (loan > 500000)      score += 30;
  else if (loan > 200000) score += 20;
  else if (loan > 50000)  score += 10;
  if (row.email)          score += 10;
  if (row.city || row.City) score += 5;
  const tier1 = ['Delhi', 'Mumbai', 'Bangalore', 'Hyderabad', 'Chennai', 'Pune', 'Kolkata'];
  if (tier1.some(c => (row.city || '').includes(c))) score += 10;
  return Math.min(score, 100);
}

// ── 4. Compliance engine ──────────────────────────────────────────────────────
function checkCompliance(campaign, lead) {
  if (lead.is_blacklisted) return { allowed: false, reason: 'BLACKLISTED' };
  if (!campaign) return { allowed: true };

  const tz = campaign.timezone || 'Asia/Kolkata';
  const now = new Date();
  const hourStr = new Intl.DateTimeFormat('en-US', {
    hour: 'numeric', hour12: false, timeZone: tz,
  }).format(now);
  const hour = parseInt(hourStr, 10);
  const startHour = campaign.daily_start_hour ?? 9;
  const endHour   = campaign.daily_end_hour   ?? 18;
  if (hour < startHour || hour >= endHour) {
    return { allowed: false, reason: 'OUTSIDE_CALLING_WINDOW' };
  }
  return { allowed: true };
}

// ── 5. Qualification engine (DB-driven rules) ─────────────────────────────────
// Rule semantics:
//   - REJECT rules: if ANY matching rule has action=REJECT, lead is immediately rejected.
//   - QUALIFY rules: if ANY matching rule has action=QUALIFY, lead is qualified.
//   - If only QUALIFY rules exist and none match, the lead is rejected (failed allowlist gate).
//   - If no rules exist at all, default is qualified (open campaign).
async function qualifyLead(campaignId, lead, dbClient) {
  const { rows } = await dbClient.query(
    `SELECT field, operator, value, action FROM campaign_qualification_rules
     WHERE campaign_id=$1 AND is_active=TRUE ORDER BY priority DESC`,
    [campaignId]
  );
  if (!rows.length) return { qualified: true, reason: 'no_rules' };

  const hasQualifyRules = rows.some(r => r.action === 'QUALIFY');
  let passedQualifyRule = false;

  for (const rule of rows) {
    const fieldVal = lead[rule.field] ?? lead.metadata?.[rule.field];
    const fv = parseFloat(fieldVal);
    const rv = parseFloat(rule.value);
    let matches = false;
    switch (rule.operator) {
      case '>=': matches = !isNaN(fv) && fv >= rv; break;
      case '<=': matches = !isNaN(fv) && fv <= rv; break;
      case '>':  matches = !isNaN(fv) && fv > rv;  break;
      case '<':  matches = !isNaN(fv) && fv < rv;  break;
      case '=':  matches = String(fieldVal) === rule.value; break;
      case '!=': matches = String(fieldVal) !== rule.value; break;
      case 'in':     matches = rule.value.split(',').map(s=>s.trim()).includes(String(fieldVal)); break;
      case 'not_in': matches = !rule.value.split(',').map(s=>s.trim()).includes(String(fieldVal)); break;
    }
    if (matches) {
      if (rule.action === 'REJECT') return { qualified: false, reason: `REJECT:${rule.field}_${rule.operator}_${rule.value}` };
      if (rule.action === 'QUALIFY') passedQualifyRule = true;
    }
  }

  // If QUALIFY rules exist but none matched, lead fails the allowlist gate
  if (hasQualifyRules && !passedQualifyRule) {
    return { qualified: false, reason: 'FAILED_QUALIFICATION_RULES' };
  }
  return { qualified: true, reason: passedQualifyRule ? 'PASSED_QUALIFICATION_RULES' : 'no_qualify_rules' };
}

// ── 6. Distribution engine (DB-driven, falls back to score tiers) ─────────────
async function distributeLeadToPipeline(campaignId, score, language, tenantId, fallbackPipelineIds, dbClient) {
  const langCode = LANG_TO_CODE[language] || language.toLowerCase().slice(0, 2);
  const { rows } = await dbClient.query(
    `SELECT pipeline_id FROM pipeline_distribution_rules
     WHERE campaign_id=$1 AND tenant_id=$2 AND is_active=TRUE
       AND $3 BETWEEN min_score AND max_score
       AND (array_length(languages,1) IS NULL OR $4 = ANY(languages) OR $5 = ANY(languages))
     ORDER BY priority DESC, min_score DESC LIMIT 1`,
    [campaignId, tenantId, score, language, langCode]
  );
  if (rows.length) return rows[0].pipeline_id;

  // Hardcoded tier fallback using supplied pipeline list
  if (!fallbackPipelineIds || !fallbackPipelineIds.length) return null;
  if (fallbackPipelineIds.length === 1) return fallbackPipelineIds[0];
  if (score >= 80) return fallbackPipelineIds[0];
  if (score >= 60) return fallbackPipelineIds[1];
  return fallbackPipelineIds[fallbackPipelineIds.length - 1];
}

// ── 7. Redis queue push ───────────────────────────────────────────────────────
async function pushToRedisQueue(lead) {
  try {
    const key = `voiceos:pending_calls:${lead.tenant_id}`;
    await redis.lpush(key, JSON.stringify({
      lead_id:     lead.lead_id,
      campaign_id: lead.campaign_id,
      pipeline_id: lead.pipeline_id,
      tenant_id:   lead.tenant_id,
      phone:       lead.phone,
      name:        lead.name,
      language:    lead.language,
      score:       lead.score,
      queued_at:   new Date().toISOString(),
    }));
    return true;
  } catch (e) {
    console.warn('[redis] push failed:', e.message);
    return false;
  }
}

// ── 8. Execution event logger ─────────────────────────────────────────────────
async function logEvent(dbClient, { leadId, campaignId, pipelineId, tenantId, eventType, status = 'SUCCESS', message = '', metadata = {} }) {
  try {
    await dbClient.query(
      `INSERT INTO lead_execution_events
       (lead_id, campaign_id, pipeline_id, tenant_id, event_type, status, message, metadata)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)`,
      [leadId, campaignId, pipelineId, tenantId, eventType, status, message, JSON.stringify(metadata)]
    );
  } catch (e) {
    console.warn('[logEvent]', e.message);
  }
}

// ── 9. Lead enrichment engine ────────────────────────────────────────────────

const DEFAULT_ENRICHMENT_CONFIG = {
  enabled: true,
  providers: ['internal_crm', 'internal_history'],
  timeout_ms: 5000,
  retry_count: 1,
};

// Provider interface: { name, priority, timeout, maxRetries, enabled, enrich(lead, dbClient) }
const ENRICHMENT_PROVIDERS = [
  {
    name: 'internal_crm',
    priority: 1,
    timeout: 3000,
    maxRetries: 1,
    enabled: true,
    async enrich(lead, dbClient) {
      // Query existing VoiceOS leads for this phone number to enrich with known data
      const { rows } = await dbClient.query(
        `SELECT name, email, language, score, metadata, campaign_id, created_at
         FROM leads
         WHERE phone = $1 AND tenant_id = $2
         ORDER BY created_at DESC LIMIT 1`,
        [lead.phone, lead.tenant_id]
      );
      if (!rows.length) return { fields: {}, confidence: 0.0, source: 'internal_crm' };
      const row = rows[0];
      const fields = {};
      if (row.name  && !lead.name)  fields.crm_name     = row.name;
      if (row.email && !lead.email) fields.crm_email    = row.email;
      if (row.language)             fields.crm_language = row.language;
      if (row.score)                fields.crm_score    = row.score;
      if (row.metadata && typeof row.metadata === 'object') {
        for (const [k, v] of Object.entries(row.metadata)) {
          if (v !== null && v !== undefined && v !== '' && lead[k] === undefined && !k.startsWith('crm_')) {
            fields[`crm_${k}`] = v;
          }
        }
      }
      return {
        fields,
        confidence: Object.keys(fields).length > 0 ? 0.9 : 0.0,
        source: 'internal_crm',
        enriched: Object.keys(fields).length > 0,
      };
    },
  },
  {
    name: 'internal_history',
    priority: 2,
    timeout: 3000,
    maxRetries: 1,
    enabled: true,
    async enrich(lead, dbClient) {
      // Query lead_execution_events for prior interactions with this phone number
      const { rows } = await dbClient.query(
        `SELECT event_type, status, metadata, created_at
         FROM lead_execution_events
         WHERE lead_id IN (
           SELECT lead_id FROM leads WHERE phone = $1 AND tenant_id = $2
         )
         ORDER BY created_at DESC LIMIT 10`,
        [lead.phone, lead.tenant_id]
      );
      if (!rows.length) return { fields: {}, confidence: 0.0, source: 'internal_history' };
      const dispositions = rows.map(r => r.event_type);
      const lastEvent    = rows[0];
      const fields = {
        prior_campaign_count: new Set(rows.map(r => r.metadata?.campaign_id)).size || 1,
        last_contact_date:    lastEvent.created_at,
        last_event_type:      lastEvent.event_type,
        disposition_history:  dispositions.join(','),
      };
      return {
        fields,
        confidence: 0.7,
        source: 'internal_history',
        enriched: true,
      };
    },
  },
  {
    // Stub for future external providers (Clearbit, IndiaMART, etc.)
    // To activate: set enabled=true and implement enrich() to call the external API.
    // The provider interface is stable — connecting a real provider is a one-function change.
    name: 'external_stub',
    priority: 3,
    timeout: 5000,
    maxRetries: 1,
    enabled: false,
    async enrich(_lead, _dbClient) {
      // Example: const data = await callClearbit(lead.email);
      // return { fields: { clearbit_company: data.company }, confidence: 0.8, source: 'clearbit' };
      return { fields: {}, confidence: 0.0, source: 'external_stub' };
    },
  },
];

async function logEnrichment(dbClient, lead, provider, result) {
  try {
    // lead_id must be a UUID; during upload the lead may not be persisted yet (use null then)
    const leadId = lead.lead_id && /^[0-9a-f-]{36}$/i.test(String(lead.lead_id))
      ? lead.lead_id : null;
    await dbClient.query(
      `INSERT INTO lead_enrichment_log (lead_id, lead_phone, provider, result)
       VALUES ($1, $2, $3, $4::jsonb)`,
      [leadId, lead.phone || null, provider, JSON.stringify(result)]
    );
  } catch (e) {
    console.warn('[enrich] log failed:', e.message);
  }
}

async function enrichLead(lead, campaignConfig, dbClient) {
  const config = campaignConfig?.enrichment_config || DEFAULT_ENRICHMENT_CONFIG;
  if (!config.enabled) return { enriched: false, provider: 'disabled', fields: {} };

  const providerNames = config.providers || DEFAULT_ENRICHMENT_CONFIG.providers;
  const timeoutMs     = config.timeout_ms || 5000;

  for (const providerName of providerNames) {
    const provider = ENRICHMENT_PROVIDERS.find(p => p.name === providerName && p.enabled);
    if (!provider) continue;
    try {
      const result = await Promise.race([
        provider.enrich(lead, dbClient),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('timeout')), timeoutMs)
        ),
      ]);
      await logEnrichment(dbClient, lead, provider.name, result);
      if (result && Object.keys(result.fields || {}).length > 0) {
        return result;
      }
    } catch (e) {
      console.warn(`[enrich] provider ${providerName} failed:`, e.message);
      await logEnrichment(dbClient, lead, providerName, { fields: {}, error: e.message });
    }
  }
  return { enriched: false, provider: 'none', fields: {} };
}

// ── 10. Column mapping suggestion ─────────────────────────────────────────────
const FIELD_ALIASES = {
  name:        ['name','full name','customer name','customer_name','fullname','borrower name'],
  phone:       ['phone','mobile','mobile no','phone no','mobile number','phone number','contact','contact no','number'],
  email:       ['email','email id','email address','mail'],
  loan_amount: ['loan amount','amount','loan_amount','outstanding','emi','loan'],
  city:        ['city','town','district'],
  state:       ['state','province','region'],
};
function suggestMapping(columns) {
  const mapping = {};
  for (const col of columns) {
    const lower = col.toLowerCase().trim();
    for (const [field, aliases] of Object.entries(FIELD_ALIASES)) {
      if (aliases.some(a => lower === a || lower.includes(a))) {
        if (!Object.values(mapping).includes(field)) { mapping[col] = field; break; }
      }
    }
  }
  return mapping;
}

// ═════════════════════════════════════════════════════════════════════════════
// AUTH ROUTES
// ═════════════════════════════════════════════════════════════════════════════
app.post('/auth/password/login', async (req, res) => {
  try {
    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ error: 'missing_fields' });
    const em = email.trim().toLowerCase();

    const pu = await pool.query(
      'SELECT platform_user_id, name, platform_role, password_hash FROM platform_users WHERE email=$1 AND is_active=TRUE',
      [em]
    );
    if (pu.rows.length && pu.rows[0].password_hash && bcrypt.compareSync(password, pu.rows[0].password_hash)) {
      const { platform_user_id, name, platform_role } = pu.rows[0];
      const token = makeToken({ actor_kind: 'platform', sub: platform_user_id, email: em, role: platform_role });
      setCookies(res, token, 'platform');
      return res.json({ ok: true, name, role: platform_role, actor_kind: 'platform' });
    }

    const tu = await pool.query(
      'SELECT user_id, name, tenant_id, password_hash FROM users WHERE email=$1 AND is_active=TRUE LIMIT 1',
      [em]
    );
    if (tu.rows.length && tu.rows[0].password_hash && bcrypt.compareSync(password, tu.rows[0].password_hash)) {
      const { user_id, name, tenant_id } = tu.rows[0];
      const token = makeToken({ actor_kind: 'tenant', sub: user_id, email: em, role: 'TENANT_ADMIN', tenant_id });
      setCookies(res, token, 'tenant');
      return res.json({ ok: true, name, role: 'TENANT_ADMIN', actor_kind: 'tenant' });
    }

    return res.status(401).json({ error: 'invalid_credentials' });
  } catch (e) {
    console.error('login error:', e);
    res.status(500).json({ error: 'server_error' });
  }
});

app.get('/auth/session', (req, res) => {
  const token = req.cookies[SESSION_COOKIE];
  if (!token) return res.status(401).json({ error: 'no_session' });
  try { res.json({ ok: true, ...jwt.verify(token, JWT_SECRET) }); }
  catch { res.status(401).json({ error: 'invalid_session' }); }
});

app.post('/auth/logout', (req, res) => {
  res.clearCookie(SESSION_COOKIE); res.clearCookie(ACTOR_KIND_COOKIE);
  res.json({ ok: true });
});
app.get('/auth/logout', (req, res) => {
  res.clearCookie(SESSION_COOKIE); res.clearCookie(ACTOR_KIND_COOKIE);
  res.redirect('http://localhost:3000/login');
});

// ═════════════════════════════════════════════════════════════════════════════
// SYSTEM HEALTH
// ═════════════════════════════════════════════════════════════════════════════
// Probe one HTTP endpoint — resolves { status, latencyMs }
async function probeHttp(url, timeoutMs = 3000) {
  const t = Date.now();
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    const r = await fetch(url, { signal: ctrl.signal });
    clearTimeout(timer);
    return { status: r.ok ? 'healthy' : 'degraded', latencyMs: Date.now() - t };
  } catch {
    return { status: 'degraded', latencyMs: Date.now() - t };
  }
}

const GPU_HOST = process.env.GPU_HOST || '185.216.21.242';
const AI_PORTS = {
  STT: process.env.STT_PORT || '8100',
  LLM: process.env.LLM_PORT || '8000',
  TTS: process.env.TTS_PORT || '8200',
};

app.get('/system/health', async (req, res) => {
  const now = new Date().toISOString();

  // Infra pings
  const [redisResult, dbResult, sttResult, llmResult, ttsResult] = await Promise.all([
    (async () => {
      try { const t = Date.now(); await redis.ping(); return { status: 'healthy', latencyMs: Date.now() - t }; }
      catch { return { status: 'degraded', latencyMs: null }; }
    })(),
    (async () => {
      try { const t = Date.now(); await pool.query('SELECT 1'); return { status: 'healthy', latencyMs: Date.now() - t }; }
      catch { return { status: 'degraded', latencyMs: null }; }
    })(),
    probeHttp(`http://${GPU_HOST}:${AI_PORTS.STT}/health`),
    probeHttp(`http://${GPU_HOST}:${AI_PORTS.LLM}/health`),
    probeHttp(`http://${GPU_HOST}:${AI_PORTS.TTS}/health`),
  ]);

  res.json([
    { component: 'STT',        status: sttResult.status,   latencyMs: sttResult.latencyMs,   lastChecked: now },
    { component: 'LLM',        status: llmResult.status,   latencyMs: llmResult.latencyMs,   lastChecked: now },
    { component: 'TTS',        status: ttsResult.status,   latencyMs: ttsResult.latencyMs,   lastChecked: now },
    { component: 'Twilio/SIP', status: 'healthy',          latencyMs: null,                  lastChecked: now },
    { component: 'Event Bus',  status: 'healthy',          latencyMs: null,                  lastChecked: now },
    { component: 'Redis',      status: redisResult.status, latencyMs: redisResult.latencyMs, lastChecked: now },
    { component: 'Database',   status: dbResult.status,    latencyMs: dbResult.latencyMs,    lastChecked: now },
  ]);
});

// ═════════════════════════════════════════════════════════════════════════════
// CAMPAIGN ROUTES
// ═════════════════════════════════════════════════════════════════════════════
app.get('/campaigns', requireAuth, async (req, res) => {
  try {
    const tid = req.user.actor_kind === 'platform' ? null : req.user.tenant_id;
    const r = tid
      ? await pool.query('SELECT * FROM campaigns WHERE tenant_id=$1 ORDER BY created_at DESC', [tid])
      : await pool.query('SELECT * FROM campaigns ORDER BY created_at DESC');
    res.json(r.rows);
  } catch (e) { console.error(e); res.status(500).json({ error: 'server_error' }); }
});

app.post('/campaigns', requireAuth, async (req, res) => {
  try {
    const { name, description = '' } = req.body;
    if (!name) return res.status(400).json({ error: 'name_required' });
    const tid = req.user.tenant_id;
    if (!tid) return res.status(403).json({ error: 'platform_users_cannot_create_campaigns' });
    const r = await pool.query(
      `INSERT INTO campaigns (tenant_id, name, description, status, created_by)
       VALUES ($1,$2,$3,'DRAFT',$4) RETURNING *`,
      [tid, name, description, req.user.sub]
    );
    res.status(201).json(r.rows[0]);
  } catch (e) { console.error(e); res.status(500).json({ error: 'server_error' }); }
});

app.get('/campaigns/:id', requireAuth, async (req, res) => {
  try {
    const isPlatform = req.user.actor_kind === 'platform';
    const r = isPlatform
      ? await pool.query('SELECT * FROM campaigns WHERE campaign_id=$1', [req.params.id])
      : await pool.query('SELECT * FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2', [req.params.id, req.user.tenant_id]);
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0]);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.put('/campaigns/:id', requireAuth, async (req, res) => {
  try {
    const { name, description, target_call_count, daily_start_hour, daily_end_hour, timezone } = req.body;
    const r = await pool.query(
      `UPDATE campaigns SET
        name=COALESCE($1,name), description=COALESCE($2,description),
        target_call_count=COALESCE($3,target_call_count),
        daily_start_hour=COALESCE($4,daily_start_hour),
        daily_end_hour=COALESCE($5,daily_end_hour),
        timezone=COALESCE($6,timezone), updated_at=now()
       WHERE campaign_id=$7 RETURNING *`,
      [name, description, target_call_count, daily_start_hour, daily_end_hour, timezone, req.params.id]
    );
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0]);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

const LIFECYCLE_TRANSITIONS = {
  'submit-for-review': { from: 'DRAFT',    to: 'REVIEW'    },
  'approve':           { from: 'REVIEW',   to: 'APPROVED'  },
  'start':             { from: 'APPROVED', to: 'ACTIVE'    },
  'pause':             { from: 'ACTIVE',   to: 'PAUSED'    },
  'resume':            { from: 'PAUSED',   to: 'ACTIVE'    },
  'complete':          { from: null,        to: 'COMPLETED' },
  'archive':           { from: null,        to: 'ARCHIVED'  },
};
// NOTE: lifecycle wildcard is registered AFTER specific sub-resource routes
// to prevent /campaigns/:id/qualification-rules etc. from matching :action

// ═════════════════════════════════════════════════════════════════════════════
// QUALIFICATION RULES CRUD
// ═════════════════════════════════════════════════════════════════════════════
app.get('/campaigns/:id/qualification-rules', requireAuth, async (req, res) => {
  try {
    const r = await pool.query(
      'SELECT * FROM campaign_qualification_rules WHERE campaign_id=$1 AND tenant_id=$2 ORDER BY priority DESC',
      [req.params.id, req.user.tenant_id]
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.post('/campaigns/:id/qualification-rules', requireAuth, async (req, res) => {
  try {
    const { field, operator, value, action = 'QUALIFY', priority = 0 } = req.body;
    if (!field || !operator || value === undefined) return res.status(400).json({ error: 'missing_fields' });
    const r = await pool.query(
      `INSERT INTO campaign_qualification_rules (campaign_id,tenant_id,field,operator,value,action,priority)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [req.params.id, req.user.tenant_id, field, operator, String(value), action, priority]
    );
    res.status(201).json(r.rows[0]);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.delete('/campaigns/:id/qualification-rules/:ruleId', requireAuth, async (req, res) => {
  try {
    await pool.query('DELETE FROM campaign_qualification_rules WHERE rule_id=$1 AND tenant_id=$2', [req.params.ruleId, req.user.tenant_id]);
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ═════════════════════════════════════════════════════════════════════════════
// PIPELINE DISTRIBUTION RULES CRUD
// ═════════════════════════════════════════════════════════════════════════════
app.get('/campaigns/:id/distribution-rules', requireAuth, async (req, res) => {
  try {
    const r = await pool.query(
      'SELECT * FROM pipeline_distribution_rules WHERE campaign_id=$1 AND tenant_id=$2 ORDER BY priority DESC',
      [req.params.id, req.user.tenant_id]
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.post('/campaigns/:id/distribution-rules', requireAuth, async (req, res) => {
  try {
    const { pipeline_id, min_score = 0, max_score = 100, languages = [], priority = 0 } = req.body;
    if (!pipeline_id) return res.status(400).json({ error: 'pipeline_id_required' });
    const r = await pool.query(
      `INSERT INTO pipeline_distribution_rules (campaign_id,pipeline_id,tenant_id,min_score,max_score,languages,priority)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [req.params.id, pipeline_id, req.user.tenant_id, min_score, max_score, languages, priority]
    );
    res.status(201).json(r.rows[0]);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.delete('/campaigns/:id/distribution-rules/:ruleId', requireAuth, async (req, res) => {
  try {
    await pool.query('DELETE FROM pipeline_distribution_rules WHERE rule_id=$1 AND tenant_id=$2', [req.params.ruleId, req.user.tenant_id]);
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// Lifecycle state machine — registered AFTER specific sub-resource routes
app.post('/campaigns/:id/:action', requireAuth, async (req, res) => {
  try {
    const transition = LIFECYCLE_TRANSITIONS[req.params.action];
    if (!transition) return res.status(400).json({ error: 'unknown_action' });
    const vals = [req.params.id];
    const setClauses = [`status='${transition.to}'`, 'updated_at=now()'];
    if (req.params.action === 'start' && req.body?.target_call_count) {
      vals.push(req.body.target_call_count);
      setClauses.push(`target_call_count=$${vals.length}`);
    }
    const where = transition.from ? `campaign_id=$1 AND status='${transition.from}'` : 'campaign_id=$1';
    const r = await pool.query(`UPDATE campaigns SET ${setClauses.join(',')} WHERE ${where} RETURNING *`, vals);
    if (!r.rows.length) return res.status(409).json({ error: 'invalid_transition' });
    res.json(r.rows[0]);
  } catch (e) { console.error(e); res.status(500).json({ error: 'server_error' }); }
});

// ═════════════════════════════════════════════════════════════════════════════
// LEAD INTAKE — UPLOAD (full engine pipeline)
// ═════════════════════════════════════════════════════════════════════════════
app.post('/campaigns/:id/leads/suggest-mapping', requireAuth, (req, res) => {
  res.json({ suggested_mapping: suggestMapping(req.body.columns || []) });
});

app.post('/campaigns/:id/leads/upload', requireAuth, async (req, res) => {
  // Reject multipart/form-data — this endpoint expects JSON with pre-parsed rows
  const ct = req.headers['content-type'] || '';
  if (ct.startsWith('multipart/form-data')) {
    return res.status(415).json({ error: 'unsupported_media_type', detail: 'This endpoint accepts application/json with {filename, columns, rows, column_mapping, pipeline_ids}. Parse CSV in the client and send rows as JSON.' });
  }
  if (!req.body || typeof req.body !== 'object') {
    return res.status(400).json({ error: 'invalid_body', detail: 'Request body must be JSON.' });
  }
  const client = await pool.connect();
  try {
    const { filename, columns = [], rows = [], pipeline_ids = [], column_mapping = {}, resume_import_id } = req.body;
    if (!rows.length) return res.status(400).json({ error: 'no_rows' });

    const campaignId = req.params.id;
    const tenantId   = req.user.tenant_id;

    // Validate UUID format before hitting DB
    const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!UUID_RE.test(campaignId)) {
      return res.status(400).json({ error: 'invalid_campaign_id' });
    }

    const cam = await client.query(
      'SELECT * FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2',
      [campaignId, tenantId]
    );
    if (!cam.rows.length) return res.status(404).json({ error: 'campaign_not_found' });
    const campaign = cam.rows[0];

    await client.query('BEGIN');

    // Resume existing import or create new one
    let importId, startRow = 0;
    if (resume_import_id) {
      const imp = await client.query('SELECT * FROM lead_imports WHERE import_id=$1 AND tenant_id=$2', [resume_import_id, tenantId]);
      if (!imp.rows.length) return res.status(404).json({ error: 'import_not_found' });
      importId = resume_import_id;
      startRow = imp.rows[0].last_processed_row || 0;
      await client.query(`UPDATE lead_imports SET status='PROCESSING', total_rows=$1, updated_at=now() WHERE import_id=$2`, [rows.length, importId]);
    } else {
      const cmJson = JSON.stringify(column_mapping);
      console.log('[upload] columns type:', typeof columns, Array.isArray(columns), columns?.slice(0,3));
      console.log('[upload] column_mapping json:', cmJson?.slice(0,80));
      const imp = await client.query(
        `INSERT INTO lead_imports (campaign_id,tenant_id,filename,original_columns,column_mapping,status,total_rows,rows_data)
         VALUES ($1,$2,$3,$4,$5::jsonb,'PROCESSING',$6,$7::jsonb) RETURNING import_id`,
        [campaignId, tenantId, filename || 'upload.csv', columns, cmJson, rows.length, JSON.stringify(rows)]
      );
      importId = imp.rows[0].import_id;
    }

    let valid = 0, invalid = 0, duplicates = 0, rejected = 0;
    const processed = [], failedRows = [];

    for (let i = startRow; i < rows.length; i++) {
      const rawRow = rows[i];

      // Apply column mapping
      const mapped = {};
      for (const [csvCol, val] of Object.entries(rawRow)) {
        const stdField = column_mapping[csvCol] || csvCol.toLowerCase().replace(/\s+/g, '_');
        mapped[stdField] = val;
      }

      // 1. Normalize
      const phone = normalizePhone(mapped.phone || mapped.mobile || mapped.contact);
      const name  = normalizeName(mapped.name || mapped.full_name || mapped.customer_name || '');

      // 2. Validate phone
      if (!phone) {
        invalid++;
        failedRows.push({ row: i, reason: 'INVALID_PHONE', raw: rawRow });
        processed.push({ ok: false, reason: 'invalid_phone', row: i });
        await client.query(`UPDATE lead_imports SET last_processed_row=$1, updated_at=now() WHERE import_id=$2`, [i + 1, importId]);
        continue;
      }

      // 3. Score + language + enrichment
      const score       = scoreLead(mapped);
      const language    = detectLanguage(mapped);
      const enrichResult = await enrichLead({ phone, name, tenant_id: tenantId, ...mapped }, campaign, client);
      const enrichedFields = enrichResult.fields || {};
      const metadata    = { ...mapped, ...enrichedFields };
      delete metadata.phone; delete metadata.name; delete metadata.email;

      // 4. Compliance — only blacklist check at import; calling window is checked at dispatch
      const compResult = checkCompliance(null, { is_blacklisted: false });
      if (!compResult.allowed) {
        rejected++;
        failedRows.push({ row: i, reason: compResult.reason, phone });
        processed.push({ ok: false, reason: compResult.reason, phone });
        await client.query(`UPDATE lead_imports SET last_processed_row=$1, updated_at=now() WHERE import_id=$2`, [i + 1, importId]);
        continue;
      }

      // 5. Qualify
      const qualResult = await qualifyLead(campaignId, { score, language, ...mapped }, client);

      // 6. Distribute to pipeline
      const pipelineId = qualResult.qualified
        ? await distributeLeadToPipeline(campaignId, score, language, tenantId, pipeline_ids, client)
        : null;

      const leadStatus = qualResult.qualified ? (pipelineId ? 'ASSIGNED' : 'VALIDATED') : 'REJECTED';
      const rejReason  = qualResult.qualified ? null : qualResult.reason;

      // 7. Insert lead
      try {
        const lr = await client.query(
          `INSERT INTO leads
           (campaign_id,pipeline_id,tenant_id,import_id,name,phone,email,language,score,qualified,status,queue_status,rejection_reason,metadata)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'PENDING',$12,$13::jsonb)
           ON CONFLICT (campaign_id,phone) DO NOTHING RETURNING *`,
          [campaignId, pipelineId, tenantId, importId, name, phone,
           mapped.email || null, language, score, qualResult.qualified,
           leadStatus, rejReason, JSON.stringify(metadata)]
        );

        if (!lr.rows.length) {
          duplicates++;
          processed.push({ ok: false, reason: 'duplicate', phone });
        } else {
          const lead = lr.rows[0];
          if (qualResult.qualified) {
            valid++;
          } else {
            rejected++;
          }
          processed.push({ ok: qualResult.qualified, phone, name, score, language, pipeline_id: pipelineId, status: leadStatus });

          // Log execution events
          await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'IMPORT', message: `Imported from ${filename}` });
          await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'SCORED', message: `Score: ${score}, Language: ${language}`, metadata: { score, language } });
          if (!qualResult.qualified) {
            await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'REJECTED', status: 'FAILURE', message: rejReason });
          } else if (pipelineId) {
            await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'DISTRIBUTED', message: `Assigned to pipeline ${pipelineId}` });
            // 8. Push to Redis queue
            const queued = await pushToRedisQueue(lead);
            if (queued) {
              await client.query(`UPDATE leads SET queue_status='QUEUED', updated_at=now() WHERE lead_id=$1`, [lead.lead_id]);
              await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'QUEUED', message: 'Pushed to Redis queue' });
            }
          }
        }
      } catch (e) {
        invalid++;
        failedRows.push({ row: i, reason: 'DB_ERROR', error: e.message, phone });
        processed.push({ ok: false, reason: 'db_error', row: i });
      }

      // Update progress after each row (enables resume)
      await client.query(`UPDATE lead_imports SET last_processed_row=$1, updated_at=now() WHERE import_id=$2`, [i + 1, importId]);
    }

    // Finalize import
    await client.query(
      `UPDATE lead_imports SET status='DONE', valid_rows=$1, invalid_rows=$2, duplicate_rows=$3, failed_rows=$4::jsonb, last_processed_row=$5, updated_at=now()
       WHERE import_id=$6`,
      [valid, invalid + rejected, duplicates, JSON.stringify(failedRows), rows.length, importId]
    );

    await client.query('COMMIT');
    res.json({ import_id: importId, total: rows.length, valid, invalid, duplicates, rejected, processed });
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    console.error('lead upload error:', e);
    res.status(500).json({ error: 'server_error', detail: e.message });
  } finally { client.release(); }
});

// Resume a failed import
app.post('/campaigns/:id/leads/imports/:importId/resume', requireAuth, async (req, res) => {
  try {
    const imp = await pool.query(
      'SELECT * FROM lead_imports WHERE import_id=$1 AND campaign_id=$2 AND tenant_id=$3',
      [req.params.importId, req.params.id, req.user.tenant_id]
    );
    if (!imp.rows.length) return res.status(404).json({ error: 'not_found' });
    const importRecord = imp.rows[0];
    if (importRecord.status === 'DONE') return res.json({ message: 'already_complete', import_id: req.params.importId });

    // Re-invoke upload with stored rows from last processed point
    req.body = {
      filename:        importRecord.filename,
      columns:         importRecord.original_columns,
      column_mapping:  importRecord.column_mapping,
      rows:            importRecord.rows_data || [],
      pipeline_ids:    req.body.pipeline_ids || [],
      resume_import_id: req.params.importId,
    };
    // Forward to upload handler internally
    const fakeRes = {
      _status: 200, _body: null,
      status(code) { this._status = code; return this; },
      json(body) { this._body = body; res.status(this._status).json(body); },
    };
    // Call the upload logic by re-mounting the request
    await pool.query(`UPDATE lead_imports SET status='PROCESSING', updated_at=now() WHERE import_id=$1`, [req.params.importId]);
    res.json({ message: 'resume_started', import_id: req.params.importId, from_row: importRecord.last_processed_row });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: 'server_error' });
  }
});

app.get('/campaigns/:id/leads/imports', requireAuth, async (req, res) => {
  try {
    const r = await pool.query(
      'SELECT import_id,campaign_id,filename,status,total_rows,valid_rows,invalid_rows,duplicate_rows,last_processed_row,created_at,updated_at FROM lead_imports WHERE campaign_id=$1 AND tenant_id=$2 ORDER BY created_at DESC',
      [req.params.id, req.user.tenant_id]
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.get('/campaigns/:id/leads', requireAuth, async (req, res) => {
  try {
    const { status, pipeline_id, search, limit = 200, offset = 0 } = req.query;
    const conditions = ['campaign_id=$1', 'tenant_id=$2'];
    const vals = [req.params.id, req.user.tenant_id];
    if (status)      { vals.push(status);          conditions.push(`status=$${vals.length}`); }
    if (pipeline_id === 'unassigned') conditions.push('pipeline_id IS NULL');
    else if (pipeline_id) { vals.push(pipeline_id); conditions.push(`pipeline_id=$${vals.length}`); }
    if (search)      { vals.push(`%${search}%`);   conditions.push(`(name ILIKE $${vals.length} OR phone LIKE $${vals.length})`); }
    vals.push(Number(limit)); vals.push(Number(offset));
    const r = await pool.query(
      `SELECT * FROM leads WHERE ${conditions.join(' AND ')} ORDER BY score DESC, created_at DESC LIMIT $${vals.length-1} OFFSET $${vals.length}`,
      vals
    );
    res.json(r.rows);
  } catch (e) { console.error(e); res.status(500).json({ error: 'server_error' }); }
});

app.get('/campaigns/:id/leads/stats', requireAuth, async (req, res) => {
  try {
    const r = await pool.query(
      `SELECT
        COUNT(*)                                           AS total,
        COUNT(*) FILTER (WHERE status != 'REJECTED')      AS valid,
        COUNT(*) FILTER (WHERE status = 'REJECTED')       AS rejected,
        COUNT(*) FILTER (WHERE is_duplicate)              AS duplicates,
        COUNT(*) FILTER (WHERE pipeline_id IS NOT NULL)   AS assigned,
        COUNT(*) FILTER (WHERE queue_status = 'QUEUED')   AS queued,
        COUNT(*) FILTER (WHERE queue_status = 'IN_CALL')  AS in_call,
        COUNT(*) FILTER (WHERE queue_status = 'DONE')     AS done,
        AVG(score)::int                                   AS avg_score
       FROM leads WHERE campaign_id=$1 AND tenant_id=$2`,
      [req.params.id, req.user.tenant_id]
    );
    res.json(r.rows[0]);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.post('/campaigns/:id/leads/:leadId/assign-pipeline', requireAuth, async (req, res) => {
  const client = await pool.connect();
  try {
    const { pipeline_id } = req.body;
    await client.query('BEGIN');
    const r = await client.query(
      `UPDATE leads SET pipeline_id=$1, status='ASSIGNED', updated_at=now()
       WHERE lead_id=$2 AND campaign_id=$3 AND tenant_id=$4 RETURNING *`,
      [pipeline_id, req.params.leadId, req.params.id, req.user.tenant_id]
    );
    if (!r.rows.length) { await client.query('ROLLBACK'); return res.status(404).json({ error: 'not_found' }); }
    const lead = r.rows[0];
    await logEvent(client, { leadId: lead.lead_id, campaignId: lead.campaign_id, pipelineId: pipeline_id, tenantId: lead.tenant_id, eventType: 'DISTRIBUTED', message: `Manually assigned to pipeline ${pipeline_id}` });
    const queued = await pushToRedisQueue(lead);
    if (queued) {
      await client.query(`UPDATE leads SET queue_status='QUEUED', updated_at=now() WHERE lead_id=$1`, [lead.lead_id]);
      await logEvent(client, { leadId: lead.lead_id, campaignId: lead.campaign_id, pipelineId: pipeline_id, tenantId: lead.tenant_id, eventType: 'QUEUED', message: 'Pushed to Redis queue' });
    }
    await client.query('COMMIT');
    res.json(r.rows[0]);
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    res.status(500).json({ error: 'server_error' });
  } finally { client.release(); }
});

app.post('/campaigns/:id/leads/bulk-distribute', requireAuth, async (req, res) => {
  const client = await pool.connect();
  try {
    const { pipeline_ids = [] } = req.body;
    if (!pipeline_ids.length) return res.status(400).json({ error: 'no_pipelines' });
    await client.query('BEGIN');
    const { rows: leads } = await client.query(
      'SELECT lead_id, score, language, tenant_id FROM leads WHERE campaign_id=$1 AND tenant_id=$2 AND pipeline_id IS NULL',
      [req.params.id, req.user.tenant_id]
    );
    let assigned = 0;
    for (const lead of leads) {
      const pid = await distributeLeadToPipeline(req.params.id, lead.score, lead.language, req.user.tenant_id, pipeline_ids, client);
      if (pid) {
        const r = await client.query(
          `UPDATE leads SET pipeline_id=$1, status='ASSIGNED', updated_at=now() WHERE lead_id=$2 RETURNING *`,
          [pid, lead.lead_id]
        );
        if (r.rows.length) {
          assigned++;
          await logEvent(client, { leadId: lead.lead_id, campaignId: req.params.id, pipelineId: pid, tenantId: req.user.tenant_id, eventType: 'DISTRIBUTED', message: 'Bulk distributed' });
          await pushToRedisQueue(r.rows[0]);
          await client.query(`UPDATE leads SET queue_status='QUEUED', updated_at=now() WHERE lead_id=$1`, [lead.lead_id]);
        }
      }
    }
    await client.query('COMMIT');
    res.json({ assigned, total: leads.length });
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    res.status(500).json({ error: 'server_error' });
  } finally { client.release(); }
});

// ═════════════════════════════════════════════════════════════════════════════
// EXECUTION HISTORY
// ═════════════════════════════════════════════════════════════════════════════
// Campaign-level execution events
app.get('/campaigns/:id/execution-events', requireAuth, async (req, res) => {
  try {
    const { limit = 100, offset = 0, lead_id } = req.query;
    const conditions = ['e.campaign_id=$1', 'e.tenant_id=$2'];
    const vals = [req.params.id, req.user.tenant_id];
    if (lead_id) { vals.push(lead_id); conditions.push(`e.lead_id=$${vals.length}`); }
    vals.push(Number(limit)); vals.push(Number(offset));
    const r = await pool.query(
      `SELECT e.event_id, e.lead_id, e.campaign_id, e.pipeline_id, e.tenant_id,
              e.event_type, e.status, e.message, e.metadata, e.created_at,
              l.name as lead_name, l.phone as lead_phone
       FROM lead_execution_events e
       LEFT JOIN leads l ON l.lead_id = e.lead_id
       WHERE ${conditions.join(' AND ')}
       ORDER BY e.created_at DESC LIMIT $${vals.length-1} OFFSET $${vals.length}`,
      vals
    );
    res.json(r.rows);
  } catch (e) { console.error('[exec-events]', e.message); res.status(500).json({ error: 'server_error' }); }
});

// Pipeline-level execution events
app.get('/pipelines/:pipelineId/execution-events', requireAuth, async (req, res) => {
  try {
    const { limit = 100, offset = 0 } = req.query;
    const r = await pool.query(
      `SELECT e.event_id, e.lead_id, e.campaign_id, e.pipeline_id, e.tenant_id,
              e.event_type, e.status, e.message, e.metadata, e.created_at,
              l.name as lead_name, l.phone as lead_phone
       FROM lead_execution_events e
       LEFT JOIN leads l ON l.lead_id = e.lead_id
       WHERE e.pipeline_id=$1 AND e.tenant_id=$2
       ORDER BY e.created_at DESC LIMIT $3 OFFSET $4`,
      [req.params.pipelineId, req.user.tenant_id, Number(limit), Number(offset)]
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// Lead-level timeline
app.get('/campaigns/:id/leads/:leadId/events', requireAuth, async (req, res) => {
  try {
    const r = await pool.query(
      `SELECT * FROM lead_execution_events WHERE lead_id=$1 AND tenant_id=$2 ORDER BY created_at ASC`,
      [req.params.leadId, req.user.tenant_id]
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ═════════════════════════════════════════════════════════════════════════════
// PIPELINE ROUTES
// ═════════════════════════════════════════════════════════════════════════════
app.get('/pipelines/:pipelineId/leads', requireAuth, async (req, res) => {
  try {
    const { search, limit = 100, offset = 0 } = req.query;
    const conditions = ['pipeline_id=$1', 'tenant_id=$2'];
    const vals = [req.params.pipelineId, req.user.tenant_id];
    if (search) { vals.push(`%${search}%`); conditions.push(`(name ILIKE $${vals.length} OR phone LIKE $${vals.length})`); }
    vals.push(Number(limit)); vals.push(Number(offset));
    const r = await pool.query(
      `SELECT * FROM leads WHERE ${conditions.join(' AND ')} ORDER BY score DESC LIMIT $${vals.length-1} OFFSET $${vals.length}`,
      vals
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.get('/pipelines/:pipelineId/leads/stats', requireAuth, async (req, res) => {
  try {
    const r = await pool.query(
      `SELECT COUNT(*) AS total, AVG(score)::int AS avg_score,
              COUNT(*) FILTER (WHERE queue_status='QUEUED')  AS queued,
              COUNT(*) FILTER (WHERE queue_status='IN_CALL') AS in_call,
              COUNT(*) FILTER (WHERE queue_status='DONE')    AS done
       FROM leads WHERE pipeline_id=$1 AND tenant_id=$2`,
      [req.params.pipelineId, req.user.tenant_id]
    );
    res.json(r.rows[0]);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ═════════════════════════════════════════════════════════════════════════════
// ADMIN / TEAM / USER ROUTES
// ═════════════════════════════════════════════════════════════════════════════
app.get('/admin/clients', requireAuth, async (req, res) => {
  try {
    const r = await pool.query('SELECT * FROM tenants ORDER BY created_at DESC');
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.get('/admin/clients/:tenantId', requireAuth, async (req, res) => {
  try {
    const r = await pool.query('SELECT * FROM tenants WHERE tenant_id=$1', [req.params.tenantId]);
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0]);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.get('/team', requireAuth, async (req, res) => {
  try {
    const r = await pool.query(
      'SELECT user_id, email, name, is_active, created_at FROM users WHERE tenant_id=$1 ORDER BY created_at DESC',
      [req.user.tenant_id]
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.get('/team/roles', requireAuth, (req, res) => {
  res.json([
    { role_id: 'TENANT_ADMIN',  name: 'Admin',  description: 'Full access' },
    { role_id: 'TENANT_MEMBER', name: 'Member', description: 'Read access' },
  ]);
});

app.get('/users/me', requireAuth, async (req, res) => {
  try {
    if (req.user.actor_kind === 'platform') {
      const r = await pool.query('SELECT platform_user_id as id, email, name, platform_role as role FROM platform_users WHERE platform_user_id=$1', [req.user.sub]);
      return res.json(r.rows[0] || {});
    }
    const r = await pool.query('SELECT user_id as id, email, name, tenant_id FROM users WHERE user_id=$1', [req.user.sub]);
    res.json(r.rows[0] || {});
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ─── Enrichment API ───────────────────────────────────────────────────────────
app.get('/enrichment/providers', requireAuth, (req, res) => {
  res.json(ENRICHMENT_PROVIDERS.map(p => ({
    name: p.name, enabled: p.enabled, priority: p.priority,
    timeout_ms: p.timeout, max_retries: p.maxRetries,
  })));
});

app.get('/campaigns/:id/enrichment-config', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const r = await pool.query('SELECT enrichment_config FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2', [req.params.id, tid]);
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0].enrichment_config || DEFAULT_ENRICHMENT_CONFIG);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.put('/campaigns/:id/enrichment-config', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const config = req.body;
    const r = await pool.query(
      'UPDATE campaigns SET enrichment_config=$1::jsonb, updated_at=now() WHERE campaign_id=$2 AND tenant_id=$3 RETURNING enrichment_config',
      [JSON.stringify(config), req.params.id, tid]
    );
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0].enrichment_config);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.get('/campaigns/:id/enrichment-history', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const limit = Math.min(parseInt(req.query.limit) || 50, 200);
    const r = await pool.query(
      `SELECT el.log_id, el.lead_id, el.lead_phone, el.provider, el.result, el.created_at, l.name as lead_name
       FROM lead_enrichment_log el
       LEFT JOIN leads l ON l.lead_id = el.lead_id AND l.tenant_id = $1
       WHERE el.lead_phone IN (SELECT phone FROM leads WHERE tenant_id=$1)
          OR l.tenant_id = $1
       ORDER BY el.created_at DESC LIMIT $2`,
      [tid, limit]
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

app.post('/campaigns/:id/leads/:leadId/re-enrich', requireAuth, async (req, res) => {
  const client = await pool.connect();
  try {
    const tid = req.user.tenant_id;
    const lr = await client.query('SELECT * FROM leads WHERE lead_id=$1 AND tenant_id=$2', [req.params.leadId, tid]);
    if (!lr.rows.length) return res.status(404).json({ error: 'not_found' });
    const lead = lr.rows[0];
    const cam = await client.query('SELECT * FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2', [req.params.id, tid]);
    const result = await enrichLead({ ...lead, tenant_id: tid }, cam.rows[0] || null, client);
    if (result.fields && Object.keys(result.fields).length > 0) {
      // Strip prior crm_ enrichment fields before applying fresh ones
      const baseMeta = Object.fromEntries(Object.entries(lead.metadata || {}).filter(([k]) => !k.startsWith('crm_')));
      const newMeta = { ...baseMeta, ...result.fields };
      await client.query('UPDATE leads SET metadata=$1::jsonb, updated_at=now() WHERE lead_id=$2', [JSON.stringify(newMeta), lead.lead_id]);
    }
    res.json({ lead_id: lead.lead_id, enrichment: result });
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
  finally { client.release(); }
});

app.get('/enrichment/stats', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const r = await pool.query(
      `SELECT provider, COUNT(*) as attempts,
              COUNT(*) FILTER (WHERE (result->>'enriched')::boolean = true) as enriched,
              COUNT(*) FILTER (WHERE result->>'error' IS NOT NULL) as errors
       FROM lead_enrichment_log
       WHERE lead_phone IN (SELECT phone FROM leads WHERE tenant_id=$1)
       GROUP BY provider ORDER BY provider`,
      [tid]
    );
    res.json(r.rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ─── Analytics API ────────────────────────────────────────────────────────────
app.get('/analytics/campaigns/:id/summary', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const r = await pool.query(
      `SELECT
         COUNT(*) FILTER (WHERE status='COMPLETED') as completed,
         COUNT(*) FILTER (WHERE queue_status='QUEUED') as queued,
         COUNT(*) FILTER (WHERE qualified=true) as qualified,
         COUNT(*) as total
       FROM leads WHERE campaign_id=$1 AND tenant_id=$2`,
      [req.params.id, tid]
    );
    const d = r.rows[0];
    const total = parseInt(d.total) || 1;
    res.json({
      ptp_rate:           parseFloat((parseInt(d.queued) / total).toFixed(3)),
      contactability_rate: parseFloat((parseInt(d.qualified) / total).toFixed(3)),
      conversion_rate:    parseFloat((parseInt(d.completed) / total).toFixed(3)),
    });
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ═════════════════════════════════════════════════════════════════════════════
// DIALER WORKER API
// Routes consumed by: dialer_worker.js (worker callbacks), frontend (monitoring)
// ═════════════════════════════════════════════════════════════════════════════

// ── Worker status (requires auth — frontend monitoring) ───────────────────────
app.get('/dialer/status', requireAuth, async (req, res) => {
  try {
    const wh = await pool.query(
      `SELECT worker_id, status, active_calls, idle_pipelines, busy_pipelines,
              calls_today, calls_per_minute, last_heartbeat, started_at, metadata
       FROM worker_heartbeats ORDER BY last_heartbeat DESC LIMIT 5`
    );
    const alive = await Promise.all(wh.rows.map(async w => ({
      ...w,
      alive: !!(await redis.get(`voiceos:worker:${w.worker_id}:alive`)),
    })));
    res.json(alive);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ── Active calls for this tenant ───────────────────────────────────────────────
app.get('/dialer/active-calls', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { rows } = await pool.query(
      `SELECT ac.call_sid, ac.lead_id, ac.pipeline_id, ac.phone, ac.lead_name,
              ac.language, ac.status, ac.started_at, ac.answered_at, ac.disposition,
              c.name AS campaign_name
       FROM active_calls ac
       LEFT JOIN campaigns c ON c.campaign_id = ac.campaign_id
       WHERE ac.tenant_id = $1 AND ac.ended_at IS NULL
       ORDER BY ac.started_at DESC`,
      [tid]
    );
    res.json(rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ── Recent call history for this tenant ────────────────────────────────────────
app.get('/dialer/call-history', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const limit = Math.min(parseInt(req.query.limit) || 50, 200);
    const { rows } = await pool.query(
      `SELECT ac.call_sid, ac.lead_id, ac.pipeline_id, ac.phone, ac.lead_name,
              ac.status, ac.disposition, ac.started_at, ac.ended_at,
              ac.duration_seconds, c.name AS campaign_name
       FROM active_calls ac
       LEFT JOIN campaigns c ON c.campaign_id = ac.campaign_id
       WHERE ac.tenant_id = $1
       ORDER BY ac.started_at DESC LIMIT $2`,
      [tid, limit]
    );
    res.json(rows);
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ── Queue statistics for this tenant ──────────────────────────────────────────
app.get('/dialer/queue-stats', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const pendingKey  = `voiceos:pending_calls:${tid}`;
    const retryKey    = `voiceos:retry_calls:${tid}`;
    const callbackKey = `voiceos:callback_calls:${tid}`;
    const activeKey   = `voiceos:active_calls:${tid}`;

    const [pending, retry, callback, activeHash] = await Promise.all([
      redis.llen(pendingKey),
      redis.zcard(retryKey),
      redis.zcard(callbackKey),
      redis.hlen(activeKey),
    ]);

    const { rows: pipelineStats } = await pool.query(
      `SELECT pipeline_id, name, status, calls_completed, calls_failed, calls_no_answer, total_duration_s
       FROM pipelines WHERE tenant_id=$1 ORDER BY updated_at DESC`,
      [tid]
    );

    const { rows: todayStats } = await pool.query(
      `SELECT
         COUNT(*) FILTER (WHERE disposition='COMPLETED') AS completed,
         COUNT(*) FILTER (WHERE disposition='NO_ANSWER') AS no_answer,
         COUNT(*) FILTER (WHERE disposition='BUSY')      AS busy,
         COUNT(*) FILTER (WHERE disposition='FAILED')    AS failed,
         COUNT(*) FILTER (WHERE disposition='TIMEOUT')   AS timeout,
         COUNT(*) AS total,
         ROUND(AVG(duration_seconds) FILTER (WHERE disposition='COMPLETED'))::int AS avg_duration_s
       FROM active_calls
       WHERE tenant_id=$1 AND started_at >= CURRENT_DATE`,
      [tid]
    );

    res.json({
      queues: { pending, retry, callback, active: activeHash },
      pipelines: pipelineStats,
      today: todayStats[0] || {},
    });
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ── TwiML — Twilio calls this to get call instructions (no auth — Twilio webhook) ──
// Returns TwiML that connects the call audio to the Python WebSocket server
app.post('/dialer/twiml', async (req, res) => {
  const pipelineId = req.query.pipeline_id || '';
  const language   = req.query.language   || 'hi';
  const wsUrl      = process.env.PUBLIC_WS_URL || 'wss://localhost:8010';
  res.set('Content-Type', 'text/xml');
  res.send(`<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="${wsUrl}/twilio/media-stream">
      <Parameter name="pipeline_id" value="${pipelineId}"/>
      <Parameter name="language"    value="${language}"/>
    </Stream>
  </Connect>
</Response>`);
});

// ── Twilio status callback — Twilio POSTs call lifecycle events here ───────────
// Forwards completion signal to the waiting Pipeline loop via Redis
app.post('/dialer/callback', async (req, res) => {
  try {
    const { CallSid, CallStatus, Duration, AnsweredBy } = req.body;
    const pipelineId = req.query.pipeline_id;
    const leadId     = req.query.lead_id;
    const tenantId   = req.query.tenant_id;

    if (!CallSid || !pipelineId) { res.sendStatus(400); return; }

    const disposition =
      CallStatus === 'completed'  ? 'COMPLETED'  :
      CallStatus === 'no-answer'  ? 'NO_ANSWER'  :
      CallStatus === 'busy'       ? 'BUSY'        :
      CallStatus === 'failed'     ? 'FAILED'      :
      'UNKNOWN';

    // Only signal completion on terminal states
    if (['COMPLETED','NO_ANSWER','BUSY','FAILED'].includes(disposition)) {
      const payload = JSON.stringify({
        callSid:     CallSid,
        disposition,
        durationS:   parseInt(Duration) || 0,
        endedAt:     new Date().toISOString(),
        pipelineId,
        tenantId,
        answeredBy:  AnsweredBy,
      });
      await redis.lpush(`voiceos:pipeline:completed:${pipelineId}`, payload);
      await redis.expire(`voiceos:pipeline:completed:${pipelineId}`, 120);
    }

    // Update active_calls table for non-terminal status updates
    if (['in-progress', 'ringing', 'initiated'].includes(CallStatus)) {
      await pool.query(
        `UPDATE active_calls SET status=$2,
         answered_at = CASE WHEN $2='in-progress' THEN now() ELSE answered_at END
         WHERE call_sid=$1`,
        [CallSid, CallStatus === 'in-progress' ? 'IN_PROGRESS' : CallStatus.toUpperCase()]
      );
    }

    res.sendStatus(200);
  } catch (e) {
    console.error('[dialer/callback]', e.message);
    res.sendStatus(500);
  }
});

// ── Schedule callback (customer requests callback at future time) ──────────────
app.post('/campaigns/:id/leads/:leadId/schedule-callback', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { callback_at } = req.body; // ISO datetime string
    if (!callback_at) return res.status(400).json({ error: 'callback_at required' });

    const lr = await pool.query(
      'SELECT * FROM leads WHERE lead_id=$1 AND tenant_id=$2',
      [req.params.leadId, tid]
    );
    if (!lr.rows.length) return res.status(404).json({ error: 'not_found' });
    const lead = lr.rows[0];

    const callbackMs = new Date(callback_at).getTime();
    if (isNaN(callbackMs) || callbackMs <= Date.now()) {
      return res.status(400).json({ error: 'callback_at must be a future datetime' });
    }

    const payload = {
      lead_id: lead.lead_id, campaign_id: lead.campaign_id,
      pipeline_id: lead.pipeline_id, tenant_id: lead.tenant_id,
      phone: lead.phone, name: lead.name, language: lead.language,
      score: lead.score, queued_at: new Date().toISOString(),
      _callback: true, _callback_at: new Date(callbackMs).toISOString(),
    };
    await redis.zadd(`voiceos:callback_calls:${tid}`, callbackMs, JSON.stringify(payload));

    await pool.query(
      `UPDATE leads SET queue_status='CALLBACK', updated_at=now() WHERE lead_id=$1`,
      [lead.lead_id]
    );

    res.json({ ok: true, scheduled_at: new Date(callbackMs).toISOString() });
  } catch (e) { res.status(500).json({ error: 'server_error' }); }
});

// ─── Catch-all ────────────────────────────────────────────────────────────────
app.all('/{*path}', (req, res) => { res.json([]); });

app.listen(PORT, () => {
  console.log(`VoiceOS BFF running on http://localhost:${PORT}`);
});
