'use strict';
const express      = require('express');
const { Pool }     = require('pg');
const jwt          = require('jsonwebtoken');
const bcrypt       = require('bcryptjs');
const cookieParser = require('cookie-parser');
const cors         = require('cors');
const Redis        = require('ioredis');
const twilio       = require('twilio');
const { randomUUID } = require('crypto');

// ─── Config ───────────────────────────────────────────────────────────────────
const app          = express();
const PORT         = parseInt(process.env.PORT) || 8000;
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
  max: parseInt(process.env.PG_POOL_MAX || "10"),
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
pool.on('error', (err) => log.error('pg.idle_client_error', { error: err.message }));

// ─── Redis ────────────────────────────────────────────────────────────────────
const redisOpts = {
  host: process.env.REDIS_HOST || '127.0.0.1',
  port: parseInt(process.env.REDIS_PORT || '6379'),
  lazyConnect: true,
  maxRetriesPerRequest: 1,
};
if (process.env.REDIS_PASSWORD) redisOpts.password = process.env.REDIS_PASSWORD;
const redis = new Redis(redisOpts);
redis.on('error', e => log.warn('redis.error', { error: e.message }));
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

// ─── W10: Security headers ────────────────────────────────────────────────────
app.use((_req, res, next) => {
  res.set('X-Content-Type-Options', 'nosniff');
  res.set('X-Frame-Options', 'DENY');
  res.set('X-XSS-Protection', '0');
  res.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.set('Permissions-Policy', 'geolocation=(), microphone=(), camera=()');
  res.set(
    'Content-Security-Policy',
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
  );
  if (process.env.VOICEOS_ENVIRONMENT === 'production' || process.env.NODE_ENV === 'production') {
    res.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains; preload');
  }
  next();
});

// ─── W10: Internal service auth (BFF → webapi) ──────────────────────────────
// Any outbound request from BFF to webapi includes X-Internal-Token so webapi
// can reject spoofed internal calls from outside the trust boundary.
const _BFF_INTERNAL_TOKEN = process.env.BFF_INTERNAL_TOKEN || null;
function _makeInternalHeaders(extra) {
  const h = { 'Content-Type': 'application/json', ...extra };
  if (_BFF_INTERNAL_TOKEN) h['X-Internal-Token'] = _BFF_INTERNAL_TOKEN;
  return h;
}

