'use strict';
const express      = require('express');
const { Pool }     = require('pg');
const jwt          = require('jsonwebtoken');
const bcrypt       = require('bcryptjs');
const cookieParser = require('cookie-parser');
const cors         = require('cors');
const Redis        = require('ioredis');
const twilio       = require('twilio');
const { randomUUID, createHmac } = require('crypto');
const { normalizeProviderStatus, canTransition } = require('./telephony_call_state');
const { parseCallbackInstant, evaluateWorkingHours, validTimezone } = require('./telephony_callback_policy');
const { buildCanonicalCallEvent } = require('./telephony_call_event');
const { persistCanonicalCallEvent } = require('./telephony_event_boundary');

// ─── Config ───────────────────────────────────────────────────────────────────
const app          = express();
const PORT         = 8000;
const JWT_SECRET = process.env.JWT_SECRET;
if (!JWT_SECRET) {
  process.stderr.write(JSON.stringify({ timestamp: new Date().toISOString(), level: 'ERROR', service: 'voiceos-bff', event: 'startup.fatal', error: 'JWT_SECRET not set' }) + '\n');
  process.exit(1);
}
const SESSION_COOKIE  = 'voiceos_session';
const ACTOR_KIND_COOKIE = 'voiceos_actor_kind';

// ─── Structured JSON logger (Phase 8a) ───────────────────────────────────────
const log = {
  _write(level, event, fields = {}) {
    process.stdout.write(JSON.stringify({
      timestamp: new Date().toISOString(),
      level,
      service:   'voiceos-bff',
      event,
      ...fields,
    }) + '\n');
  },
  info:  (event, fields) => log._write('INFO',  event, fields),
  warn:  (event, fields) => log._write('WARN',  event, fields),
  error: (event, fields) => log._write('ERROR', event, fields),
};

// ─── Prometheus operational metrics ──────────────────────────────────────────
const _metricCounters = new Map();
function _incMetric(name, labels = {}, delta = 1) {
  const key = JSON.stringify([name, labels]);
  _metricCounters.set(key, (_metricCounters.get(key) || 0) + delta);
}
function _withTimeout(promise, ms, label) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label + '_timeout')), ms); })]).finally(() => clearTimeout(timer));
}
function _escapeProm(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\\"').replace(/\n/g, '\\n');
}
function _promLabels(labels) {
  const entries = Object.entries(labels);
  return entries.length ? '{' + entries.map(([k, v]) => k + '="' + _escapeProm(v) + '"').join(',') + '}' : '';
}
function _renderCounter(name, help) {
  const lines = ['# HELP ' + name + ' ' + help, '# TYPE ' + name + ' counter'];
  for (const [rawKey, value] of _metricCounters) {
    const [metricName, labels] = JSON.parse(rawKey);
    if (metricName === name) lines.push(name + _promLabels(labels) + ' ' + value);
  }
  return lines.join('\n');
}
app.use((req, res, next) => {
  res.on('finish', () => {
    const route = req.route?.path || 'unmatched';
    const statusClass = String(res.statusCode).charAt(0) + 'xx';
    _incMetric('voiceos_bff_http_requests_total', { method: req.method, route, status_class: statusClass });
    if (res.statusCode >= 500) _incMetric('voiceos_bff_http_errors_total', { method: req.method, route });
  });
  next();
});
app.get('/metrics', (_req, res) => {
  res.type('text/plain').send([
    _renderCounter('voiceos_bff_http_requests_total', 'Total HTTP requests handled by the VoiceOS BFF.'),
    _renderCounter('voiceos_bff_http_errors_total', 'Total HTTP 5xx responses emitted by the VoiceOS BFF.'),
    _renderCounter('voiceos_bff_pg_errors_total', 'Total PostgreSQL client or pool errors observed by the VoiceOS BFF.'),
    _renderCounter('voiceos_bff_redis_errors_total', 'Total Redis errors observed by the VoiceOS BFF.'),
    _renderCounter('voiceos_bff_readiness_failures_total', 'Total BFF readiness checks with an unavailable dependency.'),
    _renderCounter('voiceos_telephony_webhook_events_total', 'Telephony webhook events processed by outcome and lifecycle state.'),
    _renderCounter('voiceos_telephony_webhook_duplicates_total', 'Duplicate telephony webhook deliveries suppressed.'),
    _renderCounter('voiceos_telephony_webhook_failures_total', 'Telephony webhook processing failures.'),
    _renderCounter('voiceos_telephony_webhook_processing_seconds_sum', 'Sum of telephony webhook processing latency in seconds.'),
    _renderCounter('voiceos_telephony_webhook_processing_seconds_count', 'Count of telephony webhook processing observations.'),
    _renderCounter('voiceos_telephony_calls_by_state_total', 'Telephony lifecycle transitions observed by canonical state.'),
    _renderCounter('voiceos_telephony_call_setup_seconds_sum', 'Sum of call setup latency from initiation to connection.'),
    _renderCounter('voiceos_telephony_call_setup_seconds_count', 'Count of call setup latency observations.'),
    _renderCounter('voiceos_telephony_canonical_events_created_total', 'Canonical telephony events durably created.'),
    _renderCounter('voiceos_telephony_canonical_event_duplicates_total', 'Duplicate canonical telephony events suppressed.'),
    _renderCounter('voiceos_telephony_callback_events_total', 'Telephony callback processing events by bounded outcome.')
  ].join('\n') + '\n');
});

// ─── Trace-ID propagation middleware ─────────────────────────────────────────
app.use((req, _res, next) => {
  req.traceId = req.headers['x-trace-id'] || randomUUID();
  next();
});

// ─── Audit log helper (Phase 8b) ─────────────────────────────────────────────
async function bffAudit(client, { req, action, resourceType, resourceId, outcome = 'SUCCESS', metadata = {} }) {
  const tenantId  = req?.user?.tenant_id || null;
  const actorId   = req?.user?.sub       || 'anonymous';
  const ip        = req?.ip              || '';
  const traceId   = req?.traceId        || '';
  try {
    await client.query(
      `INSERT INTO audit_log
         (tenant_id, actor_id, action, resource_type, resource_id, outcome, ip_address, event_payload)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)`,
      [tenantId, actorId, action, resourceType, String(resourceId), outcome, ip,
       JSON.stringify({ trace_id: traceId, ...metadata })]
    );
  } catch (e) {
    log.warn('audit.write_failed', { action, error: e.message });
  }
}

// ─── PostgreSQL ───────────────────────────────────────────────────────────────
// Phase 10b: configurable timeouts — DB_STATEMENT_TIMEOUT_MS, DB_CONNECTION_TIMEOUT_MS
const _DB_STATEMENT_TIMEOUT  = parseInt(process.env.DB_STATEMENT_TIMEOUT_MS  || '30000');
const _DB_CONNECTION_TIMEOUT = parseInt(process.env.DB_CONNECTION_TIMEOUT_MS || '5000');
const _poolCommon = {
  max: 10,
  connectionTimeoutMillis: _DB_CONNECTION_TIMEOUT,
  idleTimeoutMillis:       30000,
  options:                 `-c statement_timeout=${_DB_STATEMENT_TIMEOUT}`,
};
const pool = process.env.POSTGRES_DSN
  ? new Pool({ connectionString: process.env.POSTGRES_DSN, ..._poolCommon })
  : new Pool({
      host:     process.env.POSTGRES_HOST     || '127.0.0.1',
      port:     parseInt(process.env.POSTGRES_PORT || '5432'),
      database: process.env.POSTGRES_DB       || 'voiceos',
      user:     process.env.POSTGRES_USER     || 'voiceos',
      password: process.env.POSTGRES_PASSWORD || '',
      ..._poolCommon,
    });
pool.on('error', (err) => { _incMetric('voiceos_bff_pg_errors_total'); log.error('pg.idle_client_error', { error: err.message }); });

// ─── Redis ────────────────────────────────────────────────────────────────────
const redisOpts = {
  host: process.env.REDIS_HOST || '127.0.0.1',
  port: parseInt(process.env.REDIS_PORT || '6379'),
  lazyConnect: true,
  maxRetriesPerRequest: 1,
};
if (process.env.REDIS_PASSWORD) redisOpts.password = process.env.REDIS_PASSWORD;
const redis = new Redis(redisOpts);
redis.on('error', e => { _incMetric('voiceos_bff_redis_errors_total'); log.warn('redis.error', { error: e.message }); });
redis.connect().catch(e => log.warn('redis.connect_failed', { error: e.message }));

// ─── Middleware ───────────────────────────────────────────────────────────────
// Phase 10d: 50 MB only for the two bulk-import routes; 128 KB everywhere else
const _LARGE_BODY_RE = /^\/campaigns\/[^/]+\/leads\/(upload|imports\/[^/]+\/resume)$/;
app.use((req, res, next) => {
  express.json({ limit: _LARGE_BODY_RE.test(req.path) ? '50mb' : '128kb' })(req, res, next);
});
app.use(express.urlencoded({ extended: false })); // Twilio webhooks send form-encoded bodies
app.use(cookieParser());
// Phase 11d: multi-origin CORS — FRONTEND_ALLOWED_ORIGINS is comma-separated list of allowed origins
const _CORS_ORIGINS = (process.env.FRONTEND_ALLOWED_ORIGINS || process.env.FRONTEND_BASE_URL || 'http://localhost:3000')
  .split(',').map(o => o.trim()).filter(Boolean);
app.use(cors({
  origin: (origin, cb) => {
    if (!origin || _CORS_ORIGINS.includes(origin)) return cb(null, true);
    cb(null, false);
  },
  credentials: true,
}));

// ─── Phase 9d: Security headers ───────────────────────────────────────────────
app.use((_req, res, next) => {
  res.set('X-Content-Type-Options', 'nosniff');
  res.set('X-Frame-Options', 'DENY');
  res.set('X-XSS-Protection', '0');
  res.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.set('Permissions-Policy', 'geolocation=(), microphone=(), camera=()');
  if (process.env.NODE_ENV === 'production') {
    res.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  }
  next();
});

// ─── Phase 9 / 12c: soft JWT parse + JTI revocation check ────────────────────
app.use(async (req, _res, next) => {
  const token = req.cookies?.[SESSION_COOKIE];
  if (token) {
    try {
      const decoded = jwt.verify(token, JWT_SECRET);
      // Phase 12c: reject sessions whose JTI was revoked on logout
      if (decoded.jti) {
        const revoked = await redis.exists(`voiceos:revoked_jti:${decoded.jti}`).catch(() => 0);
        if (!revoked) req.user = decoded;
      } else {
        req.user = decoded;
      }
    } catch {}
  }
  next();
});

// ─── Phase 9c: API rate limiting (per-tenant or per-IP, sliding window) ───────
const API_RATE_WINDOW_S   = parseInt(process.env.API_RATE_WINDOW_S    || '60');
const API_RATE_MAX        = parseInt(process.env.API_RATE_MAX_REQUESTS || '300');
const _RL_BYPASS_PATHS    = new Set(['/dialer/twiml', '/dialer/callback', '/system/health']);

app.use(async (req, res, next) => {
  if (_RL_BYPASS_PATHS.has(req.path)) return next();
  const key = req.user?.tenant_id
    ? `voiceos:api_rl:t:${req.user.tenant_id}`
    : `voiceos:api_rl:ip:${req.ip}`;
  try {
    const count = await redis.incr(key);
    if (count === 1) await redis.expire(key, API_RATE_WINDOW_S);
    res.set('X-RateLimit-Limit',     String(API_RATE_MAX));
    res.set('X-RateLimit-Remaining', String(Math.max(0, API_RATE_MAX - count)));
    if (count > API_RATE_MAX) {
      const ttl = await redis.ttl(key);
      res.set('Retry-After', String(ttl > 0 ? ttl : API_RATE_WINDOW_S));
      log.warn('api.rate_limited', { key, count, trace_id: req.traceId });
      return res.status(429).json({ error: 'rate_limit_exceeded' });
    }
  } catch { /* Redis unavailable — fail open */ }
  next();
});

// ─── Phase 10c: Tenant active check ───────────────────────────────────────────
// Verifies the tenant is ACTIVE before allowing any tenant-scoped request through.
// Skips platform users and unauthenticated paths (health, Twilio callbacks).
app.use(async (req, res, next) => {
  if (!req.user || req.user.actor_kind !== 'tenant') return next();
  try {
    const r = await pool.query('SELECT status FROM tenants WHERE tenant_id=$1', [req.user.tenant_id]);
    if (!r.rows.length || r.rows[0].status !== 'ACTIVE') {
      log.warn('tenant.inactive', { tenant_id: req.user.tenant_id, status: r.rows[0]?.status, trace_id: req.traceId });
      return res.status(403).json({ error: 'tenant_suspended' });
    }
  } catch (e) {
    return next(e);
  }
  next();
});

// ─── Auth helpers ─────────────────────────────────────────────────────────────
function makeToken(payload) {
  // Phase 12c: include jti so individual sessions can be revoked on logout
  return jwt.sign({ ...payload, jti: randomUUID() }, JWT_SECRET, { expiresIn: '7d' });
}
function setCookies(res, token, actorKind) {
  const secure = process.env.NODE_ENV === 'production';
  // Phase 12e: explicit domain scoping (prevents subdomain cookie theft)
  const domain = process.env.COOKIE_DOMAIN || undefined;
  const opts = { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 7 * 24 * 3600 * 1000, secure, ...(domain ? { domain } : {}) };
  res.cookie(SESSION_COOKIE, token, opts);
  res.cookie(ACTOR_KIND_COOKIE, actorKind, opts);
}
function requireAuth(req, res, next) {
  if (!req.user) return res.status(401).json({ error: 'unauthorized' });
  next();
}

// ─── Phase 9a: Role-based access control ──────────────────────────────────────
function requireRole(...roles) {
  return (req, res, next) => {
    if (!roles.includes(req.user?.role)) {
      log.warn('authz.forbidden', { required: roles, actual: req.user?.role, trace_id: req.traceId });
      return res.status(403).json({ error: 'forbidden' });
    }
    next();
  };
}

// ─── Phase 9b: Login rate limiting ────────────────────────────────────────────
const LOGIN_WINDOW_S  = parseInt(process.env.LOGIN_RATE_WINDOW_S || '900');
const LOGIN_MAX_FAILS = parseInt(process.env.LOGIN_MAX_FAILURES   || '5');

async function checkLoginRateLimit(ip) {
  try {
    const count = parseInt(await redis.get(`voiceos:login_rl:${ip}`) || '0');
    if (count >= LOGIN_MAX_FAILS) {
      const ttl = await redis.ttl(`voiceos:login_rl:${ip}`);
      return { allowed: false, retryAfter: ttl > 0 ? ttl : LOGIN_WINDOW_S };
    }
  } catch {}
  return { allowed: true };
}
async function recordLoginFailure(ip) {
  try {
    const key   = `voiceos:login_rl:${ip}`;
    const count = await redis.incr(key);
    if (count === 1) await redis.expire(key, LOGIN_WINDOW_S);
  } catch {}
}
async function clearLoginRateLimit(ip) {
  try { await redis.del(`voiceos:login_rl:${ip}`); } catch {}
}

// ─── Phase 9e: Input validation ───────────────────────────────────────────────
function validateBody(schema) {
  return (req, res, next) => {
    const errors = [];
    for (const [field, rules] of Object.entries(schema)) {
      const val     = req.body?.[field];
      const present = val !== undefined && val !== null && val !== '';
      if (rules.required && !present) { errors.push(`${field} is required`); continue; }
      if (!present) continue;
      if (rules.type === 'string' && typeof val !== 'string') {
        errors.push(`${field} must be a string`);
        continue;
      }
      if (rules.maxLength && String(val).length > rules.maxLength)
        errors.push(`${field} exceeds maximum length of ${rules.maxLength}`);
      if (rules.minLength && String(val).trim().length < rules.minLength)
        errors.push(`${field} must be at least ${rules.minLength} characters`);
      if (rules.pattern && !rules.pattern.test(String(val)))
        errors.push(`${field} has invalid format`);
    }
    if (errors.length) {
      log.warn('validation.rejected', { path: req.path, errors, trace_id: req.traceId });
      return res.status(400).json({ error: 'validation_error', details: errors });
    }
    next();
  };
}

// ─── Phase 11b: UUID path-param validation ────────────────────────────────────
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function requireUUID(...params) {
  return (req, res, next) => {
    for (const p of params) {
      if (!UUID_RE.test(req.params[p])) {
        log.warn('validation.invalid_uuid', { param: p, value: req.params[p], trace_id: req.traceId });
        return res.status(400).json({ error: 'invalid_uuid', param: p });
      }
    }
    next();
  };
}

// ─── Phase 11a: Pagination helper ────────────────────────────────────────────
const PAGE_MAX_LIMIT = 200;
function parsePage(query, opts) {
  const maxLimit = (opts && opts.maxLimit)     || PAGE_MAX_LIMIT;
  const defLimit = (opts && opts.defaultLimit) || 50;
  const limit  = Math.min(Math.max(parseInt(query.limit)  || defLimit, 1), maxLimit);
  const offset = Math.max(parseInt(query.offset) || 0, 0);
  return { limit, offset };
}

// ─── Phase 11c: Request idempotency (prevents duplicate POST mutations) ───────
// Client sends Idempotency-Key header; first response is cached in Redis for 24h.
function idempotency(resourceType) {
  return async (req, res, next) => {
    const ikey = req.headers['idempotency-key'];
    if (!ikey) return next();
    const cacheKey = `voiceos:idempotency:${req.user?.tenant_id}:${resourceType}:${ikey}`;
    try {
      const cached = await redis.get(cacheKey);
      if (cached) {
        log.info('idempotency.hit', { resource_type: resourceType, trace_id: req.traceId });
        return res.status(200).json(JSON.parse(cached));
      }
      const origJson = res.json.bind(res);
      res.json = (body) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          redis.set(cacheKey, JSON.stringify(body), 'EX', 86400).catch(() => {});
        }
        return origJson(body);
      };
    } catch { /* Redis unavailable — proceed without idempotency */ }
    next();
  };
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
async function qualifyLead(campaignId, tenantId, lead, dbClient) {
  const { rows } = await dbClient.query(
    `SELECT field, operator, value, action FROM campaign_qualification_rules
     WHERE campaign_id=$1 AND tenant_id=$2 AND is_active=TRUE ORDER BY priority DESC`,
    [campaignId, tenantId]
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
    log.warn('redis.push_failed', { error: e.message });
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
    log.warn('db.log_event_failed', { error: e.message });
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
    log.warn('enrich.log_failed', { error: e.message });
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
      log.warn('enrich.provider_failed', { provider: providerName, error: e.message });
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

// ── 11. Import row processor — shared between upload and resume ───────────────
// Validates, normalizes, scores, enriches, qualifies, distributes, and inserts
// a single lead row. Returns an outcome descriptor; never throws.
async function processOneRow(client, {
  rowIndex, rawRow, columnMapping, campaignId, tenantId, importId,
  campaign, filename, pipelineIds,
}) {
  const mapped = {};
  for (const [csvCol, val] of Object.entries(rawRow)) {
    const stdField = columnMapping[csvCol] || csvCol.toLowerCase().replace(/\s+/g, '_');
    mapped[stdField] = val;
  }

  const phone = normalizePhone(mapped.phone || mapped.mobile || mapped.contact);
  const name  = normalizeName(mapped.name || mapped.full_name || mapped.customer_name || '');

  if (!phone) return { outcome: 'invalid', reason: 'INVALID_PHONE', raw: rawRow };

  const score        = scoreLead(mapped);
  const language     = detectLanguage(mapped);
  const enrichResult = await enrichLead({ phone, name, tenant_id: tenantId, ...mapped }, campaign, client);
  const metadata     = { ...mapped, ...(enrichResult.fields || {}) };
  delete metadata.phone; delete metadata.name; delete metadata.email;

  const compResult = checkCompliance(null, { is_blacklisted: false });
  if (!compResult.allowed) return { outcome: 'rejected', reason: compResult.reason, phone };

  const qualResult = await qualifyLead(campaignId, tenantId, { score, language, ...mapped }, client);
  const pipelineId = qualResult.qualified
    ? await distributeLeadToPipeline(campaignId, score, language, tenantId, pipelineIds, client)
    : null;

  const leadStatus = qualResult.qualified ? (pipelineId ? 'ASSIGNED' : 'VALIDATED') : 'REJECTED';
  const rejReason  = qualResult.qualified ? null : qualResult.reason;

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
    if (!lr.rows.length) return { outcome: 'duplicate', phone };

    const lead = lr.rows[0];
    await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'IMPORT', message: `Imported from ${filename}` });
    await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'SCORED', message: `Score: ${score}, Language: ${language}`, metadata: { score, language } });
    if (!qualResult.qualified) {
      await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'REJECTED', status: 'FAILURE', message: rejReason });
    } else if (pipelineId) {
      await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'DISTRIBUTED', message: `Assigned to pipeline ${pipelineId}` });
      // When require_crm_match_before_dial is set, defer queuing until CRM status is known
      if (!campaign.require_crm_match_before_dial) {
        const queued = await pushToRedisQueue(lead);
        if (queued) {
          await client.query(`UPDATE leads SET queue_status='QUEUED', updated_at=now() WHERE lead_id=$1`, [lead.lead_id]);
          await logEvent(client, { leadId: lead.lead_id, campaignId, pipelineId, tenantId, eventType: 'QUEUED', message: 'Pushed to Redis queue' });
        }
      }
    }
    return {
      outcome:     qualResult.qualified ? 'valid' : 'rejected',
      phone, name, score, language, pipeline_id: pipelineId, status: leadStatus,
    };
  } catch (e) {
    return { outcome: 'error', reason: 'DB_ERROR', error: e.message, phone };
  }
}