// ─── Phase 9 / 12c: soft JWT parse + JTI revocation check ────────────────────
app.use(async (req, _res, next) => {
  const token = req.cookies?.[SESSION_COOKIE] || req.cookies?.["voiceos_token"] || (req.headers.authorization?.startsWith("Bearer ") ? req.headers.authorization.slice(7) : null);
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
    const _INACTIVE = ['SUSPENDED','CANCELLED','DELETING','DELETED'];
      if (!r.rows.length || _INACTIVE.includes(r.rows[0].status)) {
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
      const isNonProd = process.env.NODE_ENV !== "production";
      return res.json({ ok: true, name, role: platform_role, actor_kind: "platform", ...(isNonProd ? { token, tenant_id: null } : {}) });
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
      const isNonProd2 = process.env.NODE_ENV !== "production";
      return res.json({ ok: true, name, role: "TENANT_ADMIN", actor_kind: "tenant", tenant_id, ...(isNonProd2 ? { token } : {}) });
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
  // Phase 12c: use req.user (set by the global JTI-revocation middleware) so that
  // a revoked session is rejected here just as it is on all requireAuth routes.
  if (!req.user) {
    const hasCookie = !!req.cookies[SESSION_COOKIE];
    return res.status(401).json({ error: hasCookie ? 'invalid_session' : 'no_session' });
  }
  res.json({ ok: true, ...req.user });
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
// PROMETHEUS /metrics
// ═════════════════════════════════════════════════════════════════════════════
const promClient = require('prom-client');
promClient.collectDefaultMetrics({ prefix: 'voiceos_bff_' });

const bffHttpRequestsTotal = new promClient.Counter({
  name: 'voiceos_bff_http_requests_total',
  help: 'Total HTTP requests handled by BFF',
  labelNames: ['method', 'route', 'status'],
});

// Instrument all responses
app.use((req, res, next) => {
  const originalEnd = res.end.bind(res);
  res.end = function(...args) {
    const route = req.route ? req.route.path : req.path;
    bffHttpRequestsTotal.labels(req.method, route, String(res.statusCode)).inc();
    return originalEnd(...args);
  };
  next();
});

app.get('/metrics', async (req, res) => {
  res.set('Content-Type', promClient.register.contentType);
  res.end(await promClient.register.metrics());
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



// ── W8: Analytics Platform ────────────────────────────────────────────────────

// GET /analytics/overview — tenant-wide KPIs for 24h / 7d / 30d windows
app.get('/analytics/overview', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const windows = [
      { label: '24h', pg: "NOW() - INTERVAL '24 hours'" },
      { label: '7d',  pg: "NOW() - INTERVAL '7 days'"  },
      { label: '30d', pg: "NOW() - INTERVAL '30 days'" },
    ];
    const result = {};
    for (const w of windows) {
      const [callR, ptpR, costR, cbR, retR] = await Promise.all([
        pool.query(
          `SELECT
              COUNT(*)                                                       AS total_calls,
              COUNT(*) FILTER (WHERE ca.status = 'COMPLETED')               AS calls_connected,
              COUNT(*) FILTER (WHERE ca.status = 'NO_ANSWER')               AS calls_no_answer,
              COUNT(*) FILTER (WHERE ca.status IN ('FAILED','BUSY'))         AS calls_failed,
              COALESCE(AVG(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0)::int AS avg_duration_s,
              COALESCE(SUM(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0) AS total_duration_s,
              COUNT(DISTINCT ca.lead_id)                                    AS unique_leads_called,
              COUNT(DISTINCT ca.campaign_id)                                AS active_campaigns
             FROM call_attempts ca
            WHERE ca.tenant_id = $1 AND ca.initiated_at > ${w.pg}`,
          [tid]
        ),
        pool.query(
          `SELECT
              COUNT(*) AS ptp_count,
              COALESCE(SUM(ptp.promised_amount_minor),0) AS amount_pending_minor,
              COALESCE(SUM(ptp.promised_amount_minor) FILTER (WHERE ptp.status='FULFILLED'),0) AS amount_collected_minor
             FROM promises_to_pay ptp
            WHERE ptp.tenant_id=$1 AND ptp.recorded_at > ${w.pg}`,
          [tid]
        ),
        pool.query(
          `SELECT COALESCE(SUM(ue.total_cost_minor),0) AS total_cost_minor
             FROM usage_events ue
            WHERE ue.tenant_id=$1 AND ue.created_at > ${w.pg}`,
          [tid]
        ),
        pool.query(
          `SELECT COUNT(DISTINCT lee.lead_id) AS callback_count
             FROM lead_execution_events lee
            WHERE lee.tenant_id=$1
              AND lee.event_type IN ('CALLBACK_SCHEDULED','CALL_DEFERRED')
              AND lee.created_at > ${w.pg}`,
          [tid]
        ),
        pool.query(
          `SELECT COUNT(DISTINCT lead_id) AS retry_leads
             FROM (
               SELECT lead_id FROM call_attempts
                WHERE tenant_id=$1 AND initiated_at > ${w.pg}
                GROUP BY lead_id HAVING COUNT(*) > 1
             ) t`,
          [tid]
        ),
      ]);
      const d   = callR.rows[0];
      const p   = ptpR.rows[0];
      const c   = costR.rows[0];
      const total       = parseInt(d.total_calls)         || 0;
      const connected   = parseInt(d.calls_connected)     || 0;
      const ptps        = parseInt(p.ptp_count)           || 0;
      const cost        = parseInt(c.total_cost_minor)    || 0;
      const callbacks   = parseInt(cbR.rows[0].callback_count) || 0;
      const retryLeads  = parseInt(retR.rows[0].retry_leads)   || 0;
      const uniqueLeads = parseInt(d.unique_leads_called) || 0;
      result[w.label] = {
        total_calls:                   total,
        calls_connected:               connected,
        calls_no_answer:               parseInt(d.calls_no_answer)  || 0,
        calls_failed:                  parseInt(d.calls_failed)     || 0,
        avg_duration_s:                parseInt(d.avg_duration_s)   || 0,
        total_duration_s:              parseInt(d.total_duration_s) || 0,
        unique_leads_called:           uniqueLeads,
        active_campaigns:              parseInt(d.active_campaigns) || 0,
        answer_rate_pct:               total > 0 ? Math.round((connected / total) * 1000) / 10 : 0,
        ptp_count:                     ptps,
        ptp_rate_pct:                  connected > 0 ? Math.round((ptps / connected) * 1000) / 10 : 0,
        amount_collected_minor:        parseInt(p.amount_collected_minor) || 0,
        amount_pending_minor:          parseInt(p.amount_pending_minor)   || 0,
        total_cost_minor:              cost,
        cost_per_connected_call_minor: connected > 0 ? Math.round(cost / connected) : 0,
        cost_per_ptp_minor:            ptps > 0 ? Math.round(cost / ptps) : 0,
        callback_count:                callbacks,
        callback_rate_pct:             connected > 0 ? Math.round((callbacks / connected) * 1000) / 10 : 0,
        retry_leads:                   retryLeads,
        retry_rate_pct:                uniqueLeads > 0 ? Math.round((retryLeads / uniqueLeads) * 1000) / 10 : 0,
      };
    }
    res.json(result);
  } catch (e) { throw e; }
});

// GET /analytics/time-series?days=30 — daily breakdown for charts
app.get('/analytics/time-series', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const days = Math.min(Math.max(parseInt(req.query.days) || 30, 1), 90);
    const { rows } = await pool.query(
      `SELECT
          (ca.initiated_at AT TIME ZONE 'Asia/Kolkata')::date          AS day,
          COUNT(*)                                                      AS total_calls,
          COUNT(*) FILTER (WHERE ca.status = 'COMPLETED')              AS calls_connected,
          COUNT(*) FILTER (WHERE ca.status = 'NO_ANSWER')              AS calls_no_answer,
          COUNT(*) FILTER (WHERE ca.status IN ('FAILED','BUSY'))        AS calls_failed,
          COALESCE(AVG(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0)::int AS avg_duration_s,
          COALESCE(SUM(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0) AS total_duration_s,
          COUNT(DISTINCT ca.lead_id)                                   AS unique_leads
         FROM call_attempts ca
        WHERE ca.tenant_id = $1
          AND ca.initiated_at > NOW() - ($2 || ' days')::interval
        GROUP BY 1 ORDER BY 1 ASC`,
      [tid, days]
    );
    const { rows: ptpRows } = await pool.query(
      `SELECT
          (lee.created_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
          COUNT(*) AS ptp_count
         FROM lead_execution_events lee
        WHERE lee.tenant_id=$1 AND lee.event_type='PTP_RECORDED'
          AND lee.created_at > NOW() - ($2 || ' days')::interval
        GROUP BY 1`,
      [tid, days]
    );
    const ptpMap = Object.fromEntries(ptpRows.map(r => [String(r.day), parseInt(r.ptp_count)]));
    res.json(rows.map(r => {
      const total = parseInt(r.total_calls) || 0;
      const conn  = parseInt(r.calls_connected) || 0;
      const ptps  = ptpMap[String(r.day)] || 0;
      return {
        day:              r.day,
        total_calls:      total,
        calls_connected:  conn,
        calls_no_answer:  parseInt(r.calls_no_answer) || 0,
        calls_failed:     parseInt(r.calls_failed) || 0,
        avg_duration_s:   parseInt(r.avg_duration_s) || 0,
        total_duration_s: parseInt(r.total_duration_s) || 0,
        unique_leads:     parseInt(r.unique_leads) || 0,
        ptp_count:        ptps,
        answer_rate_pct:  total > 0 ? Math.round((conn  / total) * 1000) / 10 : 0,
        ptp_rate_pct:     conn  > 0 ? Math.round((ptps  / conn)  * 1000) / 10 : 0,
      };
    }));
  } catch (e) { throw e; }
});

// GET /analytics/campaigns — per-campaign analytics
app.get('/analytics/campaigns', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { rows } = await pool.query(
      `SELECT
          c.campaign_id,
          c.name         AS campaign_name,
          c.status       AS campaign_status,
          c.created_at,
          COUNT(ca.attempt_id)                                               AS total_calls,
          COUNT(ca.attempt_id) FILTER (WHERE ca.status='COMPLETED')         AS calls_connected,
          COUNT(ca.attempt_id) FILTER (WHERE ca.status='NO_ANSWER')         AS calls_no_answer,
          COUNT(ca.attempt_id) FILTER (WHERE ca.status IN ('FAILED','BUSY')) AS calls_failed,
          COALESCE(AVG(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0)::int AS avg_duration_s,
          COALESCE(SUM(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0)      AS total_duration_s,
          COUNT(DISTINCT ca.lead_id)                                         AS unique_leads_called,
          (SELECT COUNT(*) FROM leads l
            WHERE l.campaign_id=c.campaign_id AND l.tenant_id=$1)            AS total_leads,
          (SELECT COUNT(*) FROM lead_execution_events lee
            WHERE lee.campaign_id=c.campaign_id AND lee.tenant_id=$1
              AND lee.event_type='PTP_RECORDED')                              AS ptp_count,
          (SELECT COUNT(DISTINCT lee2.lead_id) FROM lead_execution_events lee2
            WHERE lee2.campaign_id=c.campaign_id AND lee2.tenant_id=$1
              AND lee2.event_type IN ('CALLBACK_SCHEDULED','CALL_DEFERRED'))  AS callback_count,
          (SELECT COUNT(DISTINCT t.lead_id)
            FROM (SELECT lead_id FROM call_attempts ca2i
                  WHERE ca2i.campaign_id=c.campaign_id AND ca2i.tenant_id=$1
                  GROUP BY lead_id HAVING COUNT(*)>1) t)                      AS retry_leads
         FROM campaigns c
         LEFT JOIN call_attempts ca ON ca.campaign_id=c.campaign_id AND ca.tenant_id=$1
        WHERE c.tenant_id=$1
        GROUP BY c.campaign_id, c.name, c.status, c.created_at
        ORDER BY total_calls DESC NULLS LAST, c.name ASC
        LIMIT 50`,
      [tid]
    );
    res.json(rows.map(r => {
      const total = parseInt(r.total_calls)         || 0;
      const conn  = parseInt(r.calls_connected)     || 0;
      const ptps  = parseInt(r.ptp_count)           || 0;
      const cbs   = parseInt(r.callback_count)      || 0;
      const retry = parseInt(r.retry_leads)         || 0;
      const uniq  = parseInt(r.unique_leads_called) || 0;
      return {
        campaign_id:         r.campaign_id,
        campaign_name:       r.campaign_name,
        campaign_status:     r.campaign_status,
        created_at:          r.created_at,
        total_leads:         parseInt(r.total_leads) || 0,
        total_calls:         total,
        calls_connected:     conn,
        calls_no_answer:     parseInt(r.calls_no_answer) || 0,
        calls_failed:        parseInt(r.calls_failed)    || 0,
        unique_leads_called: uniq,
        avg_duration_s:      parseInt(r.avg_duration_s)  || 0,
        total_duration_s:    parseInt(r.total_duration_s)|| 0,
        answer_rate_pct:     total > 0 ? Math.round((conn  / total) * 1000) / 10 : 0,
        ptp_count:           ptps,
        ptp_rate_pct:        conn  > 0 ? Math.round((ptps  / conn)  * 1000) / 10 : 0,
        callback_count:      cbs,
        callback_rate_pct:   conn  > 0 ? Math.round((cbs   / conn)  * 1000) / 10 : 0,
        retry_leads:         retry,
        retry_rate_pct:      uniq  > 0 ? Math.round((retry / uniq)  * 1000) / 10 : 0,
      };
    }));
  } catch (e) { throw e; }
});