// ── 12. CRM phone matcher ─────────────────────────────────────────────────────
// Batch-checks a phone list against customer_contacts.
// Returns Map<phone, 'MATCHED'|'UNMATCHED'|'AMBIGUOUS'>.
//   MATCHED:   exactly 1 active, non-erased customer owns this phone
//   AMBIGUOUS: 2+ customers share this phone (data quality issue in CRM)
//   UNMATCHED: no CRM record found
async function crmMatchPhones(dbClient, phones, tenantId) {
  if (!phones.length) return new Map();
  const { rows } = await dbClient.query(
    `SELECT cc.value AS phone, COUNT(DISTINCT cc.customer_id)::int AS customer_count
     FROM customer_contacts cc
     JOIN customers c ON c.customer_id = cc.customer_id
     WHERE cc.tenant_id = $1
       AND cc.contact_type IN ('MOBILE','HOME','OFFICE','WHATSAPP')
       AND cc.value = ANY($2::text[])
       AND c.is_active = TRUE
       AND c.data_erasure_requested = FALSE
     GROUP BY cc.value`,
    [tenantId, phones]
  );
  const matchMap = new Map();
  for (const row of rows) {
    matchMap.set(row.phone, row.customer_count === 1 ? 'MATCHED' : 'AMBIGUOUS');
  }
  for (const phone of phones) {
    if (!matchMap.has(phone)) matchMap.set(phone, 'UNMATCHED');
  }
  return matchMap;
}

// ═════════════════════════════════════════════════════════════════════════════
// AUTH ROUTES
// ═════════════════════════════════════════════════════════════════════════════
app.post('/auth/password/login', async (req, res) => {
  try {
    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ error: 'missing_fields' });
    const em = email.trim().toLowerCase();

    // Phase 9b: brute-force protection
    const rl = await checkLoginRateLimit(req.ip);
    if (!rl.allowed) {
      res.set('Retry-After', String(rl.retryAfter));
      log.warn('auth.login_rate_limited', { ip: req.ip, trace_id: req.traceId });
      return res.status(429).json({ error: 'too_many_attempts', retry_after: rl.retryAfter });
    }

    const pu = await pool.query(
      'SELECT platform_user_id, name, platform_role, password_hash FROM platform_users WHERE email=$1 AND is_active=TRUE',
      [em]
    );
    if (pu.rows.length && pu.rows[0].password_hash && bcrypt.compareSync(password, pu.rows[0].password_hash)) {
      const { platform_user_id, name, platform_role } = pu.rows[0];
      const token = makeToken({ actor_kind: 'platform', sub: platform_user_id, email: em, role: platform_role });
      setCookies(res, token, 'platform');
      req.user = { sub: platform_user_id, actor_kind: 'platform' };
      await clearLoginRateLimit(req.ip);
      await bffAudit(pool, { req, action: 'auth.login', resourceType: 'user', resourceId: em, outcome: 'SUCCESS' });
      log.info('auth.login', { actor_kind: 'platform', trace_id: req.traceId });
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
      req.user = { sub: user_id, tenant_id, actor_kind: 'tenant' };
      await clearLoginRateLimit(req.ip);
      await bffAudit(pool, { req, action: 'auth.login', resourceType: 'user', resourceId: em, outcome: 'SUCCESS' });
      log.info('auth.login', { actor_kind: 'tenant', trace_id: req.traceId });
      return res.json({ ok: true, name, role: 'TENANT_ADMIN', actor_kind: 'tenant' });
    }

    await recordLoginFailure(req.ip);
    await bffAudit(pool, { req, action: 'auth.login', resourceType: 'user', resourceId: em || 'unknown', outcome: 'FAILURE' });
    log.warn('auth.login_failed', { trace_id: req.traceId });
    return res.status(401).json({ error: 'invalid_credentials' });
  } catch (e) {
    throw e;
  }
});

app.get('/auth/session', (req, res) => {
  const token = req.cookies[SESSION_COOKIE];
  if (!token) return res.status(401).json({ error: 'no_session' });
  try { res.json({ ok: true, ...jwt.verify(token, JWT_SECRET) }); }
  catch { res.status(401).json({ error: 'invalid_session' }); }
});

// Phase 10e: Refresh — reissue a fresh 7-day token from a valid existing session
app.post('/auth/refresh', requireAuth, (req, res) => {
  // Strip JWT metadata fields (iat, exp) and re-sign with the same identity claims
  const { iat, exp, ...claims } = req.user;  // eslint-disable-line no-unused-vars
  const token = makeToken(claims);
  setCookies(res, token, claims.actor_kind);
  log.info('auth.token_refreshed', { actor_kind: claims.actor_kind, trace_id: req.traceId });
  res.json({ ok: true });
});

async function _revokeSession(req) {
  // Phase 12c: add the session JTI to the Redis revocation set so the cookie
  // cannot be replayed after logout, even before the JWT naturally expires.
  if (req.user?.jti && req.user?.exp) {
    const ttl = Math.max(1, req.user.exp - Math.floor(Date.now() / 1000));
    await redis.set(`voiceos:revoked_jti:${req.user.jti}`, '1', 'EX', ttl).catch(() => {});
  }
}

app.post('/auth/logout', async (req, res) => {
  await _revokeSession(req);
  await bffAudit(pool, { req, action: 'auth.logout', resourceType: 'user', resourceId: req.user?.sub || 'anonymous' });
  log.info('auth.logout', { trace_id: req.traceId });
  res.clearCookie(SESSION_COOKIE); res.clearCookie(ACTOR_KIND_COOKIE);
  res.json({ ok: true });
});
app.get('/auth/logout', async (req, res) => {
  await _revokeSession(req);
  await bffAudit(pool, { req, action: 'auth.logout', resourceType: 'user', resourceId: req.user?.sub || 'anonymous' });
  log.info('auth.logout', { trace_id: req.traceId });
  res.clearCookie(SESSION_COOKIE); res.clearCookie(ACTOR_KIND_COOKIE);
  res.redirect((process.env.FRONTEND_BASE_URL || 'http://localhost:3000') + '/login');
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

app.get('/health/live', (_req, res) => {
  res.status(200).json({ status: 'healthy', service: 'voiceos-bff' });
});

app.get('/health/ready', async (_req, res) => {
  const checks = await Promise.all([
    (async () => {
      try { await pool.query('SELECT 1'); return true; } catch { return false; }
    })(),
    (async () => {
      try { return (await redis.ping()) === 'PONG'; } catch { return false; }
    })(),
  ]);
  const ready = checks.every(Boolean);
  if (!ready) _incMetric('voiceos_bff_readiness_failures_total');
  res.status(ready ? 200 : 503).json({
    status: ready ? 'healthy' : 'unhealthy',
    service: 'voiceos-bff',
    dependencies: { postgresql: checks[0] ? 'healthy' : 'unhealthy', redis: checks[1] ? 'healthy' : 'unhealthy' },
  });
});

app.get('/system/health', async (req, res) => {
  const now = new Date().toISOString();

  // Infra pings (concurrent — local, no tunnel)
  const [redisResult, dbResult] = await Promise.all([
    (async () => {
      try { const t = Date.now(); await redis.ping(); return { status: 'healthy', latencyMs: Date.now() - t }; }
      catch { return { status: 'degraded', latencyMs: null }; }
    })(),
    (async () => {
      try { const t = Date.now(); await pool.query('SELECT 1'); return { status: 'healthy', latencyMs: Date.now() - t }; }
      catch { return { status: 'degraded', latencyMs: null }; }
    })(),
  ]);
  // GPU probes sequential — WireGuard tunnel is TCP-over-TCP (socat/autossh); concurrent probes
  // cause head-of-line blocking and timeouts. Sequential adds ~600 ms but reliably succeeds.
  const sttResult = await probeHttp(`http://${GPU_HOST}:${AI_PORTS.STT}/health/ready`, 12000);
  const llmResult = await probeHttp(`http://${GPU_HOST}:${AI_PORTS.LLM}/health`,       12000);
  const ttsResult = await probeHttp(`http://${GPU_HOST}:${AI_PORTS.TTS}/health/ready`, 12000);

  res.json([
    { component: 'STT',        status: sttResult.status,   latencyMs: sttResult.latencyMs,   lastChecked: now },
    { component: 'LLM',        status: llmResult.status,   latencyMs: llmResult.latencyMs,   lastChecked: now },
    { component: 'TTS',        status: ttsResult.status,   latencyMs: ttsResult.latencyMs,   lastChecked: now },
    { component: 'Twilio/SIP', status: process.env.TELEPHONY_HEALTH_URL ? (await probeHttp(process.env.TELEPHONY_HEALTH_URL, 3000)).status : 'unknown', latencyMs: null, lastChecked: now },
    { component: 'Event Bus',  status: 'unknown', latencyMs: null, lastChecked: now },
    { component: 'Redis',      status: redisResult.status, latencyMs: redisResult.latencyMs, lastChecked: now },
    { component: 'Database',   status: dbResult.status,    latencyMs: dbResult.latencyMs,    lastChecked: now },
  ]);
});

// ═════════════════════════════════════════════════════════════════════════════
// CAMPAIGN ROUTES
// ═════════════════════════════════════════════════════════════════════════════
app.get('/campaigns', requireAuth, async (req, res) => {
  try {
    const { limit, offset } = parsePage(req.query);
    const tid = req.user.actor_kind === 'platform' ? null : req.user.tenant_id;
    const r = tid
      ? await pool.query('SELECT * FROM campaigns WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3', [tid, limit, offset])
      : await pool.query('SELECT * FROM campaigns ORDER BY created_at DESC LIMIT $1 OFFSET $2', [limit, offset]);
    res.json(r.rows);
  } catch (e) { throw e; }
});

app.post('/campaigns', requireAuth, idempotency('campaign'), validateBody({
  name:        { required: true, type: 'string', minLength: 1, maxLength: 255 },
  description: { type: 'string', maxLength: 2000 },
}), async (req, res) => {
  try {
    const { name, description = '', require_crm_match_before_dial = false } = req.body;
    if (!name) return res.status(400).json({ error: 'name_required' });
    const tid = req.user.tenant_id;
    if (!tid) return res.status(403).json({ error: 'platform_users_cannot_create_campaigns' });
    const r = await pool.query(
      `INSERT INTO campaigns (tenant_id, name, description, status, created_by, require_crm_match_before_dial)
       VALUES ($1,$2,$3,'DRAFT',$4,$5) RETURNING *`,
      [tid, name, description, req.user.sub, Boolean(require_crm_match_before_dial)]
    );
    await bffAudit(pool, { req, action: 'campaign.create', resourceType: 'campaign', resourceId: r.rows[0].campaign_id, metadata: { name } });
    log.info('campaign.created', { campaign_id: r.rows[0].campaign_id, trace_id: req.traceId, tenant_id: tid });
    res.status(201).json(r.rows[0]);
  } catch (e) { log.error('campaign.create_error', { error: e.message }); throw e; }
});

app.get('/campaigns/:id', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const isPlatform = req.user.actor_kind === 'platform';
    const r = isPlatform
      ? await pool.query('SELECT * FROM campaigns WHERE campaign_id=$1', [req.params.id])
      : await pool.query('SELECT * FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2', [req.params.id, req.user.tenant_id]);
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0]);
  } catch (e) { throw e; }
});

app.put('/campaigns/:id', requireAuth, requireUUID('id'), validateBody({
  name:        { type: 'string', minLength: 1, maxLength: 255 },
  description: { type: 'string', maxLength: 2000 },
}), async (req, res) => {
  try {
    const {
      name, description, target_call_count,
      daily_start_hour, daily_end_hour, timezone,
      scheduled_start, scheduled_end,
      allowed_weekdays, excluded_dates,
      max_attempts, retry_interval_hours,
      require_crm_match_before_dial,
    } = req.body;

    // Coerce arrays where present (frontend may send undefined = no change)
    const weekdays = Array.isArray(allowed_weekdays)
      ? allowed_weekdays.map(n => parseInt(n, 10)).filter(n => n >= 1 && n <= 7)
      : null;
    const excl = Array.isArray(excluded_dates)
      ? excluded_dates.filter(d => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d))
      : null;
    const crmFlag = require_crm_match_before_dial !== undefined ? Boolean(require_crm_match_before_dial) : null;

    const r = await pool.query(
      `UPDATE campaigns SET
        name=COALESCE($1,name),
        description=COALESCE($2,description),
        target_call_count=COALESCE($3,target_call_count),
        daily_start_hour=COALESCE($4,daily_start_hour),
        daily_end_hour=COALESCE($5,daily_end_hour),
        timezone=COALESCE($6,timezone),
        scheduled_start=COALESCE($7::timestamptz,scheduled_start),
        scheduled_end=COALESCE($8::timestamptz,scheduled_end),
        allowed_weekdays=COALESCE($9::smallint[],allowed_weekdays),
        excluded_dates=COALESCE($10::date[],excluded_dates),
        max_attempts=COALESCE($11,max_attempts),
        retry_interval_hours=COALESCE($12,retry_interval_hours),
        require_crm_match_before_dial=COALESCE($13,require_crm_match_before_dial),
        updated_at=now()
       WHERE campaign_id=$14 AND tenant_id=$15 RETURNING *`,
      [
        name, description, target_call_count,
        daily_start_hour, daily_end_hour, timezone,
        scheduled_start || null, scheduled_end || null,
        weekdays, excl,
        max_attempts, retry_interval_hours,
        crmFlag,
        req.params.id, req.user.tenant_id,
      ]
    );
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    await bffAudit(pool, { req, action: 'campaign.update', resourceType: 'campaign', resourceId: req.params.id, metadata: { fields: Object.keys(req.body) } });
    log.info('campaign.updated', { campaign_id: req.params.id, trace_id: req.traceId, tenant_id: req.user.tenant_id });
    res.json(r.rows[0]);
  } catch (e) { log.error('campaign.update_error', { error: e.message }); throw e; }
});

app.delete('/campaigns/:id', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const r = await pool.query(
      'DELETE FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2 RETURNING *',
      [req.params.id, req.user.tenant_id]
    );
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    await bffAudit(pool, { req, action: 'campaign.delete', resourceType: 'campaign', resourceId: req.params.id, metadata: { name: r.rows[0].name } });
    log.info('campaign.deleted', { campaign_id: req.params.id, trace_id: req.traceId, tenant_id: req.user.tenant_id });
    res.json(r.rows[0]);
  } catch (e) { log.error('campaign.delete_error', { error: e.message }); throw e; }
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
app.get('/campaigns/:id/qualification-rules', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { limit, offset } = parsePage(req.query, { defaultLimit: 100, maxLimit: 200 });
    const r = await pool.query(
      'SELECT * FROM campaign_qualification_rules WHERE campaign_id=$1 AND tenant_id=$2 ORDER BY priority DESC LIMIT $3 OFFSET $4',
      [req.params.id, req.user.tenant_id, limit, offset]
    );
    res.json(r.rows);
  } catch (e) { throw e; }
});

app.post('/campaigns/:id/qualification-rules', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { field, operator, value, action = 'QUALIFY', priority = 0 } = req.body;
    if (!field || !operator || value === undefined) return res.status(400).json({ error: 'missing_fields' });
    const r = await pool.query(
      `INSERT INTO campaign_qualification_rules (campaign_id,tenant_id,field,operator,value,action,priority)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [req.params.id, req.user.tenant_id, field, operator, String(value), action, priority]
    );
    res.status(201).json(r.rows[0]);
  } catch (e) { throw e; }
});

app.delete('/campaigns/:id/qualification-rules/:ruleId', requireAuth, requireUUID('id', 'ruleId'), async (req, res) => {
  try {
    await pool.query(
      'DELETE FROM campaign_qualification_rules WHERE rule_id=$1 AND campaign_id=$2 AND tenant_id=$3',
      [req.params.ruleId, req.params.id, req.user.tenant_id]
    );
    res.json({ ok: true });
  } catch (e) { throw e; }
});

// ═════════════════════════════════════════════════════════════════════════════
// PIPELINE DISTRIBUTION RULES CRUD
// ═════════════════════════════════════════════════════════════════════════════
app.get('/campaigns/:id/distribution-rules', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { limit, offset } = parsePage(req.query, { defaultLimit: 100, maxLimit: 200 });
    const r = await pool.query(
      'SELECT * FROM pipeline_distribution_rules WHERE campaign_id=$1 AND tenant_id=$2 ORDER BY priority DESC LIMIT $3 OFFSET $4',
      [req.params.id, req.user.tenant_id, limit, offset]
    );
    res.json(r.rows);
  } catch (e) { throw e; }
});

app.post('/campaigns/:id/distribution-rules', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { pipeline_id, min_score = 0, max_score = 100, languages = [], priority = 0 } = req.body;
    if (!pipeline_id) return res.status(400).json({ error: 'pipeline_id_required' });
    const r = await pool.query(
      `INSERT INTO pipeline_distribution_rules (campaign_id,pipeline_id,tenant_id,min_score,max_score,languages,priority)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [req.params.id, pipeline_id, req.user.tenant_id, min_score, max_score, languages, priority]
    );
    res.status(201).json(r.rows[0]);
  } catch (e) { throw e; }
});

app.delete('/campaigns/:id/distribution-rules/:ruleId', requireAuth, requireUUID('id', 'ruleId'), async (req, res) => {
  try {
    await pool.query(
      'DELETE FROM pipeline_distribution_rules WHERE rule_id=$1 AND campaign_id=$2 AND tenant_id=$3',
      [req.params.ruleId, req.params.id, req.user.tenant_id]
    );
    res.json({ ok: true });
  } catch (e) { throw e; }
});

// ═════════════════════════════════════════════════════════════════════════════
// PIPELINES — CRUD (backs frontend/lib/local-pipelines.ts)
// ═════════════════════════════════════════════════════════════════════════════
app.get('/campaigns/:id/pipelines', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const cam = await pool.query(
      'SELECT campaign_id FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2',
      [req.params.id, req.user.tenant_id]
    );
    if (!cam.rows.length) return res.status(404).json({ error: 'campaign_not_found' });
    const { limit, offset } = parsePage(req.query, { defaultLimit: 100, maxLimit: 200 });
    const r = await pool.query(
      `SELECT pipeline_id, campaign_id, tenant_id, name, status, created_at, updated_at, created_by
         FROM pipelines
        WHERE campaign_id=$1 AND tenant_id=$2
        ORDER BY created_at ASC LIMIT $3 OFFSET $4`,
      [req.params.id, req.user.tenant_id, limit, offset]
    );
    res.json(r.rows);
  } catch (e) { log.error('pipeline.list_error', { error: e.message }); throw e; }
});

app.post('/campaigns/:id/pipelines', requireAuth, requireUUID('id'), idempotency('pipeline'), validateBody({
  name: { required: true, type: 'string', minLength: 1, maxLength: 255 },
}), async (req, res) => {
  try {
    const { name } = req.body || {};
    if (!name || !String(name).trim()) return res.status(400).json({ error: 'missing_name' });
    // Verify tenant owns campaign
    const cam = await pool.query(
      'SELECT campaign_id FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2',
      [req.params.id, req.user.tenant_id]
    );
    if (!cam.rows.length) return res.status(404).json({ error: 'campaign_not_found' });

    const r = await pool.query(
      `INSERT INTO pipelines (tenant_id, campaign_id, name, status, created_by)
       VALUES ($1,$2,$3,'ACTIVE',$4)
       RETURNING pipeline_id, campaign_id, tenant_id, name, status, created_at, updated_at, created_by`,
      [req.user.tenant_id, req.params.id, String(name).trim(), req.user.email || req.user.sub || '']
    );
    await bffAudit(pool, { req, action: 'pipeline.create', resourceType: 'pipeline', resourceId: r.rows[0].pipeline_id, metadata: { campaign_id: req.params.id, name: String(name).trim() } });
    log.info('pipeline.created', { pipeline_id: r.rows[0].pipeline_id, campaign_id: req.params.id, trace_id: req.traceId, tenant_id: req.user.tenant_id });
    res.status(201).json(r.rows[0]);
  } catch (e) { log.error('pipeline.create_error', { error: e.message }); throw e; }
});