// GET /analytics/export?days=30&format=csv — download analytics report
app.get('/analytics/export', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const days = Math.min(Math.max(parseInt(req.query.days) || 30, 1), 365);
    const fmt  = String(req.query.format || 'csv').toLowerCase();
    if (!['csv', 'json'].includes(fmt)) return res.status(422).json({ error: 'format must be csv or json' });
    const { rows } = await pool.query(
      `SELECT
          (ca.initiated_at AT TIME ZONE 'Asia/Kolkata')::date          AS day,
          c.name                                                        AS campaign_name,
          ca.campaign_id,
          COUNT(*)                                                      AS total_calls,
          COUNT(*) FILTER (WHERE ca.status='COMPLETED')                AS calls_connected,
          COUNT(*) FILTER (WHERE ca.status='NO_ANSWER')                AS calls_no_answer,
          COUNT(*) FILTER (WHERE ca.status IN ('FAILED','BUSY'))        AS calls_failed,
          COALESCE(SUM(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0) AS total_duration_s,
          COALESCE(AVG(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0)::int AS avg_duration_s
         FROM call_attempts ca
         LEFT JOIN campaigns c ON c.campaign_id=ca.campaign_id
        WHERE ca.tenant_id=$1 AND ca.initiated_at > NOW() - ($2 || ' days')::interval
        GROUP BY 1,2,3 ORDER BY 1 DESC, 2 ASC`,
      [tid, days]
    );
    if (fmt === 'json') return res.json(rows);
    const cols = ['day','campaign_name','campaign_id','total_calls','calls_connected',
                  'calls_no_answer','calls_failed','total_duration_s','avg_duration_s'];
    const lines = [cols.join(','), ...rows.map(r => cols.map(c => String(r[c] ?? '')).join(','))];
    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="voiceos-analytics-${days}d.csv"`);
    return res.send(lines.join('\n'));
  } catch (e) { throw e; }
});

// POST /analytics/aggregate — compute & upsert analytics_daily rollup for a day
app.post('/analytics/aggregate', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { day, campaign_id } = req.body || {};
    const targetDay = day || new Date().toISOString().slice(0, 10);

    const statsQ = await pool.query(
      `SELECT
          COUNT(*) FILTER (WHERE ca.status='COMPLETED')                           AS calls_completed,
          COALESCE(AVG(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0)::bigint*1000 AS avg_duration_ms,
          COALESCE(SUM(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0)    AS total_duration_s,
          COUNT(*)                                                                AS total_calls,
          COUNT(*) FILTER (WHERE ca.status NOT IN ('FAILED','BUSY'))             AS contactable
         FROM call_attempts ca
        WHERE ca.tenant_id=$1
          AND (ca.initiated_at AT TIME ZONE 'Asia/Kolkata')::date = $2::date
          AND ($3::uuid IS NULL OR ca.campaign_id=$3::uuid)`,
      [tid, targetDay, campaign_id || null]
    );
    const ptpQ = await pool.query(
      `SELECT
          COUNT(*) AS ptp_count,
          COALESCE(SUM(ptp.promised_amount_minor) FILTER (WHERE ptp.status='FULFILLED'),0) AS amount_collected_minor
         FROM promises_to_pay ptp
        WHERE ptp.tenant_id=$1
          AND (ptp.recorded_at AT TIME ZONE 'Asia/Kolkata')::date = $2::date`,
      [tid, targetDay]
    );
    const s = statsQ.rows[0], p = ptpQ.rows[0];
    const completed = parseInt(s.calls_completed) || 0;
    const total     = parseInt(s.total_calls)     || 0;
    const contactable = parseInt(s.contactable)   || 0;
    const ptps      = parseInt(p.ptp_count)        || 0;

    const conflictClause = campaign_id
      ? '(tenant_id, day, campaign_id) WHERE campaign_id IS NOT NULL'
      : '(tenant_id, day) WHERE campaign_id IS NULL';

    const upsert = await pool.query(
      `INSERT INTO analytics_daily
           (tenant_id, day, campaign_id,
            calls_completed, ptp_count, ptp_rate,
            avg_duration_ms, amount_collected_minor,
            contactability_rate, recovery_rate, avg_dpd, computed_at)
         VALUES ($1,$2::date,$3::uuid, $4,$5,$6, $7,$8, $9,0,0, NOW())
         ON CONFLICT ${conflictClause}
         DO UPDATE SET
           calls_completed       = EXCLUDED.calls_completed,
           ptp_count             = EXCLUDED.ptp_count,
           ptp_rate              = EXCLUDED.ptp_rate,
           avg_duration_ms       = EXCLUDED.avg_duration_ms,
           amount_collected_minor= EXCLUDED.amount_collected_minor,
           contactability_rate   = EXCLUDED.contactability_rate,
           computed_at           = NOW()
         RETURNING *`,
      [
        tid, targetDay, campaign_id || null,
        completed, ptps, completed > 0 ? ptps / completed : 0,
        parseInt(s.avg_duration_ms) || 0,
        parseInt(p.amount_collected_minor) || 0,
        total > 0 ? contactable / total : 0,
      ]
    );
    // Log to report_runs
    await pool.query(
      'INSERT INTO report_runs (tenant_id,report_day,campaign_id) VALUES ($1,$2::date,$3) ON CONFLICT DO NOTHING',
      [tid, targetDay, campaign_id || null]
    );
    await bffAudit(pool, { req, action: 'analytics.aggregate', resourceType: 'analytics', resourceId: targetDay });
    res.json({ ok: true, day: targetDay, rollup: upsert.rows[0] || null });
  } catch (e) { throw e; }
});

// ─── Analytics API ────────────────────────────────────────────────────────────
app.get('/analytics/campaigns/:id/summary', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const cid = req.params.id;
    const { rows } = await pool.query(
      `SELECT
          COUNT(*)                                                       AS total_calls,
          COUNT(*) FILTER (WHERE ca.status = 'COMPLETED')               AS calls_connected,
          COUNT(*) FILTER (WHERE ca.status = 'NO_ANSWER')               AS calls_no_answer,
          COUNT(*) FILTER (WHERE ca.status = 'FAILED')                  AS calls_failed,
          COALESCE(AVG(ca.duration_s) FILTER (WHERE ca.status='COMPLETED'),0)::int AS avg_duration_s,
          COUNT(DISTINCT ca.lead_id)                                    AS unique_leads_called,
          (SELECT COUNT(*) FROM leads l
            WHERE l.campaign_id=$2 AND l.tenant_id=$1)                  AS total_leads,
          (SELECT COUNT(*) FROM lead_execution_events lee
            WHERE lee.campaign_id=$2 AND lee.tenant_id=$1
              AND lee.event_type='PTP_RECORDED')                         AS ptp_count,
          (SELECT COUNT(DISTINCT lee2.lead_id) FROM lead_execution_events lee2
            WHERE lee2.campaign_id=$2 AND lee2.tenant_id=$1
              AND lee2.event_type IN ('CALLBACK_SCHEDULED','CALL_DEFERRED')) AS callback_count,
          (SELECT COUNT(DISTINCT lead_id)
            FROM (SELECT lead_id FROM call_attempts ca2
                  WHERE ca2.campaign_id=$2 AND ca2.tenant_id=$1
                  GROUP BY lead_id HAVING COUNT(*)>1) t)                AS retry_leads,
          (SELECT COALESCE(SUM(ptp.promised_amount_minor),0)
            FROM promises_to_pay ptp
            WHERE ptp.tenant_id=$1
              AND ptp.call_id IN (
                SELECT attempt_id FROM call_attempts WHERE campaign_id=$2 AND tenant_id=$1
              )
              AND ptp.status='FULFILLED')                                AS amount_collected_minor
        FROM call_attempts ca
       WHERE ca.tenant_id=$1 AND ca.campaign_id=$2`,
      [tid, cid]
    );
    const r           = rows[0];
    const total       = parseInt(r.total_calls)         || 0;
    const connected   = parseInt(r.calls_connected)     || 0;
    const ptps        = parseInt(r.ptp_count)           || 0;
    const callbacks   = parseInt(r.callback_count)      || 0;
    const retryLeads  = parseInt(r.retry_leads)         || 0;
    const uniqueLeads = parseInt(r.unique_leads_called) || 0;
    res.json({
      total_calls:             total,
      calls_connected:         connected,
      calls_no_answer:         parseInt(r.calls_no_answer)  || 0,
      calls_failed:            parseInt(r.calls_failed)     || 0,
      total_leads:             parseInt(r.total_leads)      || 0,
      unique_leads_called:     uniqueLeads,
      avg_duration_s:          parseInt(r.avg_duration_s)   || 0,
      ptp_count:               ptps,
      ptp_rate:                connected > 0 ? ptps / connected : 0,
      contactability_rate:     total > 0 ? connected / total : 0,
      conversion_rate:         total > 0 ? ptps / total : 0,
      answer_rate_pct:         total > 0 ? Math.round((connected / total) * 1000) / 10 : 0,
      ptp_rate_pct:            connected > 0 ? Math.round((ptps  / connected) * 1000) / 10 : 0,
      callback_count:          callbacks,
      callback_rate_pct:       connected > 0 ? Math.round((callbacks  / connected) * 1000) / 10 : 0,
      retry_leads:             retryLeads,
      retry_rate_pct:          uniqueLeads > 0 ? Math.round((retryLeads / uniqueLeads) * 1000) / 10 : 0,
      amount_collected_minor:  parseInt(r.amount_collected_minor) || 0,
    });
  } catch (e) { throw e; }
});


// ── W9: Compliance + Governance ───────────────────────────────────────────────

// GET /compliance/dnc?limit=100&offset=0&search=<phone>
app.get('/compliance/dnc', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const lim = Math.min(parseInt(req.query.limit) || 100, 500);
    const off = parseInt(req.query.offset) || 0;
    const search = req.query.search ? String(req.query.search).replace(/[%_]/g, '\\$&') : null;
    const params = [tid];
    let where = '(tenant_id=$1 OR tenant_id IS NULL)';
    if (search) { params.push('%' + search + '%'); where += ' AND phone LIKE $' + params.length; }
    const { rows } = await pool.query(
      `SELECT dnc_id, phone, source, reason, added_at, expires_at,
              tenant_id IS NULL AS is_global
         FROM dnc_numbers WHERE ${where}
        ORDER BY added_at DESC
        LIMIT $${params.length + 1} OFFSET $${params.length + 2}`,
      [...params, lim, off]
    );
    const { rows: cnt } = await pool.query(
      'SELECT COUNT(*) AS total FROM dnc_numbers WHERE (tenant_id=$1 OR tenant_id IS NULL)', [tid]
    );
    res.json({ total: parseInt(cnt[0].total) || 0, items: rows });
  } catch (e) { throw e; }
});

// POST /compliance/dnc — add phone to DNC
app.post('/compliance/dnc', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { phone, reason, source = 'manual', expires_at } = req.body || {};
    if (!phone || typeof phone !== 'string') return res.status(422).json({ error: 'phone required' });
    const normalized = String(phone).replace(/\D/g, '').replace(/^91/, '').slice(-10);
    if (normalized.length < 10) return res.status(422).json({ error: 'invalid phone number — need 10 digits' });
    const validSources = ['manual','ndnc','trai','tenant_upload','opted_out'];
    if (!validSources.includes(source)) return res.status(422).json({ error: 'invalid source' });
    const { rows } = await pool.query(
      `INSERT INTO dnc_numbers (phone, tenant_id, source, reason, expires_at)
         VALUES ($1, $2, $3, $4, $5::timestamptz)
         ON CONFLICT (phone, tenant_id) DO UPDATE SET
           source=EXCLUDED.source, reason=EXCLUDED.reason,
           added_at=NOW(), expires_at=EXCLUDED.expires_at
         RETURNING *`,
      [normalized, tid, source, reason || null, expires_at || null]
    );
    // Invalidate Redis DNC cache
    await redis.del(`voiceos:dnc:${normalized}:${tid}`).catch(() => {});
    await bffAudit(pool, { req, action: 'compliance.dnc.add', resourceType: 'dnc', resourceId: normalized });
    res.status(201).json(rows[0]);
  } catch (e) { throw e; }
});

// DELETE /compliance/dnc/:phone — remove from tenant DNC
app.delete('/compliance/dnc/:phone', requireAuth, async (req, res) => {
  try {
    const tid  = req.user.tenant_id;
    const normalized = String(req.params.phone).replace(/\D/g, '').replace(/^91/, '').slice(-10);
    const { rowCount } = await pool.query(
      'DELETE FROM dnc_numbers WHERE phone=$1 AND tenant_id=$2', [normalized, tid]
    );
    if (rowCount === 0) return res.status(404).json({ error: 'not_found' });
    await redis.del(`voiceos:dnc:${normalized}:${tid}`).catch(() => {});
    await bffAudit(pool, { req, action: 'compliance.dnc.remove', resourceType: 'dnc', resourceId: normalized });
    res.json({ ok: true });
  } catch (e) { throw e; }
});

// GET /compliance/violations — active + recent compliance violations
app.get('/compliance/violations', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { rows } = await pool.query(
      `SELECT violation_id, rule_id, signal_summary, status,
              detected_at, resolved_at, redetected_at
         FROM compliance_violations
        WHERE tenant_id=$1
        ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, detected_at DESC
        LIMIT 200`,
      [tid]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// POST /compliance/violations/:id/resolve
app.post('/compliance/violations/:id/resolve', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const vid = req.params.id;
    const { rowCount } = await pool.query(
      `UPDATE compliance_violations
          SET status='RESOLVED', resolved_at=NOW()
        WHERE violation_id=$1 AND tenant_id=$2 AND status='ACTIVE'`,
      [vid, tid]
    );
    if (rowCount === 0) return res.status(404).json({ error: 'violation not found or already resolved' });
    await bffAudit(pool, { req, action: 'compliance.violation.resolve', resourceType: 'compliance_violation', resourceId: vid });
    res.json({ ok: true });
  } catch (e) { throw e; }
});

// GET /compliance/report — compliance health summary
app.get('/compliance/report', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const [dncR, violR, auditR, oohR] = await Promise.all([
      pool.query(
        'SELECT COUNT(*) AS total FROM dnc_numbers WHERE tenant_id=$1 OR tenant_id IS NULL', [tid]
      ),
      pool.query(
        `SELECT
            COUNT(*) FILTER (WHERE status='ACTIVE')   AS active_count,
            COUNT(*) FILTER (WHERE status='RESOLVED') AS resolved_count,
            MAX(detected_at) FILTER (WHERE status='ACTIVE') AS last_active_at
           FROM compliance_violations WHERE tenant_id=$1`,
        [tid]
      ),
      pool.query(
        `SELECT COUNT(*) AS total
           FROM audit_log
          WHERE tenant_id=$1 AND recorded_at > NOW() - INTERVAL '30 days'`,
        [tid]
      ),
      pool.query(
        `SELECT COUNT(DISTINCT ca.attempt_id) AS outside_hours
           FROM call_attempts ca
           JOIN campaigns c ON c.campaign_id=ca.campaign_id
          WHERE ca.tenant_id=$1
            AND ca.initiated_at > NOW() - INTERVAL '30 days'
            AND (
              EXTRACT(HOUR FROM ca.initiated_at AT TIME ZONE COALESCE(c.timezone,'Asia/Kolkata'))
                < COALESCE(c.daily_start_hour, 9)
              OR
              EXTRACT(HOUR FROM ca.initiated_at AT TIME ZONE COALESCE(c.timezone,'Asia/Kolkata'))
                >= COALESCE(c.daily_end_hour, 21)
            )`,
        [tid]
      ),
    ]);
    const v = violR.rows[0];
    res.json({
      dnc_total:                  parseInt(dncR.rows[0].total)    || 0,
      active_violations:          parseInt(v.active_count)        || 0,
      resolved_violations:        parseInt(v.resolved_count)      || 0,
      last_active_violation_at:   v.last_active_at                || null,
      audit_events_30d:           parseInt(auditR.rows[0].total)  || 0,
      out_of_hours_calls_30d:     parseInt(oohR.rows[0].outside_hours) || 0,
      rbi_calling_hours:          '09:00–21:00 IST',
      rbi_calling_hours_enforced: true,
      dpdp_compliant:             parseInt(v.active_count) === 0,
      compliance_score:           Math.max(0, 100 - (parseInt(v.active_count) || 0) * 10),
    });
  } catch (e) { throw e; }
});

// POST /compliance/data-erasure — DPDP Art.12 right-to-erasure
app.post('/compliance/data-erasure', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { customer_id, reason } = req.body || {};
    if (!customer_id) return res.status(422).json({ error: 'customer_id required' });
    // Mark customer record for erasure + revoke all consents in one transaction
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const custR = await client.query(
        `UPDATE customers
            SET data_erasure_requested=TRUE, updated_at=NOW()
          WHERE customer_id=$1::uuid AND tenant_id=$2
          RETURNING customer_id`,
        [customer_id, tid]
      );
      if (custR.rowCount === 0) {
        await client.query('ROLLBACK');
        return res.status(404).json({ error: 'customer not found' });
      }
      // Revoke all active consents
      await client.query(
        `UPDATE consents SET status='REVOKED', revoked_at=NOW(), updated_at=NOW()
          WHERE customer_id=$1::uuid AND tenant_id=$2 AND status='GRANTED'`,
        [customer_id, tid]
      );
      // Add to DNC if phone exists
      const phoneR = await pool.query(
        'SELECT phone FROM leads WHERE crm_customer_id=$1::uuid AND tenant_id=$2 LIMIT 1',
        [customer_id, tid]
      );
      const phone = phoneR.rows.length ? phoneR.rows[0].phone : null;
      if (phone) {
        const norm = String(phone).replace(/\D/g,'').replace(/^91/,'').slice(-10);
        if (norm.length >= 10) {
          await client.query(
            `INSERT INTO dnc_numbers (phone, tenant_id, source, reason)
               VALUES ($1, $2, 'opted_out', $3)
               ON CONFLICT (phone, tenant_id) DO UPDATE SET source='opted_out', reason=EXCLUDED.reason, added_at=NOW()`,
            [norm, tid, reason || 'DPDP Art.12 erasure request']
          );
          await redis.del(`voiceos:dnc:${norm}:${tid}`).catch(() => {});
        }
      }
      await client.query('COMMIT');
    } catch (err) { await client.query('ROLLBACK'); throw err; }
    finally { client.release(); }
    await bffAudit(pool, {
      req, action: 'compliance.data_erasure.requested',
      resourceType: 'customer', resourceId: customer_id,
      metadata: { reason: reason || 'DPDP Art.12' },
    });
    res.status(202).json({
      ok: true,
      customer_id,
      message: 'Erasure queued. PII will be purged within 72 hours per DPDP Art. 12.',
      requested_at: new Date().toISOString(),
    });
  } catch (e) { throw e; }
});

// GET /compliance/consent/:customerId — consent records for a customer
app.get('/compliance/consent/:customerId', requireAuth, requireUUID('customerId'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const cid = req.params.customerId;
    const { rows } = await pool.query(
      `SELECT consent_id, consent_type, status, granted_at, revoked_at, expires_at
         FROM consents
        WHERE tenant_id=$1 AND customer_id=$2::uuid
        ORDER BY created_at DESC`,
      [tid, cid]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// POST /compliance/consent/:customerId/revoke — revoke all consent (opt-out)
app.post('/compliance/consent/:customerId/revoke', requireAuth, requireUUID('customerId'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const cid = req.params.customerId;
    const { reason } = req.body || {};
    const { rowCount } = await pool.query(
      `UPDATE consents SET status='REVOKED', revoked_at=NOW(), updated_at=NOW()
        WHERE tenant_id=$1 AND customer_id=$2::uuid AND status='GRANTED'`,
      [tid, cid]
    );
    // Add to DNC
    const { rows: custRows } = await pool.query(
      'SELECT phone FROM leads WHERE crm_customer_id=$1::uuid AND tenant_id=$2 LIMIT 1',
      [cid, tid]
    );
    if (custRows.length && custRows[0].phone) {
      const norm = String(custRows[0].phone).replace(/\D/g,'').replace(/^91/,'').slice(-10);
      if (norm.length >= 10) {
        await pool.query(
          `INSERT INTO dnc_numbers (phone, tenant_id, source, reason)
             VALUES ($1, $2, 'opted_out', $3)
             ON CONFLICT (phone, tenant_id) DO UPDATE SET
               source='opted_out', reason=EXCLUDED.reason, added_at=NOW()`,
          [norm, tid, reason || 'Consent revoked']
        ).catch(() => {});
        await redis.del(`voiceos:dnc:${norm}:${tid}`).catch(() => {});
      }
    }
    await bffAudit(pool, { req, action: 'compliance.consent.revoke', resourceType: 'customer', resourceId: cid });
    res.json({ ok: true, consents_revoked: rowCount });
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
  try {
    const twilioAuthToken = process.env.TWILIO_AUTH_TOKEN;
    if (twilioAuthToken) {
      const signature = req.headers['x-twilio-signature'] || '';
      const proto = req.headers['x-forwarded-proto'] || 'https';
      const host  = req.headers['x-forwarded-host'] || req.headers.host;
      const fullUrl = `${proto}://${host}${req.originalUrl}`;
      if (!twilio.validateRequest(twilioAuthToken, signature, fullUrl, req.body)) {
        log.warn('dialer.callback.invalid_signature', { ip: req.ip });
        return res.sendStatus(403);
      }
    } else if (process.env.NODE_ENV === 'production') {
      log.error('dialer.callback.no_auth_token', { env: 'production' });
      return res.sendStatus(503);
    } else {
      log.warn('dialer.callback.no_auth_token', { env: 'development' });
    }

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
      // Idempotency guard: Twilio sometimes delivers the same terminal event twice.
      // Use the existing idempotency_keys table (migration 005) to deduplicate.
      const idempKey = `twilio_callback:${CallSid}:${CallStatus}`;
      const existing = await pool.query(
        'SELECT key FROM idempotency_keys WHERE key=$1',
        [idempKey]
      );
      if (existing.rows.length) {
        log.info('dialer.callback.duplicate_suppressed', { call_sid: CallSid, status: CallStatus });
        return res.sendStatus(200);
      }
      await pool.query(
        `INSERT INTO idempotency_keys (key, tenant_id, resource_type, expires_at)
         VALUES ($1, $2::uuid, 'twilio_callback', NOW() + INTERVAL '24 hours')
         ON CONFLICT (key) DO NOTHING`,
        [idempKey, tenantId || '00000000-0000-0000-0000-000000000000']
      );

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
    log.error('dialer.callback_error', { error: e.message });
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
  } catch (e) { throw e; }
});