app.get('/campaigns/:id/pipelines/:pipelineId', requireAuth, requireUUID('id', 'pipelineId'), async (req, res) => {
  try {
    const r = await pool.query(
      `SELECT pipeline_id, campaign_id, tenant_id, name, status, created_at, updated_at, created_by
         FROM pipelines
        WHERE pipeline_id=$1 AND campaign_id=$2 AND tenant_id=$3`,
      [req.params.pipelineId, req.params.id, req.user.tenant_id]
    );
    if (!r.rows.length) return res.status(404).json({ error: 'pipeline_not_found' });
    res.json(r.rows[0]);
  } catch (e) { log.error('pipeline.get_error', { error: e.message }); throw e; }
});

app.patch('/campaigns/:id/pipelines/:pipelineId', requireAuth, requireUUID('id', 'pipelineId'), validateBody({
  name: { type: 'string', minLength: 1, maxLength: 255 },
}), async (req, res) => {
  try {
    const { name, status } = req.body || {};
    const set = [], vals = [];
    if (name && String(name).trim()) { vals.push(String(name).trim()); set.push(`name=$${vals.length}`); }
    if (status && ['DRAFT','ACTIVE','PAUSED','ARCHIVED'].includes(status)) { vals.push(status); set.push(`status=$${vals.length}`); }
    if (!set.length) return res.status(400).json({ error: 'no_updates' });
    set.push('updated_at=now()');
    vals.push(req.params.pipelineId, req.params.id, req.user.tenant_id);
    const r = await pool.query(
      `UPDATE pipelines SET ${set.join(', ')}
        WHERE pipeline_id=$${vals.length-2} AND campaign_id=$${vals.length-1} AND tenant_id=$${vals.length}
        RETURNING pipeline_id, campaign_id, tenant_id, name, status, created_at, updated_at, created_by`,
      vals
    );
    if (!r.rows.length) return res.status(404).json({ error: 'pipeline_not_found' });
    await bffAudit(pool, { req, action: 'pipeline.update', resourceType: 'pipeline', resourceId: req.params.pipelineId, metadata: { campaign_id: req.params.id, fields: Object.keys(req.body || {}) } });
    log.info('pipeline.updated', { pipeline_id: req.params.pipelineId, campaign_id: req.params.id, trace_id: req.traceId, tenant_id: req.user.tenant_id });
    res.json(r.rows[0]);
  } catch (e) { log.error('pipeline.patch_error', { error: e.message }); throw e; }
});

// Lifecycle state machine — registered AFTER specific sub-resource routes
app.post('/campaigns/:id/:action', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const transition = LIFECYCLE_TRANSITIONS[req.params.action];
    if (!transition) return res.status(400).json({ error: 'unknown_action' });
    const vals = [req.params.id, req.user.tenant_id];
    // Parameterize status values so this query is always fully parameterized
    vals.push(transition.to);
    const setClauses = [`status=$${vals.length}`, 'updated_at=now()'];
    if (req.params.action === 'start' && req.body?.target_call_count) {
      vals.push(req.body.target_call_count);
      setClauses.push(`target_call_count=$${vals.length}`);
    }
    let where = 'campaign_id=$1 AND tenant_id=$2';
    if (transition.from) {
      vals.push(transition.from);
      where += ` AND status=$${vals.length}`;
    }
    const r = await pool.query(`UPDATE campaigns SET ${setClauses.join(',')} WHERE ${where} RETURNING *`, vals);
    if (!r.rows.length) return res.status(409).json({ error: 'invalid_transition' });
    await bffAudit(pool, { req, action: 'campaign.state_change', resourceType: 'campaign', resourceId: req.params.id, metadata: { action: req.params.action, to: transition.to } });
    log.info('campaign.state_changed', { campaign_id: req.params.id, action: req.params.action, to: transition.to, trace_id: req.traceId, tenant_id: req.user.tenant_id });
    res.json(r.rows[0]);
  } catch (e) { throw e; }
});

// ═════════════════════════════════════════════════════════════════════════════
// LEAD INTAKE — UPLOAD (full engine pipeline)
// ═════════════════════════════════════════════════════════════════════════════
app.post('/campaigns/:id/leads/suggest-mapping', requireAuth, requireUUID('id'), (req, res) => {
  res.json({ suggested_mapping: suggestMapping(req.body.columns || []) });
});

app.post('/campaigns/:id/leads/upload', requireAuth, requireUUID('id'), async (req, res) => {
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
      log.info('upload.columns_parsed', { type: typeof columns, is_array: Array.isArray(columns) });
      log.info('upload.column_mapping', { preview: cmJson?.slice(0,80) });
      const imp = await client.query(
        `INSERT INTO lead_imports (campaign_id,tenant_id,filename,original_columns,column_mapping,status,total_rows,rows_data)
         VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,'PROCESSING',$6,$7::jsonb) RETURNING import_id`,
        [campaignId, tenantId, filename || 'upload.csv', JSON.stringify(columns), cmJson, rows.length, JSON.stringify(rows)]
      );
      importId = imp.rows[0].import_id;
    }

    let valid = 0, invalid = 0, duplicates = 0, rejected = 0;
    const processed = [], failedRows = [], insertedPhones = [];

    for (let i = startRow; i < rows.length; i++) {
      const result = await processOneRow(client, {
        rowIndex: i, rawRow: rows[i], columnMapping: column_mapping,
        campaignId, tenantId, importId, campaign, filename, pipelineIds: pipeline_ids,
      });

      switch (result.outcome) {
        case 'valid':
          valid++;
          insertedPhones.push(result.phone);
          processed.push({ ok: true, phone: result.phone, name: result.name, score: result.score, language: result.language, pipeline_id: result.pipeline_id, status: result.status });
          break;
        case 'rejected':
          rejected++;
          failedRows.push({ row: i, reason: result.reason, phone: result.phone });
          processed.push({ ok: false, reason: result.reason || 'rejected', phone: result.phone });
          if (result.status) insertedPhones.push(result.phone); // lead was inserted with status=REJECTED
          break;
        case 'duplicate':
          duplicates++;
          processed.push({ ok: false, reason: 'duplicate', phone: result.phone });
          break;
        case 'invalid':
          invalid++;
          failedRows.push({ row: i, reason: result.reason || 'INVALID_PHONE', raw: result.raw });
          processed.push({ ok: false, reason: 'invalid_phone', row: i });
          break;
        case 'error':
          invalid++;
          failedRows.push({ row: i, reason: result.reason, error: result.error, phone: result.phone });
          processed.push({ ok: false, reason: 'db_error', row: i });
          break;
      }

      await client.query(`UPDATE lead_imports SET last_processed_row=$1, updated_at=now() WHERE import_id=$2`, [i + 1, importId]);
    }

    // CRM match — batch-check all inserted phones against customer_contacts in one query per status
    let crm_matched = 0, crm_unmatched = 0, crm_ambiguous = 0;
    if (insertedPhones.length) {
      const crmMap   = await crmMatchPhones(client, insertedPhones, tenantId);
      const byStatus = { MATCHED: [], UNMATCHED: [], AMBIGUOUS: [] };
      for (const [phone, status] of crmMap) {
        byStatus[status].push(phone);
        if (status === 'MATCHED')        crm_matched++;
        else if (status === 'AMBIGUOUS') crm_ambiguous++;
        else                             crm_unmatched++;
      }
      for (const [status, phones] of Object.entries(byStatus)) {
        if (phones.length) {
          await client.query(
            `UPDATE leads SET metadata = metadata || $1::jsonb, updated_at=now()
             WHERE campaign_id=$2 AND tenant_id=$3 AND phone = ANY($4::text[])`,
            [JSON.stringify({ crm_match_status: status }), campaignId, tenantId, phones]
          );
        }
      }
      // Queue only MATCHED leads when campaign requires CRM match before dial
      if (campaign.require_crm_match_before_dial && byStatus.MATCHED.length) {
        const matchedLeads = await client.query(
          `SELECT * FROM leads WHERE campaign_id=$1 AND tenant_id=$2 AND phone = ANY($3::text[]) AND queue_status='PENDING'`,
          [campaignId, tenantId, byStatus.MATCHED]
        );
        for (const lead of matchedLeads.rows) {
          const queued = await pushToRedisQueue(lead);
          if (queued) {
            await client.query(`UPDATE leads SET queue_status='QUEUED', updated_at=now() WHERE lead_id=$1`, [lead.lead_id]);
          }
        }
      }
    }

    // Finalize import
    await client.query(
      `UPDATE lead_imports SET status='DONE', valid_rows=$1, invalid_rows=$2, duplicate_rows=$3, failed_rows=$4::jsonb, last_processed_row=$5, completed_at=NOW(), updated_at=now()
       WHERE import_id=$6`,
      [valid, invalid + rejected, duplicates, JSON.stringify(failedRows), rows.length, importId]
    );

    await client.query('COMMIT');
    await bffAudit(pool, { req, action: 'lead.upload', resourceType: 'lead_import', resourceId: importId, metadata: { campaign_id: campaignId, filename, total: rows.length, valid, invalid, duplicates, rejected } });
    log.info('lead.upload_complete', { import_id: importId, campaign_id: campaignId, total: rows.length, valid, invalid, duplicates, rejected, trace_id: req.traceId, tenant_id: tenantId });
    res.json({ import_id: importId, total: rows.length, valid, invalid, duplicates, rejected, processed, crm_matched, crm_unmatched, crm_ambiguous });
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    log.error('lead.upload_error', { error: e.message });
    res.status(500).json({ error: 'server_error', detail: e.message });
  } finally { client.release(); }
});