// ── Team invite + deactivate ──────────────────────────────────────────────────

// POST /team/invite — invite a team member by email
app.post('/team/invite', requireAuth, async (req, res) => {
  try {
    const { email, role_id, scope_type = 'TENANT' } = req.body;
    if (!email || !role_id) {
      return res.status(422).json({ error: 'email and role_id required' });
    }
    const tid = req.user.tenant_id;
    // Verify role belongs to tenant
    const roleCheck = await pool.query(
      'SELECT role_id FROM roles WHERE role_id=$1 AND tenant_id=$2',
      [role_id, tid]
    );
    if (!roleCheck.rows.length) {
      return res.status(422).json({ error: 'unknown role_id' });
    }
    // Ensure no active pending invite for same email
    await pool.query(
      "UPDATE invitations SET status='EXPIRED' WHERE tenant_id=$1 AND email=$2 AND status='PENDING'",
      [tid, email.toLowerCase()]
    );
    // Generate token — raw shown once, only hash persisted
    const rawToken = secrets.token_urlsafe(32);
    const tokenHash = require('crypto').createHash('sha256').update(rawToken).digest('hex');
    const expiresAt = new Date(Date.now() + 72 * 3600 * 1000);
    const { rows } = await pool.query(
      `INSERT INTO invitations
         (tenant_id, email, role_id, org_scope_type, org_scope_id, token_hash, status, invited_by, expires_at)
       VALUES ($1,$2,$3,$4,$5,$6,'PENDING',$7,$8)
       RETURNING invitation_id, email, role_id, status, expires_at`,
      [tid, email.toLowerCase(), role_id, scope_type, tid,
       tokenHash, req.user.sub, expiresAt]
    );
    log.info('team.invite', { tenant_id: tid, email, role_id });
    res.status(201).json({
      ...rows[0],
      invitation_token: rawToken,
      note: 'Share this token with the invitee — it is shown only once'
    });
  } catch (e) { throw e; }
});