// Resume a failed or interrupted import — processes remaining rows in crash-safe batches
app.post('/campaigns/:id/leads/imports/:importId/resume', requireAuth, requireUUID('id', 'importId'), async (req, res) => {
  const campaignId = req.params.id;
  const tenantId   = req.user.tenant_id;
  const importId   = req.params.importId;
  try {
    // 1. Fetch import record with campaign + tenant isolation
    const impResult = await pool.query(
      'SELECT * FROM lead_imports WHERE import_id=$1 AND campaign_id=$2 AND tenant_id=$3',
      [importId, campaignId, tenantId]
    );
    if (!impResult.rows.length) return res.status(404).json({ error: 'not_found' });
    const importRecord = impResult.rows[0];

    // 2. Reject resume of an already-complete import
    if (importRecord.status === 'DONE') {
      return res.status(400).json({ error: 'import_already_complete', import_id: importId });
    }

    // 3. rows_data must be present — upload handler stores it for exactly this case
    const rows = importRecord.rows_data || [];
    if (!rows.length) {
      return res.status(400).json({ error: 'no_rows_data', detail: 'Import has no stored rows to resume.' });
    }

    const startRow = importRecord.last_processed_row || 0;

    // 4. Edge case: all rows were processed but finalization crashed — just finalize
    if (startRow >= rows.length) {
      await pool.query(
        `UPDATE lead_imports SET status='DONE', completed_at=NOW(), updated_at=now() WHERE import_id=$1`,
        [importId]
      );
      return res.json({ import_id: importId, message: 'already_processed', total: rows.length });
    }

    // 5. Fetch campaign record (required by processOneRow for enrichment config)
    const camResult = await pool.query(
      'SELECT * FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2',
      [campaignId, tenantId]
    );
    if (!camResult.rows.length) return res.status(404).json({ error: 'campaign_not_found' });
    const campaign      = camResult.rows[0];
    const pipeline_ids  = req.body?.pipeline_ids || [];
    const columnMapping = importRecord.column_mapping || {};

    // 6. Mark PROCESSING so concurrent callers can see state
    await pool.query(
      `UPDATE lead_imports SET status='PROCESSING', updated_at=now() WHERE import_id=$1`,
      [importId]
    );

    // 7. Process remaining rows in batches of 100.
    //    Each batch runs in its own transaction and commits last_processed_row on success.
    //    A crash mid-batch rolls back that batch; the next resume restarts from the last
    //    committed row — no lead is silently lost, and ON CONFLICT DO NOTHING in
    //    processOneRow provides idempotency for any rows re-processed after a retry.
    const BATCH_SIZE = 100;
    let valid = 0, invalid = 0, duplicates = 0, rejected = 0;
    const insertedPhones = [];
    const failedRows = Array.isArray(importRecord.failed_rows) ? [...importRecord.failed_rows] : [];

    for (let batchStart = startRow; batchStart < rows.length; batchStart += BATCH_SIZE) {
      const batchEnd    = Math.min(batchStart + BATCH_SIZE, rows.length);
      const batchClient = await pool.connect();
      try {
        await batchClient.query('BEGIN');
        for (let i = batchStart; i < batchEnd; i++) {
          const result = await processOneRow(batchClient, {
            rowIndex: i, rawRow: rows[i], columnMapping,
            campaignId, tenantId, importId, campaign,
            filename: importRecord.filename, pipelineIds: pipeline_ids,
          });
          switch (result.outcome) {
            case 'valid':
              valid++;
              insertedPhones.push(result.phone);
              break;
            case 'rejected':
              rejected++;
              failedRows.push({ row: i, reason: result.reason, phone: result.phone });
              if (result.status) insertedPhones.push(result.phone); // lead was inserted with status=REJECTED
              break;
            case 'duplicate': duplicates++; break;
            case 'invalid':
              invalid++;
              failedRows.push({ row: i, reason: result.reason || 'INVALID_PHONE', raw: result.raw });
              break;
            case 'error':
              invalid++;
              failedRows.push({ row: i, reason: result.reason, error: result.error, phone: result.phone });
              break;
          }
        }
        await batchClient.query(
          `UPDATE lead_imports SET last_processed_row=$1, updated_at=now() WHERE import_id=$2`,
          [batchEnd, importId]
        );
        await batchClient.query('COMMIT');
      } catch (batchErr) {
        await batchClient.query('ROLLBACK').catch(() => {});
        await pool.query(
          `UPDATE lead_imports SET status='FAILED', failed_rows=$1::jsonb, updated_at=now() WHERE import_id=$2`,
          [JSON.stringify(failedRows), importId]
        );
        log.error('resume.batch_failed', { batch_start: batchStart, batch_end: batchEnd, error: batchErr?.message || String(batchErr) });
        return res.status(500).json({ error: 'batch_failed', detail: batchErr.message, last_processed_row: batchStart });
      } finally {
        batchClient.release();
      }
    }

    // 8. CRM match + finalize in a single closing transaction
    let crm_matched = 0, crm_unmatched = 0, crm_ambiguous = 0;
    const finalClient = await pool.connect();
    try {
      await finalClient.query('BEGIN');
      if (insertedPhones.length) {
        const crmMap   = await crmMatchPhones(finalClient, insertedPhones, tenantId);
        const byStatus = { MATCHED: [], UNMATCHED: [], AMBIGUOUS: [] };
        for (const [phone, status] of crmMap) {
          byStatus[status].push(phone);
          if (status === 'MATCHED')        crm_matched++;
          else if (status === 'AMBIGUOUS') crm_ambiguous++;
          else                             crm_unmatched++;
        }
        for (const [status, phones] of Object.entries(byStatus)) {
          if (phones.length) {
            await finalClient.query(
              `UPDATE leads SET metadata = metadata || $1::jsonb, updated_at=now()
               WHERE campaign_id=$2 AND tenant_id=$3 AND phone = ANY($4::text[])`,
              [JSON.stringify({ crm_match_status: status }), campaignId, tenantId, phones]
            );
          }
        }
        // Queue only MATCHED leads when campaign requires CRM match before dial
        if (campaign.require_crm_match_before_dial && byStatus.MATCHED.length) {
          const matchedLeads = await finalClient.query(
            `SELECT * FROM leads WHERE campaign_id=$1 AND tenant_id=$2 AND phone = ANY($3::text[]) AND queue_status='PENDING'`,
            [campaignId, tenantId, byStatus.MATCHED]
          );
          for (const lead of matchedLeads.rows) {
            const queued = await pushToRedisQueue(lead);
            if (queued) {
              await finalClient.query(`UPDATE leads SET queue_status='QUEUED', updated_at=now() WHERE lead_id=$1`, [lead.lead_id]);
            }
          }
        }
      }
      await finalClient.query(
        `UPDATE lead_imports SET status='DONE', valid_rows=$1, invalid_rows=$2, duplicate_rows=$3,
         failed_rows=$4::jsonb, completed_at=NOW(), updated_at=now() WHERE import_id=$5`,
        [valid, invalid + rejected, duplicates, JSON.stringify(failedRows), importId]
      );
      await finalClient.query('COMMIT');
    } catch (finalErr) {
      await finalClient.query('ROLLBACK').catch(() => {});
      await pool.query(
        `UPDATE lead_imports SET status='FAILED', updated_at=now() WHERE import_id=$1`,
        [importId]
      );
      log.error('resume.finalization_failed', { error: finalErr?.message || String(finalErr) });
      return res.status(500).json({ error: 'finalization_failed', detail: finalErr.message });
    } finally {
      finalClient.release();
    }

    res.json({
      import_id: importId, total: rows.length, from_row: startRow,
      valid, invalid, duplicates, rejected,
      crm_matched, crm_unmatched, crm_ambiguous,
    });
  } catch (e) {
    log.error('resume.error', { error: e.message });
    res.status(500).json({ error: 'server_error', detail: e.message });
  }
});

app.get('/campaigns/:id/leads/imports', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { limit, offset } = parsePage(req.query);
    const r = await pool.query(
      'SELECT import_id,campaign_id,filename,status,total_rows,valid_rows,invalid_rows,duplicate_rows,last_processed_row,created_at,updated_at FROM lead_imports WHERE campaign_id=$1 AND tenant_id=$2 ORDER BY created_at DESC LIMIT $3 OFFSET $4',
      [req.params.id, req.user.tenant_id, limit, offset]
    );
    res.json(r.rows);
  } catch (e) { throw e; }
});

app.get('/campaigns/:id/leads/imports/:importId', requireAuth, requireUUID('id', 'importId'), async (req, res) => {
  try {
    const r = await pool.query(
      `SELECT import_id, campaign_id, filename, status, total_rows, valid_rows, invalid_rows,
              duplicate_rows, last_processed_row, created_at, completed_at
       FROM lead_imports WHERE import_id=$1 AND campaign_id=$2 AND tenant_id=$3`,
      [req.params.importId, req.params.id, req.user.tenant_id]
    );
    if (!r.rows.length) return res.status(404).json({ error: { code: 'NOT_FOUND', message: 'Import not found' } });
    res.json(r.rows[0]);
  } catch (e) { throw e; }
});

app.get('/campaigns/:id/leads', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { status, pipeline_id, search } = req.query;
    const { limit, offset } = parsePage(req.query);
    const conditions = ['campaign_id=$1', 'tenant_id=$2'];
    const vals = [req.params.id, req.user.tenant_id];
    if (status)      { vals.push(status);          conditions.push(`status=$${vals.length}`); }
    if (pipeline_id === 'unassigned') conditions.push('pipeline_id IS NULL');
    else if (pipeline_id) { vals.push(pipeline_id); conditions.push(`pipeline_id=$${vals.length}`); }
    if (search)      { vals.push(`%${search}%`);   conditions.push(`(name ILIKE $${vals.length} OR phone LIKE $${vals.length})`); }
    vals.push(limit); vals.push(offset);
    const r = await pool.query(
      `SELECT * FROM leads WHERE ${conditions.join(' AND ')} ORDER BY score DESC, created_at DESC LIMIT $${vals.length-1} OFFSET $${vals.length}`,
      vals
    );
    res.json(r.rows);
  } catch (e) { throw e; }
});

app.get('/campaigns/:id/leads/stats', requireAuth, requireUUID('id'), async (req, res) => {
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
  } catch (e) { throw e; }
});

app.post('/campaigns/:id/leads/:leadId/assign-pipeline', requireAuth, requireUUID('id', 'leadId'), async (req, res) => {
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
    throw e;
  } finally { client.release(); }
});

app.post('/campaigns/:id/leads/bulk-distribute', requireAuth, requireUUID('id'), async (req, res) => {
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
    throw e;
  } finally { client.release(); }
});

// ═════════════════════════════════════════════════════════════════════════════
// EXECUTION HISTORY
// ═════════════════════════════════════════════════════════════════════════════
// Campaign-level execution events
app.get('/campaigns/:id/execution-events', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { lead_id } = req.query;
    const { limit, offset } = parsePage(req.query);
    const conditions = ['e.campaign_id=$1', 'e.tenant_id=$2'];
    const vals = [req.params.id, req.user.tenant_id];
    if (lead_id) { vals.push(lead_id); conditions.push(`e.lead_id=$${vals.length}`); }
    vals.push(limit); vals.push(offset);
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
  } catch (e) { log.error('exec_events.error', { error: e.message }); throw e; }
});

// Pipeline-level execution events
app.get('/pipelines/:pipelineId/execution-events', requireAuth, requireUUID('pipelineId'), async (req, res) => {
  try {
    const { limit, offset } = parsePage(req.query);
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
  } catch (e) { throw e; }
});

// Lead-level timeline
app.get('/campaigns/:id/leads/:leadId/events', requireAuth, requireUUID('id', 'leadId'), async (req, res) => {
  try {
    const { limit, offset } = parsePage(req.query);
    const r = await pool.query(
      `SELECT event_id, lead_id, campaign_id, pipeline_id, tenant_id,
              event_type, status, message, metadata, created_at
       FROM lead_execution_events WHERE lead_id=$1 AND tenant_id=$2 ORDER BY created_at ASC LIMIT $3 OFFSET $4`,
      [req.params.leadId, req.user.tenant_id, limit, offset]
    );
    res.json(r.rows);
  } catch (e) { throw e; }
});

// ═════════════════════════════════════════════════════════════════════════════
// PIPELINE ROUTES
// ═════════════════════════════════════════════════════════════════════════════
app.get('/pipelines/:pipelineId/leads', requireAuth, requireUUID('pipelineId'), async (req, res) => {
  try {
    const { search } = req.query;
    const { limit, offset } = parsePage(req.query);
    const conditions = ['pipeline_id=$1', 'tenant_id=$2'];
    const vals = [req.params.pipelineId, req.user.tenant_id];
    if (search) { vals.push(`%${search}%`); conditions.push(`(name ILIKE $${vals.length} OR phone LIKE $${vals.length})`); }
    vals.push(limit); vals.push(offset);
    const r = await pool.query(
      `SELECT * FROM leads WHERE ${conditions.join(' AND ')} ORDER BY score DESC LIMIT $${vals.length-1} OFFSET $${vals.length}`,
      vals
    );
    res.json(r.rows);
  } catch (e) { throw e; }
});

app.get('/pipelines/:pipelineId/leads/stats', requireAuth, requireUUID('pipelineId'), async (req, res) => {
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
  } catch (e) { throw e; }
});

// ═════════════════════════════════════════════════════════════════════════════
// ADMIN / TEAM / USER ROUTES
// ═════════════════════════════════════════════════════════════════════════════
const _TENANT_SAFE_COLS = 'tenant_id, name, slug, status, plan, max_users, created_at, updated_at';
app.get('/admin/clients', requireAuth, requireRole('PLATFORM_ADMIN'), async (req, res) => {
  try {
    const { limit, offset } = parsePage(req.query);
    const r = await pool.query(`SELECT ${_TENANT_SAFE_COLS} FROM tenants ORDER BY created_at DESC LIMIT $1 OFFSET $2`, [limit, offset]);
    res.json(r.rows);
  } catch (e) { throw e; }
});

app.get('/admin/clients/:tenantId', requireAuth, requireRole('PLATFORM_ADMIN'), requireUUID('tenantId'), async (req, res) => {
  try {
    const r = await pool.query(`SELECT ${_TENANT_SAFE_COLS} FROM tenants WHERE tenant_id=$1`, [req.params.tenantId]);
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0]);
  } catch (e) { throw e; }
});

app.get('/team', requireAuth, async (req, res) => {
  try {
    const { limit, offset } = parsePage(req.query);
    const r = await pool.query(
      'SELECT user_id, email, name, is_active, created_at FROM users WHERE tenant_id=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3',
      [req.user.tenant_id, limit, offset]
    );
    res.json(r.rows);
  } catch (e) { throw e; }
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
      return res.json({ ...(r.rows[0] || {}), actor_kind: req.user.actor_kind });
    }
    const r = await pool.query('SELECT user_id as id, email, name, tenant_id FROM users WHERE user_id=$1', [req.user.sub]);
    res.json({ ...(r.rows[0] || {}), actor_kind: req.user.actor_kind });
  } catch (e) { throw e; }
});

// ─── Enrichment API ───────────────────────────────────────────────────────────
app.get('/enrichment/providers', requireAuth, (req, res) => {
  res.json(ENRICHMENT_PROVIDERS.map(p => ({
    name: p.name, enabled: p.enabled, priority: p.priority,
    timeout_ms: p.timeout, max_retries: p.maxRetries,
  })));
});

app.get('/campaigns/:id/enrichment-config', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const r = await pool.query('SELECT enrichment_config FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2', [req.params.id, tid]);
    if (!r.rows.length) return res.status(404).json({ error: 'not_found' });
    res.json(r.rows[0].enrichment_config || DEFAULT_ENRICHMENT_CONFIG);
  } catch (e) { throw e; }
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
  } catch (e) { throw e; }
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
  } catch (e) { throw e; }
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
  } catch (e) { throw e; }
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
  } catch (e) { throw e; }
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
  } catch (e) { throw e; }
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
  } catch (e) { throw e; }
});

// ── Active calls for this tenant ───────────────────────────────────────────────
app.get('/dialer/active-calls', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { limit, offset } = parsePage(req.query, { defaultLimit: 50, maxLimit: 200 });
    const { rows } = await pool.query(
      `SELECT ac.call_sid, ac.lead_id, ac.pipeline_id, ac.phone, ac.lead_name,
              ac.language, ac.status, ac.started_at, ac.answered_at, ac.disposition,
              c.name AS campaign_name
       FROM active_calls ac
       LEFT JOIN campaigns c ON c.campaign_id = ac.campaign_id
       WHERE ac.tenant_id = $1 AND ac.ended_at IS NULL
       ORDER BY ac.started_at DESC LIMIT $2 OFFSET $3`,
      [tid, limit, offset]
    );
    res.json(rows);
  } catch (e) { throw e; }
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
  } catch (e) { throw e; }
});