// DELETE /team/:id — deactivate team member
app.delete('/team/:id', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { rowCount } = await pool.query(
      'UPDATE users SET is_active=FALSE WHERE user_id=$1 AND tenant_id=$2',
      [req.params.id, tid]
    );
    if (!rowCount) return res.status(404).json({ error: 'user not found' });
    log.info('team.deactivate', { tenant_id: tid, user_id: req.params.id });
    res.json({ user_id: req.params.id, is_active: false });
  } catch (e) { throw e; }
});

// ── API Key Management ────────────────────────────────────────────────────────

// GET /api-keys — list all API keys for tenant
app.get('/api-keys', requireAuth, async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT api_key_id, role, scopes, plan_tier, is_revoked,
              created_at, expires_at, revoked_at
       FROM api_keys WHERE tenant_id=$1 ORDER BY created_at DESC`,
      [req.user.tenant_id]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// POST /api-keys — issue a new API key
app.post('/api-keys', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { role = '', scopes = [], plan_tier = '', expires_at } = req.body;
    // Generate raw key — show once, store hash only
    const crypto = require('crypto');
    const rawKey = crypto.randomBytes(32).toString('base64url');
    const keyHash = crypto.createHash('sha256').update(rawKey).digest('hex');
    const { rows } = await pool.query(
      `INSERT INTO api_keys (tenant_id, key_hash, role, scopes, plan_tier, expires_at)
       VALUES ($1,$2,$3,$4,$5,$6)
       RETURNING api_key_id, role, scopes, plan_tier, is_revoked, created_at, expires_at`,
      [tid, keyHash, role, scopes, plan_tier, expires_at || null]
    );
    log.info('api_key.issued', { tenant_id: tid, api_key_id: rows[0].api_key_id });
    res.status(201).json({
      ...rows[0],
      raw_key: rawKey,
      note: 'Store this key — it is shown only once'
    });
  } catch (e) { throw e; }
});

// POST /api-keys/:id/rotate — replace credential material for an API key
app.post('/api-keys/:id/rotate', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const crypto = require('crypto');
    const rawKey = crypto.randomBytes(32).toString('base64url');
    const keyHash = crypto.createHash('sha256').update(rawKey).digest('hex');
    const { rowCount } = await pool.query(
      `UPDATE api_keys SET key_hash=$1 WHERE api_key_id=$2 AND tenant_id=$3 AND is_revoked=FALSE`,
      [keyHash, req.params.id, tid]
    );
    if (!rowCount) return res.status(404).json({ error: 'api key not found or already revoked' });
    log.info('api_key.rotated', { tenant_id: tid, api_key_id: req.params.id });
    res.json({ api_key_id: req.params.id, raw_key: rawKey,
               note: 'Store this key — it is shown only once' });
  } catch (e) { throw e; }
});

// POST /api-keys/:id/revoke — permanently revoke an API key
app.post('/api-keys/:id/revoke', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { rowCount } = await pool.query(
      `UPDATE api_keys SET is_revoked=TRUE, revoked_at=NOW()
       WHERE api_key_id=$1 AND tenant_id=$2 AND is_revoked=FALSE`,
      [req.params.id, tid]
    );
    if (!rowCount) return res.status(404).json({ error: 'api key not found or already revoked' });
    log.info('api_key.revoked', { tenant_id: tid, api_key_id: req.params.id });
    res.json({ api_key_id: req.params.id, revoked: true });
  } catch (e) { throw e; }
});


// ── Phase 7: Campaign Audience View (Leads) ───────────────────────────────────

// GET /leads?campaign_id=... — campaign_audiences joined with customers
app.get('/leads', requireAuth, async (req, res) => {
  try {
    const { campaign_id } = req.query;
    if (!campaign_id) return res.status(422).json({ error: 'campaign_id required' });
    const tid = req.user.tenant_id;
    const { rows } = await pool.query(
      `SELECT ca.campaign_audience_id, ca.campaign_id, ca.customer_id,
              c.name AS customer_name,
              cc.value AS primary_contact,
              ca.dnd, ca.included_at, ca.excluded_reason
       FROM campaign_audiences ca
       LEFT JOIN customers c ON c.customer_id = ca.customer_id AND c.tenant_id = $1
       LEFT JOIN customer_contacts cc ON cc.customer_id = ca.customer_id
         AND cc.is_primary = TRUE AND cc.tenant_id = $1
       WHERE ca.campaign_id = $2 AND ca.tenant_id = $1
       ORDER BY ca.included_at DESC LIMIT 500`,
      [tid, campaign_id]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// ── Phase 7: Collections / Escalations ───────────────────────────────────────

// GET /collections/escalations — list tenant escalations
app.get('/collections/escalations', requireAuth, async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT escalation_id, tenant_id, call_id, customer_id,
              reason, escalated_to, escalated_at, resolved_at, resolution_notes
       FROM escalation_records WHERE tenant_id=$1
       ORDER BY escalated_at DESC LIMIT 200`,
      [req.user.tenant_id]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// POST /collections/escalations/:id/resolve — mark escalation resolved
app.post('/collections/escalations/:id/resolve', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { resolution_notes = '' } = req.body;
    const { rowCount, rows } = await pool.query(
      `UPDATE escalation_records
         SET resolved_at = NOW(), resolution_notes = $1
       WHERE escalation_id = $2 AND tenant_id = $3 AND resolved_at IS NULL
       RETURNING *`,
      [resolution_notes, req.params.id, req.user.tenant_id]
    );
    if (!rowCount) return res.status(404).json({ error: 'escalation not found or already resolved' });
    res.json(rows[0]);
  } catch (e) { throw e; }
});