// ── Queue statistics for this tenant ──────────────────────────────────────────
app.get('/recordings/:recordingId/access', requireAuth, async (req, res) => {
  const tid = req.user.tenant_id;
  const secret = process.env.RECORDING_INTERNAL_SECRET || '';
  const mediaGateway = process.env.MEDIA_GATEWAY_HTTP_URL || '';
  if (!secret || !mediaGateway) return res.status(503).json({ error: 'recording_access_unavailable' });
  const { rows } = await pool.query(
    `SELECT recording_id, state, retention_until
     FROM telephony_recordings
     WHERE recording_id=$1 AND tenant_id=$2 AND state IN ('AVAILABLE','RETAINED')`,
    [req.params.recordingId, tid]
  );
  if (!rows.length) return res.status(404).json({ error: 'not_found' });
  const expires = Math.floor(Date.now()/1000) + 300;
  const message = tid + ':' + req.params.recordingId + ':' + expires;
  const signature = createHmac('sha256', secret).update(message).digest('hex');
  const url = new URL('/recordings/' + encodeURIComponent(req.params.recordingId) + '/access', mediaGateway);
  url.searchParams.set('tenant_id', tid);
  try {
    const upstream = await _withTimeout(fetch(url, {
      method:'GET',
      headers:{
        'x-voiceos-recording-expires': String(expires),
        'x-voiceos-recording-signature': signature,
        'x-trace-id': req.traceId,
      },
      signal: AbortSignal.timeout(3000),
    }), 3500, 'recording_access');
    if (!upstream.ok) return res.status(upstream.status === 404 ? 404 : 502).json({ error:'recording_access_failed' });
    const body = await upstream.json();
    return res.json({ recording_id: body.recording_id, expires_at: body.expires_at, url: body.url });
  } catch (e) {
    log.warn('recording.access_failed', { recording_id:req.params.recordingId, trace_id:req.traceId, error:e.message });
    return res.status(502).json({ error:'recording_access_failed', trace_id:req.traceId });
  }
});

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
  } catch (e) { throw e; }
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
  const startedAt = process.hrtime.bigint();
  let webhookOutcome = 'failure';
  let webhookState = 'unknown';
  const client = await pool.connect();
  try {
    const twilioAuthToken = process.env.TWILIO_AUTH_TOKEN;
    if (twilioAuthToken) {
      const signature = req.headers['x-twilio-signature'] || '';
      const proto = req.headers['x-forwarded-proto'] || 'https';
      const host = req.headers['x-forwarded-host'] || req.headers.host;
      const fullUrl = `${proto}://${host}${req.originalUrl}`;
      if (!twilio.validateRequest(twilioAuthToken, signature, fullUrl, req.body)) { webhookOutcome='spoofed'; return res.sendStatus(403); }
    } else if (process.env.NODE_ENV === 'production') {
      webhookOutcome='configuration_failure'; return res.sendStatus(503);
    }

    const { CallSid, CallStatus, Duration, AnsweredBy } = req.body || {};
    if (!CallSid || !CallStatus) { webhookOutcome='malformed'; return res.sendStatus(400); }

    await client.query('BEGIN');
    await client.query("SET LOCAL statement_timeout='5000ms'");
    const result = await client.query(
      `SELECT attempt_id, tenant_id, campaign_id, lead_id, pipeline_id, status, initiated_at
       FROM call_attempts WHERE call_sid=$1 FOR UPDATE`, [CallSid]);
    if (!result.rows.length) {
      await client.query('ROLLBACK');
      webhookOutcome='unknown_call'; return res.sendStatus(404);
    }
    const attempt = result.rows[0];

    if ((req.query.tenant_id && req.query.tenant_id !== String(attempt.tenant_id)) ||
        (req.query.lead_id && req.query.lead_id !== String(attempt.lead_id)) ||
        (req.query.pipeline_id && req.query.pipeline_id !== String(attempt.pipeline_id))) {
      await client.query('ROLLBACK');
      webhookOutcome='tenant_mismatch'; return res.sendStatus(403);
    }

    const next = normalizeProviderStatus(CallStatus, AnsweredBy);
    if (!next) {
      await client.query('ROLLBACK');
      webhookOutcome='unknown_status'; return res.sendStatus(400);
    }

    const current = attempt.status === 'IN_PROGRESS' ? 'CONNECTED' :
      attempt.status === 'INITIATED' ? 'DIALING' : attempt.status;
    if (!canTransition(current, next)) {
      await client.query('ROLLBACK');
      webhookOutcome='out_of_order'; webhookState=current;
      log.info('dialer.callback.out_of_order_suppressed', { call_sid: CallSid, current_state: current, provider_status: CallStatus, trace_id:req.traceId });
      return res.sendStatus(200);
    }

    const terminal = ['COMPLETED','BUSY','NO_ANSWER','FAILED','CANCELLED','TIMEOUT','VOICEMAIL'].includes(next);
    webhookState=next;
    _incMetric('voiceos_telephony_calls_by_state_total', { state: next });
    if (next === 'CONNECTED' && attempt.status !== 'IN_PROGRESS') {
      const initiatedMs = attempt.initiated_at ? new Date(attempt.initiated_at).getTime() : NaN;
      if (Number.isFinite(initiatedMs)) {
        _incMetric('voiceos_telephony_call_setup_seconds_sum', {}, Math.max(0, (Date.now()-initiatedMs)/1000));
        _incMetric('voiceos_telephony_call_setup_seconds_count');
      }
    }
    const dbStatus = next === 'CONNECTED' ? 'IN_PROGRESS' : next;
    const duration = Duration == null ? null : Math.max(0, parseInt(Duration) || 0);

    await client.query(
      `UPDATE call_attempts SET status=$2,
         disposition=CASE WHEN $3 THEN $2 ELSE disposition END,
         duration_s=CASE WHEN $4::int IS NULL THEN duration_s ELSE $4::int END,
         answered_at=CASE WHEN $2='IN_PROGRESS' AND answered_at IS NULL THEN NOW() ELSE answered_at END,
         ended_at=CASE WHEN $3 THEN NOW() ELSE ended_at END
       WHERE attempt_id=$1`,
      [attempt.attempt_id, dbStatus, terminal, duration]);

    const canonicalEvent = buildCanonicalCallEvent({
      tenantId: attempt.tenant_id,
      campaignId: attempt.campaign_id,
      leadId: attempt.lead_id,
      callId: CallSid,
      callAttemptId: attempt.attempt_id,
      callSid: CallSid,
      lifecycleState: next,
      outcome: terminal ? next : null,
      durationSeconds: duration,
      eventTimestamp: new Date().toISOString(),
      correlationId: req.traceId,
    });
    const canonicalPersist = await persistCanonicalCallEvent(client, {
      ...canonicalEvent,
      provider: 'twilio',
    });
    if (canonicalPersist.duplicate) {
      _incMetric('voiceos_telephony_canonical_event_duplicates_total');
    } else {
      _incMetric('voiceos_telephony_canonical_events_created_total', { event_type: canonicalEvent.eventType });
    }
    await client.query(
      `UPDATE active_calls SET status=$2,
         answered_at=CASE WHEN $2='IN_PROGRESS' AND answered_at IS NULL THEN NOW() ELSE answered_at END,
         ended_at=CASE WHEN $3 THEN NOW() ELSE ended_at END,
         duration_seconds=CASE WHEN $4::int IS NULL THEN duration_seconds ELSE $4::int END,
         disposition=CASE WHEN $3 THEN $5 ELSE disposition END
       WHERE call_sid=$1 AND tenant_id=$6`,
      [CallSid, dbStatus, terminal, duration, next, attempt.tenant_id]);

    if (terminal) {
      const idempKey = `twilio_callback:${CallSid}:${CallStatus}`;
      const idem = await client.query(
        `INSERT INTO idempotency_keys (key, tenant_id, resource_type, expires_at)
         VALUES ($1,$2,'twilio_callback',NOW()+INTERVAL '24 hours')
         ON CONFLICT (key) DO NOTHING RETURNING key`,
        [idempKey, attempt.tenant_id]);
      if (!idem.rows.length) {
        _incMetric('voiceos_telephony_webhook_duplicates_total');
      }
      if (idem.rows.length) {
        const payload = JSON.stringify({
          callSid: CallSid, attemptId: attempt.attempt_id, disposition: next,
          durationS: duration || 0, endedAt: new Date().toISOString(),
          pipelineId: attempt.pipeline_id, tenantId: attempt.tenant_id,
          leadId: attempt.lead_id, answeredBy: AnsweredBy,
        });
        await _withTimeout(redis.lpush(`voiceos:pipeline:completed:${attempt.pipeline_id}`, payload), 3000, 'redis_completion');
        await _withTimeout(redis.expire(`voiceos:pipeline:completed:${attempt.pipeline_id}`, 120), 3000, 'redis_expiry');
      }
    }

    await client.query('COMMIT');
    if (webhookOutcome === 'failure') webhookOutcome='processed';
    return res.sendStatus(200);
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    webhookOutcome='failure';
    _incMetric('voiceos_telephony_webhook_failures_total', { reason: e.message.includes('timeout') ? 'timeout' : 'processing' });
    log.error('dialer.callback_error', { error: e.message, trace_id:req.traceId });
    return res.sendStatus(500);
  } finally {
    const latencySeconds=Number(process.hrtime.bigint()-startedAt)/1e9;
    _incMetric('voiceos_telephony_webhook_events_total',{outcome:webhookOutcome,state:webhookState});
    const callbackMetricOutcome = new Set(['processed','spoofed','configuration_failure','malformed','unknown_call','tenant_mismatch','unknown_status','out_of_order','failure']).has(webhookOutcome)
      ? webhookOutcome
      : 'failure';
    _incMetric('voiceos_telephony_callback_events_total', { outcome: callbackMetricOutcome });
    _incMetric('voiceos_telephony_webhook_processing_seconds_sum',{},latencySeconds);
    _incMetric('voiceos_telephony_webhook_processing_seconds_count');
    log.info('dialer.callback.completed',{outcome:webhookOutcome,state:webhookState,latency_ms:Math.round(latencySeconds*1000),trace_id:req.traceId});
    client.release();
  }
});

// ── Schedule callback (customer requests callback at future time) ──────────────
app.post('/campaigns/:id/leads/:leadId/schedule-callback', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { callback_at, callback_timezone } = req.body || {};
    if (!callback_at) return res.status(400).json({ error: 'callback_at required' });

    const { rows } = await pool.query(
      `SELECT l.lead_id, l.campaign_id, l.pipeline_id, l.phone, l.name, l.language, l.score,
              c.tenant_id AS campaign_tenant_id, c.timezone AS campaign_timezone,
              c.daily_start_hour, c.daily_end_hour, c.allowed_weekdays, c.excluded_dates,
              t.timezone AS tenant_timezone
       FROM leads l
       JOIN campaigns c ON c.campaign_id=l.campaign_id AND c.tenant_id=$1
       JOIN tenants t ON t.tenant_id=$1
       WHERE l.lead_id=$2 AND l.campaign_id=$3 AND l.tenant_id=$1`,
      [tid, req.params.leadId, req.params.id]
    );
    if (!rows.length) return res.status(404).json({ error: 'not_found' });
    const lead = rows[0];

    const timezone = callback_timezone || lead.campaign_timezone || lead.tenant_timezone;
    if (!validTimezone(timezone)) {
      return res.status(400).json({ error: 'invalid_or_missing_timezone' });
    }

    let callbackDate;
    try {
      callbackDate = parseCallbackInstant(callback_at, timezone);
    } catch (e) {
      return res.status(400).json({ error: 'invalid_callback_datetime', detail: e.message });
    }
    if (callbackDate.getTime() <= Date.now()) {
      return res.status(400).json({ error: 'callback_at must be a future datetime' });
    }

    const working = evaluateWorkingHours(callbackDate, timezone, {
      dailyStartHour: lead.daily_start_hour,
      dailyEndHour: lead.daily_end_hour,
      allowedWeekdays: lead.allowed_weekdays,
      excludedDates: lead.excluded_dates,
    });
    if (!working.allowed) {
      return res.status(400).json({ error: 'callback_outside_calling_window', reason: working.reason, timezone, local_date: working.localDate, local_hour: working.localHour });
    }

    const callbackMs = callbackDate.getTime();
    const payload = {
      lead_id: lead.lead_id, campaign_id: lead.campaign_id,
      pipeline_id: lead.pipeline_id, tenant_id: lead.campaign_tenant_id,
      phone: lead.phone, name: lead.name, language: lead.language,
      score: lead.score, queued_at: new Date().toISOString(),
      _callback: true, _callback_at: callbackDate.toISOString(),
      _callback_timezone: timezone,
    };
    await redis.zadd(`voiceos:callback_calls:${tid}`, callbackMs, JSON.stringify(payload));
    _incMetric('voiceos_telephony_callback_events_total', { outcome: 'scheduled' });
    await pool.query(
      `UPDATE leads SET queue_status='CALLBACK', updated_at=now() WHERE lead_id=$1 AND tenant_id=$2`,
      [lead.lead_id, tid]
    );
    res.json({ ok: true, scheduled_at: callbackDate.toISOString(), timezone, local_date: working.localDate, local_hour: working.localHour });
  } catch (e) { throw e; }
});
// ─── Catch-all ────────────────────────────────────────────────────────────────
app.all('/{*path}', (req, res) => { res.status(404).json({ error: 'not_found' }); });

// ─── Phase 10a: Global error handler ─────────────────────────────────────────
// Receives errors thrown by route handlers (Express 5 catches async rejections).
// Also handles Express built-in errors (413 payload too large, 400 bad JSON, etc.).
// eslint-disable-next-line no-unused-vars
app.use((err, req, res, _next) => {
  if (res.headersSent) return;
  const status = err.status || err.statusCode || 500;
  log.error('bff.error', {
    error:    err.message,
    type:     err.type,
    status,
    method:   req.method,
    path:     req.path,
    trace_id: req.traceId,
  });
  const body = status < 500
    ? { error: err.type || 'bad_request',  trace_id: req.traceId }
    : { error: 'server_error',             trace_id: req.traceId };
  res.status(status).json(body);
});

if (require.main === module) {
  const server = app.listen(PORT, () => {
    log.info('bff.started', { port: PORT });
  });

  process.on('SIGTERM', () => {
    log.info('bff.sigterm', { event: 'drain_started' });
    server.close(() => {
      pool.end(() => {
        redis.disconnect();
        process.exit(0);
      });
    });
    setTimeout(() => {
      log.error('bff.sigterm_timeout', { event: 'force_exit' });
      process.exit(1);
    }, 30000);
  });
}

module.exports = { app, pool, redis };