// ── Phase 7: HITL Queue ───────────────────────────────────────────────────────

// GET /hitl/queue — list pending + claimed HITL items for tenant
app.get('/hitl/queue', requireAuth, async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT hitl_item_id, call_id, reason, priority, status,
              context, enqueued_at, claimed_by, claimed_at, resolved_at,
              sla_deadline_at, sla_breached
       FROM hitl_queue
       WHERE tenant_id = $1 AND status != 'RESOLVED'
       ORDER BY
         CASE priority WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,
         sla_deadline_at ASC`,
      [req.user.tenant_id]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// POST /hitl/queue/claim-next — atomically claim highest-priority pending item
app.post('/hitl/queue/claim-next', requireAuth, async (req, res) => {
  try {
    const { rows, rowCount } = await pool.query(
      `UPDATE hitl_queue
         SET status = 'CLAIMED', claimed_by = $1, claimed_at = NOW()
       WHERE hitl_item_id = (
         SELECT hitl_item_id FROM hitl_queue
         WHERE tenant_id = $2 AND status = 'PENDING'
         ORDER BY
           CASE priority WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,
           sla_deadline_at ASC
         LIMIT 1
         FOR UPDATE SKIP LOCKED
       )
       RETURNING *`,
      [req.user.sub, req.user.tenant_id]
    );
    if (!rowCount) return res.status(404).json({ error: 'no pending items' });
    res.json(rows[0]);
  } catch (e) { throw e; }
});

// POST /hitl/items/:id/decision — record supervisor decision and resolve item
app.post('/hitl/items/:id/decision', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const { decision, rationale } = req.body;
    if (!decision || !rationale) {
      return res.status(422).json({ error: 'decision and rationale required' });
    }
    const tid = req.user.tenant_id;
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { rowCount } = await client.query(
        `UPDATE hitl_queue SET status='RESOLVED', resolved_at=NOW()
         WHERE hitl_item_id=$1 AND tenant_id=$2`,
        [req.params.id, tid]
      );
      if (!rowCount) {
        await client.query('ROLLBACK');
        return res.status(404).json({ error: 'item not found' });
      }
      const { rows } = await client.query(
        `INSERT INTO hitl_decisions
           (hitl_item_id, tenant_id, supervisor_id, decision, rationale, decided_at)
         VALUES ($1, $2, $3, $4, $5, NOW())
         RETURNING *`,
        [req.params.id, tid, req.user.sub, decision, rationale]
      );
      await client.query('COMMIT');
      res.status(201).json(rows[0]);
    } catch (e) {
      await client.query('ROLLBACK');
      throw e;
    } finally {
      client.release();
    }
  } catch (e) { throw e; }
});

// ── Phase 7: Report Runs ──────────────────────────────────────────────────────

// GET /reports/runs — list aggregation run history for tenant
app.get('/reports/runs', requireAuth, async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT tenant_id, campaign_id, report_day AS day, ran_at
       FROM report_runs WHERE tenant_id=$1
       ORDER BY ran_at DESC LIMIT 100`,
      [req.user.tenant_id]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// POST /reports/runs — trigger a new aggregation run for a given day
app.post('/reports/runs', requireAuth, async (req, res) => {
  try {
    const { day, campaign_id } = req.body;
    if (!day) return res.status(422).json({ error: 'day required (YYYY-MM-DD)' });
    const { rows } = await pool.query(
      `INSERT INTO report_runs (tenant_id, report_day, campaign_id)
       VALUES ($1, $2::date, $3)
       RETURNING tenant_id, campaign_id, report_day AS day, ran_at`,
      [req.user.tenant_id, day, campaign_id || null]
    );
    res.status(201).json(rows[0]);
  } catch (e) { throw e; }
});

// ── Phase 7: Tenant-Facing Audit Log ─────────────────────────────────────────

// GET /audit-logs — return this tenant's own audit trail
app.get('/audit-logs', requireAuth, async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT audit_id, actor_id, action, resource_type, resource_id, outcome, recorded_at
       FROM audit_log WHERE tenant_id=$1
       ORDER BY recorded_at DESC LIMIT 200`,
      [req.user.tenant_id]
    );
    res.json(rows);
  } catch (e) { throw e; }
});


// ── Phase 7b: Tenant Self-Service Profile ─────────────────────────────────────

const ALLOWED_TIMEZONES = new Set([
  'Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Asia/Bangkok',
  'Asia/Tokyo', 'Asia/Karachi', 'Asia/Dhaka', 'UTC',
  'America/New_York', 'America/Chicago', 'America/Los_Angeles',
  'Europe/London', 'Europe/Paris', 'Europe/Berlin',
  'Australia/Sydney', 'Pacific/Auckland',
]);

const ALLOWED_CURRENCIES = new Set(['INR', 'USD', 'AED', 'EUR', 'GBP', 'SGD', 'THB', 'JPY', 'AUD', 'BDT', 'PKR']);

// GET /tenants/me — return current tenant's full profile
app.get('/tenants/me', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    if (!tid) return res.status(403).json({ error: 'platform users have no tenant profile' });
    const { rows } = await pool.query(
      `SELECT tenant_id, slug, display_name, subscription_tier, isolation_profile,
              status, timezone, currency, max_concurrent_calls, feature_flags,
              created_at, updated_at
       FROM tenants WHERE tenant_id = $1`,
      [tid]
    );
    if (!rows.length) return res.status(404).json({ error: 'tenant not found' });
    res.json(rows[0]);
  } catch (e) { throw e; }
});

// PUT /tenants/me — update editable profile fields (display_name, timezone, currency)
// slug, max_concurrent_calls, subscription_tier are platform-managed — not updatable here
app.put('/tenants/me', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    if (!tid) return res.status(403).json({ error: 'platform users have no tenant profile' });
    const { display_name, timezone, currency } = req.body;
    if (!display_name?.trim()) return res.status(422).json({ error: 'display_name required' });
    if (!timezone) return res.status(422).json({ error: 'timezone required' });
    if (!currency) return res.status(422).json({ error: 'currency required' });
    if (!ALLOWED_TIMEZONES.has(timezone)) {
      return res.status(422).json({ error: `invalid timezone: ${timezone}` });
    }
    if (!ALLOWED_CURRENCIES.has(currency.toUpperCase())) {
      return res.status(422).json({ error: `invalid currency: ${currency}` });
    }
    const { rows } = await pool.query(
      `UPDATE tenants
         SET display_name = $1, timezone = $2, currency = $3, updated_at = NOW()
       WHERE tenant_id = $4
       RETURNING tenant_id, slug, display_name, subscription_tier, isolation_profile,
                 status, timezone, currency, max_concurrent_calls, feature_flags,
                 created_at, updated_at`,
      [display_name.trim(), timezone, currency.toUpperCase(), tid]
    );
    if (!rows.length) return res.status(404).json({ error: 'tenant not found' });
    log.info('tenant.profile_updated', { tenant_id: tid, display_name: display_name.trim(), timezone, currency });
    res.json(rows[0]);
  } catch (e) { throw e; }
});


// ── Phase 8: Call History ─────────────────────────────────────────────────────

app.get('/calls', requireAuth, async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { campaign_id, status, limit: lq, offset: oq } = req.query;
    const lim = Math.min(parseInt(lq) || 100, 500);
    const off = parseInt(oq) || 0;
    const conds = ['ca.tenant_id = $1'], vals = [tid];
    if (campaign_id) { vals.push(campaign_id); conds.push('ca.campaign_id = $' + vals.length); }
    if (status)      { vals.push(status);      conds.push('ca.status = $'      + vals.length); }
    vals.push(lim, off);
    const pNum = vals.length;
    const { rows } = await pool.query(
      `SELECT ca.attempt_id, ca.call_sid, ca.campaign_id, ca.lead_id, ca.pipeline_id,
              ca.status, ca.disposition, ca.duration_s,
              ca.initiated_at, ca.answered_at, ca.ended_at, ca.error_message,
              l.name AS lead_name, l.phone AS lead_phone,
              tr.recording_id, tr.state AS recording_state
         FROM call_attempts ca
         LEFT JOIN leads l ON l.lead_id = ca.lead_id
         LEFT JOIN telephony_recordings tr ON tr.call_attempt_id = ca.attempt_id
        WHERE ${conds.join(' AND ')}
        ORDER BY ca.initiated_at DESC
        LIMIT $${pNum - 1} OFFSET $${pNum}`,
      vals
    );
    res.json(rows);
  } catch (e) { throw e; }
});

app.get('/campaigns/:id/calls', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const { status, limit: lq, offset: oq } = req.query;
    const lim = Math.min(parseInt(lq) || 100, 500);
    const off = parseInt(oq) || 0;
    const conds = ['ca.tenant_id = $1', 'ca.campaign_id = $2'], vals = [tid, req.params.id];
    if (status) { vals.push(status); conds.push('ca.status = $' + vals.length); }
    vals.push(lim, off);
    const pNum = vals.length;
    const { rows } = await pool.query(
      `SELECT ca.attempt_id, ca.call_sid, ca.campaign_id, ca.lead_id, ca.pipeline_id,
              ca.status, ca.disposition, ca.duration_s,
              ca.initiated_at, ca.answered_at, ca.ended_at, ca.error_message,
              l.name AS lead_name, l.phone AS lead_phone,
              tr.recording_id, tr.state AS recording_state
         FROM call_attempts ca
         LEFT JOIN leads l ON l.lead_id = ca.lead_id
         LEFT JOIN telephony_recordings tr ON tr.call_attempt_id = ca.attempt_id
        WHERE ${conds.join(' AND ')}
        ORDER BY ca.initiated_at DESC
        LIMIT $${pNum - 1} OFFSET $${pNum}`,
      vals
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// ── Phase 8: Campaign / Pipeline CRM ─────────────────────────────────────────

app.get('/campaigns/:id/customers', requireAuth, requireUUID('id'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const cam = await pool.query(
      'SELECT campaign_id FROM campaigns WHERE campaign_id=$1 AND tenant_id=$2',
      [req.params.id, tid]
    );
    if (!cam.rows.length) return res.status(404).json({ error: 'campaign_not_found' });
    const { rows } = await pool.query(
      `SELECT c.customer_id, c.name, c.crm_id,
              cc.value AS primary_contact,
              ca.dnd, ca.included_at, ca.excluded_reason,
              ca.campaign_audience_id
         FROM campaign_audiences ca
         JOIN customers c ON c.customer_id = ca.customer_id AND c.tenant_id = $1
         LEFT JOIN customer_contacts cc
           ON cc.customer_id = ca.customer_id AND cc.is_primary = TRUE AND cc.tenant_id = $1
        WHERE ca.campaign_id = $2 AND ca.tenant_id = $1
        ORDER BY ca.included_at DESC LIMIT 300`,
      [tid, req.params.id]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

app.get('/campaigns/:id/pipelines/:pipelineId/customers', requireAuth, requireUUID('id', 'pipelineId'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const pl = await pool.query(
      'SELECT pipeline_id FROM pipelines WHERE pipeline_id=$1 AND campaign_id=$2 AND tenant_id=$3',
      [req.params.pipelineId, req.params.id, tid]
    );
    if (!pl.rows.length) return res.status(404).json({ error: 'pipeline_not_found' });
    const { rows } = await pool.query(
      `SELECT DISTINCT
              COALESCE(c.customer_id::text, l.lead_id::text) AS customer_id,
              COALESCE(c.name, l.name) AS name,
              c.crm_id,
              l.phone AS primary_contact,
              l.status
         FROM leads l
         LEFT JOIN customers c ON c.customer_id = l.crm_customer_id AND c.tenant_id = $1
        WHERE l.pipeline_id = $2 AND l.campaign_id = $3 AND l.tenant_id = $1
        ORDER BY 2 LIMIT 300`,
      [tid, req.params.pipelineId, req.params.id]
    );
    res.json(rows);
  } catch (e) { throw e; }
});

// ── Phase 8: Pipeline Model Config ───────────────────────────────────────────

const VALID_STT_P8   = new Set(['whisper','deepgram','google_stt']);
const VALID_LLM_P8   = new Set(['vllm','openai','anthropic']);
const VALID_TTS_P8   = new Set(['veena','elevenlabs','google_tts','amazon_polly']);
const VALID_VOICE_P8 = new Set(['kavya','default','priya','aarav','Rachel','Domi','Bella','Antoni']);

app.get('/campaigns/:id/pipelines/:pipelineId/model-config', requireAuth, requireUUID('id', 'pipelineId'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const pl = await pool.query(
      'SELECT pipeline_id FROM pipelines WHERE pipeline_id=$1 AND campaign_id=$2 AND tenant_id=$3',
      [req.params.pipelineId, req.params.id, tid]
    );
    if (!pl.rows.length) return res.status(404).json({ error: 'pipeline_not_found' });
    const r = await pool.query(
      `SELECT model_config_id, stt_adapter, stt_model, llm_adapter, llm_model,
              llm_temperature, tts_adapter, tts_voice, updated_at,
              CASE WHEN pipeline_id IS NOT NULL THEN 'pipeline'
                   WHEN campaign_id  IS NOT NULL THEN 'campaign'
                   ELSE 'tenant' END AS config_level
         FROM model_configs
        WHERE tenant_id = $1
          AND (pipeline_id = $2
               OR (pipeline_id IS NULL AND campaign_id = $3)
               OR (pipeline_id IS NULL AND campaign_id IS NULL))
        ORDER BY
          CASE WHEN pipeline_id = $2 THEN 1
               WHEN campaign_id = $3 THEN 2 ELSE 3 END
        LIMIT 1`,
      [tid, req.params.pipelineId, req.params.id]
    );
    if (!r.rows.length) {
      return res.json({ model_config_id: null,
        stt_adapter: 'whisper', stt_model: 'whisper-large-v3-turbo',
        llm_adapter: 'vllm',    llm_model: 'qwen2.5-7b-instruct-fp8',
        llm_temperature: 0.3,   tts_adapter: 'veena', tts_voice: 'kavya',
        config_level: 'default' });
    }
    res.json(r.rows[0]);
  } catch (e) { throw e; }
});

app.put('/campaigns/:id/pipelines/:pipelineId/model-config', requireAuth, requireUUID('id', 'pipelineId'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const pl = await pool.query(
      'SELECT pipeline_id FROM pipelines WHERE pipeline_id=$1 AND campaign_id=$2 AND tenant_id=$3',
      [req.params.pipelineId, req.params.id, tid]
    );
    if (!pl.rows.length) return res.status(404).json({ error: 'pipeline_not_found' });
    const { stt_adapter, stt_model, llm_adapter, llm_model, llm_temperature, tts_adapter, tts_voice } = req.body || {};
    if (stt_adapter && !VALID_STT_P8.has(stt_adapter))   return res.status(422).json({ error: 'invalid stt_adapter' });
    if (llm_adapter && !VALID_LLM_P8.has(llm_adapter))   return res.status(422).json({ error: 'invalid llm_adapter' });
    if (tts_adapter && !VALID_TTS_P8.has(tts_adapter))   return res.status(422).json({ error: 'invalid tts_adapter' });
    if (tts_voice   && !VALID_VOICE_P8.has(tts_voice))   return res.status(422).json({ error: 'invalid tts_voice' });
    if (llm_temperature !== undefined && (isNaN(+llm_temperature) || +llm_temperature < 0 || +llm_temperature > 2))
      return res.status(422).json({ error: 'llm_temperature must be 0-2' });
    const { rows } = await pool.query(
      `INSERT INTO model_configs
           (tenant_id, campaign_id, pipeline_id, stt_adapter, stt_model,
            llm_adapter, llm_model, llm_temperature, tts_adapter, tts_voice)
         VALUES ($1,$2,$3,
           COALESCE($4,'whisper'),  COALESCE($5,'whisper-large-v3-turbo'),
           COALESCE($6,'vllm'),     COALESCE($7,'qwen2.5-7b-instruct-fp8'),
           COALESCE($8,0.3),        COALESCE($9,'veena'), COALESCE($10,'kavya'))
         ON CONFLICT (tenant_id, pipeline_id) WHERE pipeline_id IS NOT NULL
         DO UPDATE SET
           stt_adapter     = COALESCE(EXCLUDED.stt_adapter,     model_configs.stt_adapter),
           stt_model       = COALESCE(EXCLUDED.stt_model,       model_configs.stt_model),
           llm_adapter     = COALESCE(EXCLUDED.llm_adapter,     model_configs.llm_adapter),
           llm_model       = COALESCE(EXCLUDED.llm_model,       model_configs.llm_model),
           llm_temperature = COALESCE(EXCLUDED.llm_temperature, model_configs.llm_temperature),
           tts_adapter     = COALESCE(EXCLUDED.tts_adapter,     model_configs.tts_adapter),
           tts_voice       = COALESCE(EXCLUDED.tts_voice,       model_configs.tts_voice),
           updated_at      = NOW()
         RETURNING model_config_id, stt_adapter, stt_model, llm_adapter, llm_model,
                   llm_temperature, tts_adapter, tts_voice, updated_at`,
      [tid, req.params.id, req.params.pipelineId,
       stt_adapter||null, stt_model||null, llm_adapter||null, llm_model||null,
       llm_temperature||null, tts_adapter||null, tts_voice||null]
    );
    await bffAudit(pool, { req, action: 'pipeline.model_config.update', resourceType: 'pipeline', resourceId: req.params.pipelineId });
    res.json({ ...rows[0], config_level: 'pipeline' });
  } catch (e) { throw e; }
});

// ── Phase 8: Pipeline Analytics ──────────────────────────────────────────────

app.get('/campaigns/:id/pipelines/:pipelineId/analytics', requireAuth, requireUUID('id', 'pipelineId'), async (req, res) => {
  try {
    const tid = req.user.tenant_id;
    const pl = await pool.query(
      `SELECT calls_completed, calls_no_answer, calls_failed, total_duration_s
         FROM pipelines WHERE pipeline_id=$1 AND campaign_id=$2 AND tenant_id=$3`,
      [req.params.pipelineId, req.params.id, tid]
    );
    if (!pl.rows.length) return res.status(404).json({ error: 'pipeline_not_found' });
    const p = pl.rows[0];
    const ev = await pool.query(
      `SELECT COUNT(*)                                             AS total_events,
              COUNT(*) FILTER (WHERE status='SUCCESS')            AS successes,
              COUNT(*) FILTER (WHERE status='FAILURE')            AS failures,
              COUNT(*) FILTER (WHERE event_type='PTP_RECORDED')   AS ptps,
              COUNT(*) FILTER (WHERE event_type='LEAD_SKIPPED')   AS skipped,
              COUNT(DISTINCT lead_id)                             AS unique_leads
         FROM lead_execution_events WHERE pipeline_id=$1 AND tenant_id=$2`,
      [req.params.pipelineId, tid]
    );
    const e = ev.rows[0];
    const totalCalls = +p.calls_completed + +p.calls_no_answer + +p.calls_failed;
    const contactRate = totalCalls > 0 ? Math.round((+p.calls_completed / totalCalls) * 1000) / 10 : 0;
    const ptpRate = +e.unique_leads > 0 ? Math.round((+e.ptps / +e.unique_leads) * 1000) / 10 : 0;
    const avgDuration = +p.calls_completed > 0 ? Math.round(+p.total_duration_s / +p.calls_completed) : 0;
    res.json({ calls_completed: +p.calls_completed, calls_no_answer: +p.calls_no_answer,
      calls_failed: +p.calls_failed,     total_duration_s: +p.total_duration_s,
      avg_duration_s: avgDuration,        contact_rate_pct: contactRate,
      ptp_count: +e.ptps,                ptp_rate_pct: ptpRate,
      unique_leads: +e.unique_leads,      total_events: +e.total_events,
      successes: +e.successes,            failures: +e.failures, skipped: +e.skipped });
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
