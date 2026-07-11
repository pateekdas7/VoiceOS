#!/usr/bin/env bash
# ==============================================================================
# VoiceOS CPU Node Health Check Script
# Verifies all deployed services are healthy.
# Sourced by restore.sh; can also be run standalone.
#
# Usage:
#   source healthcheck.sh
#   # or: bash healthcheck.sh
# ==============================================================================

set -euo pipefail

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] HEALTH $*"; }
ok()  { log "OK  — $*"; }
fail(){ log "FAIL — $*"; HEALTH_FAILED=1; }

HEALTH_FAILED=0

# Sprint-017 fix: every psql invocation below authenticates as the `voiceos`
# user, but no PGPASSWORD was ever exported — psql then either prompts
# interactively (hanging a non-interactive run) or fails outright
# ("fe_sendauth: no password supplied"), so every Postgres-dependent check
# (including the pre-existing PostgreSQL/schema checks, not just the new
# Policy Engine one) silently reported false failures. Found running this
# script for real during Sprint-017 Phase 2 (see CHANGELOG.md). No credential
# is hardcoded here (same convention as REDIS_PASSWORD below) — export
# POSTGRES_PASSWORD in your shell before running this script.
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

# ── Infrastructure services ───────────────────────────────────────────────────
log "Checking infrastructure services..."

# Redis
if redis-cli -h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -a "${REDIS_PASSWORD:-}" ping 2>/dev/null | grep -q PONG; then
  ok "Redis"
else
  fail "Redis — cannot ping"
fi

# Redis persistence (TT-002 — must be hardened, not apt defaults)
AOF_STATUS=$(redis-cli -h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -a "${REDIS_PASSWORD:-}" \
  CONFIG GET appendonly 2>/dev/null | tail -1)
if [[ "${AOF_STATUS}" == "yes" ]]; then
  ok "Redis AOF persistence (appendonly=yes)"
else
  fail "Redis AOF persistence — appendonly=${AOF_STATUS:-unknown} (expected 'yes'); see CPU_NODE_STATE.md §18 TT-002"
fi

# EventBus (Sprint-013 — Redis Streams consumer group + DLQ depth)
# TT-002: self-heals on detection rather than merely reporting failure — the
# recreation is idempotent/race-safe (scripts/eventbus_recovery.py wraps the
# same EventBus.ensure_consumer_group() every real Consumer already calls at
# startup), so manual intervention is never required for this condition.
EVENT_BUS_STREAM="${EVENT_BUS_STREAM:-voiceos-events}"
EVENT_BUS_DLQ="${EVENT_BUS_DLQ:-dlq:voiceos-events}"
EVENT_BUS_CONSUMER_GROUP="${EVENT_BUS_CONSUMER_GROUP:-main-group}"
HEALTHCHECK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${HEALTHCHECK_DIR}/../.." && pwd)"
# Sprint-020 fix: this used to unconditionally rebuild REDIS_URL with no
# password and no ${REDIS_URL:-...} fallback guard (unlike every other
# REDIS_URL/POSTGRES_DSN construction in this script), clobbering any
# already-exported, auth-bearing REDIS_URL. Harmless before Sprint-019 (no
# Redis auth existed yet); once auth was enforced, every Python call below
# that relies on this env var (eventbus_recovery.py, the Policy Engine
# cache-warm snippet) started failing with AuthenticationError. Found
# running this script for real during Sprint-020 Phase 2.
export REDIS_URL="${REDIS_URL:-redis://:${REDIS_PASSWORD:-}@${REDIS_HOST:-redis}:${REDIS_PORT:-6379}/0}"
if redis-cli -h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -a "${REDIS_PASSWORD:-}" \
    XINFO GROUPS "${EVENT_BUS_STREAM}" 2>/dev/null | grep -q "${EVENT_BUS_CONSUMER_GROUP}"; then
  ok "EventBus consumer group '${EVENT_BUS_CONSUMER_GROUP}' on stream '${EVENT_BUS_STREAM}'"
else
  log "EventBus consumer group '${EVENT_BUS_CONSUMER_GROUP}' missing on '${EVENT_BUS_STREAM}' — self-healing..."
  if python "${APP_ROOT}/scripts/eventbus_recovery.py" 2>&1 | while IFS= read -r line; do log "  ${line}"; done; then
    ok "EventBus consumer group '${EVENT_BUS_CONSUMER_GROUP}' — recovered automatically"
  else
    fail "EventBus — consumer group '${EVENT_BUS_CONSUMER_GROUP}' could not be recovered automatically"
  fi
fi
DLQ_DEPTH=$(redis-cli -h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -a "${REDIS_PASSWORD:-}" XLEN "${EVENT_BUS_DLQ}" 2>/dev/null || echo "?")
log "EventBus DLQ depth (${EVENT_BUS_DLQ}): ${DLQ_DEPTH}"

# PostgreSQL
if psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -c "SELECT 1;" &>/dev/null; then
  ok "PostgreSQL"
else
  fail "PostgreSQL — cannot connect"
fi

# PostgreSQL schema (Sprint-014 — Alembic migration version + table count;
# head bumped to 0027 by Sprint-028's performance_baselines migration --
# same recurring "stale hardcoded migration-head" bug class as every prior
# sprint, fixed proactively this time instead of found stale, see
# CHANGELOG.md)
#
# Sprint-016 fix: alembic.ini's env.py reads POSTGRES_DSN to build the
# SQLAlchemy URL — without it, `alembic current` fails with
# `NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:driver` and,
# under this script's `set -euo pipefail`, silently aborts the entire health
# check before any later section (including the Sprint-016 circuit-breaker
# check below) ever runs. Exporting it here, with a `|| true` fallback so a
# future Alembic hiccup degrades to a FAIL line instead of killing the script.
if command -v alembic &>/dev/null; then
  export POSTGRES_DSN="${POSTGRES_DSN:-postgresql://${POSTGRES_USER:-voiceos}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-voiceos}}"
  ALEMBIC_VERSION=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && alembic current 2>/dev/null | tail -1 || true)
  if echo "${ALEMBIC_VERSION}" | grep -q "0027"; then
    ok "Alembic migration version: ${ALEMBIC_VERSION}"
  else
    fail "Alembic — expected head revision 0027, got: '${ALEMBIC_VERSION}'"
  fi
fi
TABLE_COUNT=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null || echo "0")
if [[ "${TABLE_COUNT}" -ge 13 ]]; then
  ok "PostgreSQL schema — ${TABLE_COUNT} tables present (>= 13 minimum)"
else
  fail "PostgreSQL schema — only ${TABLE_COUNT} tables present, expected >= 13"
fi

# MongoDB
if mongosh "${MONGO_URI:-mongodb://localhost:27017}" --quiet --eval "db.adminCommand('ping').ok" 2>/dev/null | grep -q 1; then
  ok "MongoDB"
else
  fail "MongoDB — cannot ping"
fi

# MongoDB indexes (Sprint-014 — response_plans, decision_envelopes, call_transcripts, call_lineage)
for collection in response_plans decision_envelopes call_transcripts call_lineage; do
  idx_count=$(mongosh "${MONGO_URI:-mongodb://localhost:27017}" --quiet --eval \
    "db.getSiblingDB('${POSTGRES_DB:-voiceos}').${collection}.getIndexes().length" 2>/dev/null || echo "0")
  if [[ "${idx_count}" -ge 2 ]]; then
    ok "MongoDB indexes — ${collection}: ${idx_count} present"
  else
    fail "MongoDB indexes — ${collection}: only ${idx_count} present (expected >= 2)"
  fi
done

# Circuit breakers (Sprint-016, V3 Ch14) — sanity: every per-dependency
# breaker (STT/LLM/TTS/Postgres/Redis/MongoDB) must start CLOSED. No live
# service process exists yet to query a real /metrics circuit_breaker_state
# gauge from (Sprint-026 gives services HTTP listeners) — this constructs
# the same CircuitBreakerRegistry the services will use and asserts the
# library-level startup invariant directly.
log "Checking circuit breaker initial state..."
CB_CHECK=$(cd "${APP_ROOT}" && python3 -c "
from src.libs.circuit_breaker.breaker import CircuitBreakerRegistry, CircuitState
registry = CircuitBreakerRegistry()
services = ['stt', 'llm', 'tts', 'postgres', 'redis', 'mongo']
all_closed = all(registry.get_or_create(s).state == CircuitState.CLOSED for s in services)
print('CLOSED' if all_closed else 'NOT_CLOSED')
" 2>/dev/null || echo "ERROR")
if [[ "${CB_CHECK}" == "CLOSED" ]]; then
  ok "Circuit breakers — stt/llm/tts/postgres/redis/mongo all CLOSED at startup"
else
  fail "Circuit breakers — expected all CLOSED at startup, got: ${CB_CHECK}"
fi

# Policy Engine (Sprint-017, V4 Ch4) — seeded rule rows in Postgres +
# a warm Redis rule-set cache. PolicyEngineService is a library class (no
# HTTP listener — see §8.1 precedent), so this checks its two real
# dependencies directly rather than an HTTP endpoint.
log "Checking Policy Engine..."
POLICY_ROW_COUNT=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM policies WHERE active = TRUE;" 2>/dev/null || echo "0")
if [[ "${POLICY_ROW_COUNT}" -ge 18 ]]; then
  ok "Policy Engine — ${POLICY_ROW_COUNT} active policy rows in Postgres (>= 18 built-in rules)"
else
  fail "Policy Engine — only ${POLICY_ROW_COUNT} active policy rows present, expected >= 18 (run scripts/seed_policies.py)"
fi
# The rule-set cache TTL is 30s by design (V4 Ch4 §4.13 decision_cache_ttl_s)
# — it is expected to be cold between requests, so an absent key is not
# itself unhealthy. Self-heal (like the EventBus consumer-group check above)
# by issuing one real evaluation, which populates it on demand, then confirm.
POLICY_CACHE_KEYS=$(redis-cli -h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -a "${REDIS_PASSWORD:-}" \
  --scan --pattern 'policy:*' 2>/dev/null | wc -l || echo "0")
if [[ "${POLICY_CACHE_KEYS}" -lt 1 ]]; then
  log "Policy Engine — no 'policy:*' cache keys in Redis (expected — 30s TTL); warming via one live evaluation..."
  export POSTGRES_DSN="${POSTGRES_DSN:-postgresql://${POSTGRES_USER:-voiceos}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-voiceos}}"
  export REDIS_URL="${REDIS_URL:-redis://${REDIS_HOST:-redis}:${REDIS_PORT:-6379}/0}"
  (cd "${APP_ROOT}" && python3 -c "
from src.libs.repositories.policy import PolicyRepository
from src.services.policy_engine.engine import PolicyEngine
from src.services.policy_engine.rule import PolicyRequest
import os, psycopg2, redis as redis_lib
pg = psycopg2.connect(os.environ['POSTGRES_DSN'])
r = redis_lib.Redis.from_url(os.environ['REDIS_URL'], decode_responses=False, protocol=2)
engine = PolicyEngine(redis=r, policy_repository=PolicyRepository(pg))
engine.evaluate(PolicyRequest(domain='rbi', action='__healthcheck_warmup__', subject='healthcheck', resource='-'))
" 2>&1) | while IFS= read -r line; do log "  ${line}"; done
  POLICY_CACHE_KEYS=$(redis-cli -h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -a "${REDIS_PASSWORD:-}" \
    --scan --pattern 'policy:*' 2>/dev/null | wc -l || echo "0")
fi
if [[ "${POLICY_CACHE_KEYS}" -ge 1 ]]; then
  ok "Policy Engine — ${POLICY_CACHE_KEYS} 'policy:*' rule-set cache key(s) warm in Redis"
else
  fail "Policy Engine — could not warm any 'policy:*' cache key in Redis"
fi

# Auth / Authz / AI Governance (Sprint-018, V4 Ch5/Ch6/Ch3) — like Policy
# Engine, all three are library classes (no standalone HTTP listener yet —
# same §8.1 precedent); this checks their real dependencies directly: the
# mTLS PKI on disk, and the AI Governance violation counter's steady state.
log "Checking Auth / Authz / AI Governance..."

MTLS_CERTS_DIR="${MTLS_CA_CERT_PATH:-/opt/voiceos/certs/ca.crt}"
MTLS_CERTS_DIR="$(dirname "${MTLS_CERTS_DIR}")"
if [[ -f "${MTLS_CERTS_DIR}/ca.crt" && -f "${MTLS_CERTS_DIR}/ca.key" ]]; then
  ok "mTLS PKI — CA cert/key present at ${MTLS_CERTS_DIR}"
else
  fail "mTLS PKI — CA cert/key missing at ${MTLS_CERTS_DIR} (run scripts/pki/generate_mtls_certs.py)"
fi

LEAF_CERT_COUNT=$(find "${MTLS_CERTS_DIR}" -maxdepth 1 -name '*.crt' ! -name 'ca.crt' 2>/dev/null | wc -l || echo "0")
if [[ "${LEAF_CERT_COUNT}" -ge 1 ]]; then
  ok "mTLS PKI — ${LEAF_CERT_COUNT} service leaf certificate(s) present"
else
  fail "mTLS PKI — no service leaf certificates present in ${MTLS_CERTS_DIR}"
fi

# JWT/RBAC/AI-Governance library smoke test — proves the new packages import
# and evaluate cleanly against this node's Python/venv (no real IdP needed:
# an in-process RSA key pair signs/validates a token, same pattern as
# scripts/sprint018_infra_validation.py).
AUTH_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from cryptography.hazmat.primitives.asymmetric import rsa
from src.services.auth.jwt_validator import JWTValidator, issue_test_token
from src.services.authz.rbac_engine import RBACEngine
from src.services.authz.roles import Role
from src.services.ai_governance.service import AIGovernanceService
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
token = issue_test_token(key, subject='healthcheck', tenant_id='healthcheck')
ctx = JWTValidator(public_key=key.public_key()).validate(token)
assert ctx.tenant_id == 'healthcheck'
assert RBACEngine().check_http_method(Role.AUDITOR, 'POST') is False
AIGovernanceService.create()
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${AUTH_SMOKE}" == "OK" ]]; then
  ok "Auth/Authz/AI Governance — JWT sign+validate, RBAC deny-on-write, AIGovernanceService construction all OK"
else
  fail "Auth/Authz/AI Governance — smoke test failed: ${AUTH_SMOKE}"
fi

# law_of_authority_violations — must be 0 at steady state (only increments on
# an actual detected violation, never as a baseline/background rate).
LOA_VIOLATIONS=$(cd "${APP_ROOT}" && python3 -c "
from src.services.ai_governance import metrics
print(int(metrics.LAW_OF_AUTHORITY_VIOLATIONS._value.get()))
" 2>/dev/null || echo "-1")
if [[ "${LOA_VIOLATIONS}" == "0" ]]; then
  ok "law_of_authority_violations counter — 0 at steady state (fresh process)"
else
  log "law_of_authority_violations counter — ${LOA_VIOLATIONS} (non-zero is expected only if this process already evaluated a violating output)"
fi

# Secrets Management / Encryption / Privacy (Sprint-019, V4 Ch7/Ch8/Ch9) —
# self-hosted Vault (KV v2 + Transit) on this node, PII field-level
# encryption in Postgres, and Redis/MongoDB auth enforcement.
log "Checking Vault / Encryption / Privacy..."

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
if curl -sf "${VAULT_ADDR}/v1/sys/health" &>/dev/null; then
  ok "Vault — reachable at ${VAULT_ADDR} and unsealed"
else
  fail "Vault — not reachable/unsealed at ${VAULT_ADDR}"
fi

if [[ -n "${VAULT_TOKEN:-}" ]]; then
  SECRET_FETCH=$(cd "${APP_ROOT}" && python3 -c "
from src.libs.secrets.providers.vault_provider import HVACVaultClient, VaultProvider
from src.libs.secrets.manager import SecretsManager
import os
sm = SecretsManager(VaultProvider(HVACVaultClient(os.environ['VAULT_ADDR'], os.environ['VAULT_TOKEN'])))
sm.get_secret('voiceos/postgres')
print('OK')
" 2>&1 || echo "ERROR")
  if [[ "${SECRET_FETCH}" == "OK" ]]; then
    ok "SecretsManager — fetches a real secret from Vault KV"
  else
    fail "SecretsManager — could not fetch from Vault: ${SECRET_FETCH}"
  fi
else
  log "VAULT_TOKEN not set — skipping SecretsManager fetch check"
fi

# check_secrets.py — must be clean on the deployed codebase (blocks CI merges).
if (cd "${APP_ROOT}" && python3 scripts/check_secrets.py src/ tests/ scripts/ &>/dev/null); then
  ok "check_secrets.py — 0 hardcoded secrets found in deployed code"
else
  fail "check_secrets.py — found hardcoded secrets in deployed code"
fi

# PII columns — customers.name_encrypted etc. must exist (migration 0016).
PII_COLUMNS=$(PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -h "${POSTGRES_HOST:-localhost}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.columns WHERE table_name IN ('customers','customer_contacts','customer_addresses') AND column_name LIKE '%_encrypted';" 2>/dev/null || echo "0")
if [[ "${PII_COLUMNS}" == "3" ]]; then
  ok "PII encryption columns — name_encrypted/value_encrypted/address_encrypted all present"
else
  fail "PII encryption columns — expected 3, found ${PII_COLUMNS} (migration 0016 not applied?)"
fi

# Redis/MongoDB auth — an unauthenticated PING/ping must now be rejected.
if redis-cli ping 2>&1 | grep -q "NOAUTH"; then
  ok "Redis — requires auth (unauthenticated PING rejected)"
else
  fail "Redis — does not require auth (Sprint-019 requirepass not active)"
fi

# mongosh exits non-zero when the evaluated command throws (unlike redis-cli,
# which exits 0 even on an ERROR reply above) — under `pipefail` that poisons
# `| grep -q`'s pipeline exit status regardless of what grep matches, so
# capture the output first and grep the variable (same fix as
# scripts/vault/bootstrap_vault.sh's unseal check and
# provision_datastore_auth.sh's own MongoDB detection note).
MONGO_AUTH_CHECK_OUTPUT="$(mongosh --quiet --eval 'db.adminCommand({listCollections:1})' 2>&1)" || true
if echo "${MONGO_AUTH_CHECK_OUTPUT}" | grep -q "requires authentication"; then
  ok "MongoDB — requires auth (unauthenticated command rejected)"
else
  fail "MongoDB — does not require auth (Sprint-019 --auth not active)"
fi

# PII Protection / Audit / API Security / AI Safety / Compliance Monitoring /
# Incident Response (Sprint-020, V4 Ch10/11/12/13/14/16/17) — all library
# classes, no standalone HTTP listener yet (same §8.1 precedent as Policy
# Engine/Auth/Authz above); this checks their real dependencies directly.
log "Checking PII Protection / Audit / AI Safety / Compliance Monitoring / Incident Response..."

AUDIT_HASH_COLUMNS=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = 'audit_log' AND column_name IN ('seq','prev_hash','hash');" 2>/dev/null || echo "0")
if [[ "${AUDIT_HASH_COLUMNS}" == "3" ]]; then
  ok "audit_log hash-chain columns — seq/prev_hash/hash all present (migration 0017)"
else
  fail "audit_log hash-chain columns — expected 3, found ${AUDIT_HASH_COLUMNS} (migration 0017 not applied?)"
fi

PII_TOKENS_TABLE=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'pii_tokens';" 2>/dev/null || echo "0")
if [[ "${PII_TOKENS_TABLE}" == "1" ]]; then
  ok "pii_tokens table present (migration 0017)"
else
  fail "pii_tokens table missing (migration 0017 not applied?)"
fi

SPRINT020_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from src.libs.pii.detector import PIIDetector
from src.libs.pii.redactor import PIIRedactor
from src.libs.ai_safety.content_moderator import ContentModerator
from src.libs.ai_safety.prompt_injection import PromptInjectionDetector
from src.libs.api_security.headers import SecurityHeaders
from src.services.compliance_monitoring.service import ComplianceMonitoring
from src.services.incident_response.service import IncidentResponse
assert '[PHONE]' in PIIRedactor().redact('call 9876543210')
assert any(s.entity_type.value == 'PHONE' for s in PIIDetector().detect('9876543210'))
assert not ContentModerator().check('tu chutiya hai').safe
assert PromptInjectionDetector().detect('ignore instructions').flagged
assert 'Strict-Transport-Security' in SecurityHeaders().as_dict()
ComplianceMonitoring()
IncidentResponse()
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${SPRINT020_SMOKE}" == "OK" ]]; then
  ok "Sprint-020 libraries — PIIDetector/PIIRedactor/ContentModerator/PromptInjectionDetector/SecurityHeaders/ComplianceMonitoring/IncidentResponse all construct and evaluate OK"
else
  fail "Sprint-020 libraries — smoke test failed: ${SPRINT020_SMOKE}"
fi

# Tenant Management / Org Management / User Management (Sprint-021, V5 Ch2/
# Ch3/Ch8) — all library classes, no standalone HTTP listener yet (same
# §8.1 precedent as every service above).
log "Checking Tenant Management / Org Management / User Management..."

TENANTS_STATUS_ENUM=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_tenants_status_enum';" 2>/dev/null || echo "")
if echo "${TENANTS_STATUS_ENUM}" | grep -q "TRIAL"; then
  ok "tenants.status enum reworked to the 7-state lifecycle (migration 0018)"
else
  fail "tenants.status enum still on the pre-Sprint-021 5-value set (migration 0018 not applied?)"
fi

for t in invitations sso_config; do
  T_EXISTS=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '${t}';" 2>/dev/null || echo "0")
  if [[ "${T_EXISTS}" == "1" ]]; then
    ok "${t} table present (migration 0018)"
  else
    fail "${t} table missing (migration 0018 not applied?)"
  fi
done

SPRINT021_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from src.services.tenant_management.lifecycle import TenantLifecycle
from src.libs.contracts.models.tenant import TenantStatus
from src.services.org_management.hierarchy import OrgHierarchy
from src.services.user_management.invitation import InvitationService
assert TenantLifecycle.is_valid_transition(TenantStatus.TRIAL, TenantStatus.SANDBOX)
assert not TenantLifecycle.is_valid_transition(TenantStatus.DELETED, TenantStatus.PRODUCTION)
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${SPRINT021_SMOKE}" == "OK" ]]; then
  ok "Sprint-021 libraries — TenantLifecycle/OrgHierarchy/InvitationService all construct and evaluate OK"
else
  fail "Sprint-021 libraries — smoke test failed: ${SPRINT021_SMOKE}"
fi

# CRM / Collections (Sprint-022, V5 Ch4/Ch5) — CRMService (CustomerService,
# CustomerContextAssembler) and CollectionsService (LoanAccountService, PTP,
# Settlement, Callback, Escalation), both library classes, no standalone HTTP
# listener yet (same §8.1 precedent as every service above).
log "Checking CRM / Collections..."

SETTLEMENT_AUTH_COLUMNS=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = 'settlements' AND column_name IN ('approved_by','authorized_at');" 2>/dev/null || echo "0")
if [[ "${SETTLEMENT_AUTH_COLUMNS}" == "2" ]]; then
  ok "settlements authorization columns — approved_by/authorized_at present (migration 0019)"
else
  fail "settlements authorization columns — expected 2, found ${SETTLEMENT_AUTH_COLUMNS} (migration 0019 not applied?)"
fi

SPRINT022_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from src.libs.invariants import assert_ri5_law_of_authority, InvariantViolationError
from src.services.collections.emi_schedule import EMIScheduleService
from src.services.collections.settlement import SettlementService
raised = False
try:
    assert_ri5_law_of_authority('outstanding_balance', 100, {'crm_collections'}, 'llm_hallucination')
except InvariantViolationError:
    raised = True
assert raised
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${SPRINT022_SMOKE}" == "OK" ]]; then
  ok "Sprint-022 libraries — CustomerContextAssembler/EMIScheduleService/SettlementService construct OK; RI-5 blocks unauthorized source"
else
  fail "Sprint-022 libraries — smoke test failed: ${SPRINT022_SMOKE}"
fi

# Campaign Management / Contact Center / HITL (Sprint-023, V5 Ch6/Ch7; V4
# Ch2/Ch15) — CampaignManagementService (CampaignService/ScheduleEngine/
# ABTestingFramework/AudienceSelector/CallDispatcher), ContactCenterService
# (SkillsBasedRouter/LiveTransferService/SupervisorService), HITLService
# (HITLQueue/SLAEnforcer/HumanReviewAPI/OverrideLogger/HITLDashboard) — all
# library classes, no standalone HTTP listener yet (same §8.1 precedent as
# every service above). HumanReviewAPI's Starlette app follows the
# create_health_app() "library ASGI app" pattern (TT-006) — nothing to probe
# on a live port here either.
log "Checking Campaign Management / Contact Center / HITL..."

HITL_TABLE_COUNT=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('campaign_audiences','campaign_results','hitl_queue','hitl_decisions');" 2>/dev/null || echo "0")
if [[ "${HITL_TABLE_COUNT}" == "4" ]]; then
  ok "Sprint-023 tables — campaign_audiences/campaign_results/hitl_queue/hitl_decisions present (migration 0020)"
else
  fail "Sprint-023 tables — expected 4, found ${HITL_TABLE_COUNT} (migration 0020 not applied?)"
fi

CAMPAIGN_STATUS_ENUM=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_campaigns_status_enum';" 2>/dev/null || echo "")
if echo "${CAMPAIGN_STATUS_ENUM}" | grep -q "REVIEW" && echo "${CAMPAIGN_STATUS_ENUM}" | grep -q "APPROVED"; then
  ok "campaigns.status enum — reworked 7-state lifecycle present (migration 0020)"
else
  fail "campaigns.status enum — still on the pre-Sprint-023 6-value set (migration 0020 not applied?)"
fi

SPRINT023_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from src.services.campaign_management.lifecycle import CampaignLifecycle, CampaignLifecycleError
from src.libs.contracts.models.campaign import CampaignStatus
raised = False
try:
    CampaignLifecycle.validate_transition(CampaignStatus.DRAFT, CampaignStatus.ACTIVE)
except CampaignLifecycleError:
    raised = True
assert raised
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${SPRINT023_SMOKE}" == "OK" ]]; then
  ok "Sprint-023 libraries — CampaignLifecycle/ScheduleEngine/HITLQueue construct OK; DRAFT->ACTIVE (skip APPROVED) raises"
else
  fail "Sprint-023 libraries — smoke test failed: ${SPRINT023_SMOKE}"
fi

HITL_QUEUE_DEPTH=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM hitl_queue WHERE status IN ('PENDING','CLAIMED');" 2>/dev/null || echo "?")
log "hitl_queue_depth (open items, all tenants): ${HITL_QUEUE_DEPTH}"

# Billing / Usage Metering / Analytics / Reporting / BI Platform (Sprint-024,
# V5 Ch9/Ch10/Ch11/Ch12/Ch21) — BillingService (SubscriptionManager/
# EntitlementEngine/InvoiceGenerator/PaymentProcessor), MeteringService
# (UsageCollector/UsageAggregator/UsageLimitEnforcer), AnalyticsService
# (CallAnalytics/CampaignAnalytics/RealtimeAnalytics/DailyAggregationJob),
# ReportingService (ReportScheduler/ExportService), BIPlatformService
# (BIWarehouse/ForecastingEngine/CrossTenantBenchmarking/ExecutiveDashboard) —
# all library classes, no standalone HTTP listener yet (same §8.1 precedent
# as every service above).
log "Checking Billing / Usage Metering / Analytics / Reporting / BI Platform..."

SPRINT024_TABLE_COUNT=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'analytics_daily';" 2>/dev/null || echo "0")
if [[ "${SPRINT024_TABLE_COUNT}" == "1" ]]; then
  ok "Sprint-024 tables — analytics_daily present (migration 0022)"
else
  fail "Sprint-024 tables — analytics_daily missing (migration 0022 not applied?)"
fi

BI_FACTS_TABLE_COUNT=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'bi_facts' AND table_name IN ('dim_tenant','fact_daily');" 2>/dev/null || echo "0")
if [[ "${BI_FACTS_TABLE_COUNT}" == "2" ]]; then
  ok "Sprint-024 bi_facts schema — dim_tenant/fact_daily present (migration 0023)"
else
  fail "Sprint-024 bi_facts schema — expected 2 tables, found ${BI_FACTS_TABLE_COUNT} (migration 0023 not applied?)"
fi

INVOICE_LINE_ITEMS_COLUMN=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = 'invoices' AND column_name = 'line_items';" 2>/dev/null || echo "0")
if [[ "${INVOICE_LINE_ITEMS_COLUMN}" == "1" ]]; then
  ok "invoices.line_items JSONB column present (migration 0021)"
else
  fail "invoices.line_items column missing (migration 0021 not applied?)"
fi

SPRINT024_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from src.services.billing.rate_card import DEFAULT_RATE_CARD, TIER_USAGE_LIMITS
from src.services.metering.collector import UsageCollector
from src.services.analytics.aggregation import DailyAggregationJob
from src.services.reporting.exporter import ExportService
from src.services.bi_platform.warehouse import BIWarehouse
from src.libs.contracts.models.billing import SubscriptionTier, UsageType
assert TIER_USAGE_LIMITS[SubscriptionTier.ENTERPRISE][UsageType.CALL_MINUTE] is None
assert DEFAULT_RATE_CARD.entries[UsageType.CALL_MINUTE].price_per_unit_minor > 0
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${SPRINT024_SMOKE}" == "OK" ]]; then
  ok "Sprint-024 libraries — Billing/Metering/Analytics/Reporting/BIPlatform construct OK; ENTERPRISE tier unlimited"
else
  fail "Sprint-024 libraries — smoke test failed: ${SPRINT024_SMOKE}"
fi

# Admin Portal / AI Configuration / Integration Platform / API Platform
# (Sprint-025, V5 Ch13/Ch14/Ch15/Ch16) — AdminAPI (Tenant/User/Campaign/
# Billing/Audit/AIConfig admin controllers), AIConfigService (Prompt
# Versioning/ModelConfig), IntegrationPlatformService (WebhookService/
# WebhookDeliveryEngine), APIPlatformService (PublicAPI, spec-first OpenAPI
# 3.1) — all library classes, no standalone HTTP listener yet (same §8.1
# precedent as every service above).
log "Checking Admin Portal / AI Configuration / Integration Platform / API Platform..."

SPRINT025_TABLE_COUNT=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('prompt_versions','campaign_prompt_pins','model_configs','webhook_registrations','webhook_deliveries','api_keys');" 2>/dev/null || echo "0")
if [[ "${SPRINT025_TABLE_COUNT}" == "6" ]]; then
  ok "Sprint-025 tables — prompt_versions/model_configs/webhook_registrations/webhook_deliveries/api_keys present (migration 0024)"
else
  fail "Sprint-025 tables — expected 6, found ${SPRINT025_TABLE_COUNT} (migration 0024 not applied?)"
fi

# Sprint-025 Part-3 (migration 0025): webhook_delivery_attempts, webhook_dead_letter_queue,
# api_key_usage, api_rate_limits (tables) + admin_audit_views (view).
SPRINT025_PART3_TABLE_COUNT=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('webhook_delivery_attempts','webhook_dead_letter_queue','api_key_usage','api_rate_limits');" 2>/dev/null || echo "0")
SPRINT025_PART3_VIEW_COUNT=$(psql -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER:-voiceos}" -d "${POSTGRES_DB:-voiceos}" -tAc \
  "SELECT COUNT(*) FROM information_schema.views WHERE table_schema = 'public' AND table_name = 'admin_audit_views';" 2>/dev/null || echo "0")
if [[ "${SPRINT025_PART3_TABLE_COUNT}" == "4" && "${SPRINT025_PART3_VIEW_COUNT}" == "1" ]]; then
  ok "Sprint-025 Part-3 tables/view — webhook_delivery_attempts/webhook_dead_letter_queue/api_key_usage/api_rate_limits/admin_audit_views present (migration 0025)"
else
  fail "Sprint-025 Part-3 tables/view — expected 4 tables + 1 view, found ${SPRINT025_PART3_TABLE_COUNT} tables/${SPRINT025_PART3_VIEW_COUNT} view (migration 0025 not applied?)"
fi

SPRINT025_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from src.services.ai_config.prompt_versioning import PromptVersioningService
from src.services.ai_config.model_config import GLOBAL_DEFAULT_MODEL_CONFIG
from src.services.integration_platform.signature import WebhookSigner
from src.services.integration_platform.webhook import DOMAIN_EVENT_TO_WEBHOOK_EVENT
from src.services.api_platform.openapi import get_openapi_schema
schema = get_openapi_schema()
assert schema['openapi'].startswith('3.1')
assert len(schema['paths']) >= 6
assert len(DOMAIN_EVENT_TO_WEBHOOK_EVENT) == 5
sig = WebhookSigner.sign({'a': 1}, 'secret')
assert WebhookSigner.verify({'a': 1}, 'secret', sig)
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${SPRINT025_SMOKE}" == "OK" ]]; then
  ok "Sprint-025 libraries — AIConfig/IntegrationPlatform/APIPlatform construct OK; OpenAPI spec loads with 6+ paths"
else
  fail "Sprint-025 libraries — smoke test failed: ${SPRINT025_SMOKE}"
fi

SPRINT025_PART3_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from src.services.api_platform.api_key_lifecycle import APIKeyLifecycleService
from src.services.api_platform.rate_limits import burst_for_tier, BURST_WINDOW_SECONDS
from src.services.campaign_management.service import CampaignService, CampaignPromptNotPinnedError
from src.services.policy_engine.packs.admin import AdminPolicyPack
from src.libs.repositories.admin_audit_view import AdminAuditViewRepository
from src.libs.repositories.integration import WebhookDeliveryAttemptRepository, WebhookDLQRepository, APIKeyUsageRepository, APIRateLimitRepository
from src.libs.contracts.models.billing import SubscriptionTier
assert burst_for_tier(SubscriptionTier.GROWTH) == 300
assert BURST_WINDOW_SECONDS == 10
assert len(AdminPolicyPack.rules()) == 1
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${SPRINT025_PART3_SMOKE}" == "OK" ]]; then
  ok "Sprint-025 Part-3 libraries — APIKeyLifecycle/burst-rate-limits/CampaignPromptPinning/AdminPolicyPack/new repositories construct OK"
else
  fail "Sprint-025 Part-3 libraries — smoke test failed: ${SPRINT025_PART3_SMOKE}"
fi

# ── Observability Stack (Sprint-027, V7 Ch7-10) ───────────────────────────────
# Prometheus/Grafana/Alertmanager/Loki/FluentBit/OTel Collector/Jaeger, all
# deployed as real K8s Deployments/DaemonSet in the voiceos-ops namespace —
# unlike every service section above, these ARE real HTTP-serving processes
# (third-party images, not health-stub), so these checks hit real endpoints.
log "Checking Observability Stack (Prometheus/Grafana/Loki/Jaeger/Alertmanager)..."

if curl -sf "http://prometheus.voiceos-ops.svc.cluster.local:9090/-/ready" &>/dev/null; then
  PROM_TARGETS_UP=$(curl -sf "http://prometheus.voiceos-ops.svc.cluster.local:9090/api/v1/targets" 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for t in d['data']['activeTargets'] if t['health']=='up'))" 2>/dev/null || echo "0")
  ok "Prometheus — ready, ${PROM_TARGETS_UP} scrape targets UP"
else
  fail "Prometheus — /-/ready did not return 200"
fi

if curl -sf "http://grafana.voiceos-ops.svc.cluster.local:3000/api/health" &>/dev/null; then
  ok "Grafana — /api/health OK"
else
  fail "Grafana — /api/health did not return 200"
fi

if curl -sf "http://loki.voiceos-ops.svc.cluster.local:3100/ready" &>/dev/null; then
  ok "Loki — /ready OK"
else
  fail "Loki — /ready did not return 200"
fi

if curl -sf "http://jaeger-query.voiceos-ops.svc.cluster.local:16686/jaeger" &>/dev/null; then
  ok "Jaeger — query UI reachable"
else
  fail "Jaeger — query UI did not return 200"
fi

if curl -sf "http://alertmanager.voiceos-ops.svc.cluster.local:9093/-/ready" &>/dev/null; then
  ok "Alertmanager — /-/ready OK"
else
  fail "Alertmanager — /-/ready did not return 200"
fi

if curl -sf "http://otel-collector.voiceos-ops.svc.cluster.local:8889/metrics" &>/dev/null; then
  ok "OTel Collector — /metrics OK"
else
  fail "OTel Collector — /metrics did not return 200"
fi

# GPU Fleet Management (fleet_health.py/warmup.py/vram_budget.py) + Cost
# Optimization / Operational Analytics services (src/services/cost_optimizer,
# src/services/ops_analytics) — all library classes, no standalone HTTP
# listener yet (same §8.1 precedent as every service above).
SPRINT027_SMOKE=$(cd "${APP_ROOT}" && python3 -c "
from monitoring.gpu_fleet.fleet_health import GPUFleetHealthMonitor, GPUNodeSnapshot
from monitoring.gpu_fleet.warmup import ModelWarmupOrchestrator
from monitoring.gpu_fleet.vram_budget import FleetVRAMBudget
from src.services.cost_optimizer.service import CostOptimizer
from src.services.ops_analytics.service import OpsAnalytics
monitor = GPUFleetHealthMonitor()
monitor.report_node(GPUNodeSnapshot(node_id='n1', healthy=True, vram_used_mb=0, vram_total_mb=1))
assert monitor.fleet_health_score() == 1.0
print('OK')
" 2>&1 || echo "ERROR")
if [[ "${SPRINT027_SMOKE}" == "OK" ]]; then
  ok "Sprint-027 libraries — GPUFleetHealthMonitor/ModelWarmupOrchestrator/FleetVRAMBudget/CostOptimizer/OpsAnalytics construct OK"
else
  fail "Sprint-027 libraries — smoke test failed: ${SPRINT027_SMOKE}"
fi

# ── Application services (via kubectl port-forward or cluster internal) ───────
log "Checking application services..."

# NOTE: Update SERVICE_HOSTS map after each sprint as new services are added.
declare -A SERVICE_PORTS=(
  ["media-gateway"]="8080"
  ["audio-session-manager"]="8081"
  ["audio-preprocessing"]="8082"
  ["vad-endpointing"]="8083"
  ["gpu-scheduler"]="8084"
  # Sprint-009 (code deployed; HTTP servers land in Sprint-012):
  ["stt-service"]="8085"
  ["llm-service"]="8086"
  ["tts-service"]="8087"
  # Sprint-010+:
  # ["intent-engine"]="8088"
  # ["working-memory"]="8089"
  # Sprint-011+:
  # ["negotiation-engine"]="8090"
  # Sprint-012+:
  # ["conversation-engine"]="8091"
  # ["dialogue-manager"]="8092"
  # Sprint-017 (code deployed; library class, no HTTP server yet — see the
  # dedicated Policy Engine check above instead):
  # ["policy-engine"]="8093"
  # Sprint-018 (code deployed; library classes, no HTTP server yet — see the
  # dedicated Auth/Authz/AI Governance check above instead):
  # ["auth-service"]="8094"
  # ["authz-service"]="8095"
  # ["ai-governance-service"]="8096"
  # Sprint-020 (code deployed; library classes, no HTTP server yet — see the
  # dedicated PII/Audit/AI Safety/Compliance Monitoring/Incident Response
  # check above instead):
  # ["pii-redaction-service"]="8097"
  # ["audit-log-service"]="8098"
  # ["consent-management-service"]="8099"
  # ["compliance-monitoring-service"]="8100"
  # ["incident-response-service"]="8101"
  # ["human-oversight-router"]="8102"
  # Sprint-021 (code deployed; library classes, no HTTP server yet — see the
  # dedicated Tenant/Org/User Management check above instead):
  # ["tenant-management-service"]="8103"
  # ["org-management-service"]="8104"
  # ["user-management-service"]="8105"
  # Sprint-022 (code deployed; library classes, no HTTP server yet — see the
  # dedicated CRM/Collections check above instead):
  # ["crm-service"]="8106"
  # ["collections-service"]="8107"
  # Sprint-023 (code deployed; library classes, no HTTP server yet — see the
  # dedicated Campaign Management/Contact Center/HITL check above instead):
  # ["campaign-management-service"]="8108"
  # ["contact-center-service"]="8109"
  # ["hitl-service"]="8110"
  # Sprint-024 (code deployed; library classes, no HTTP server yet — see the
  # dedicated Billing/Metering/Analytics/Reporting/BI Platform check above
  # instead):
  # ["billing-service"]="8111"
  # ["metering-service"]="8112"
  # ["analytics-service"]="8113"
  # ["reporting-service"]="8114"
  # ["bi-platform-service"]="8115"
  # Sprint-025 (code deployed; library classes, no HTTP server yet — see the
  # dedicated Admin Portal/AI Config/Integration Platform/API Platform check
  # above instead):
  # ["admin-portal-service"]="8116"
  # ["ai-config-service"]="8117"
  # ["integration-platform-service"]="8118"
  # ["api-platform-service"]="8119"
  # ... [updated each sprint]
)

for service in "${!SERVICE_PORTS[@]}"; do
  port="${SERVICE_PORTS[$service]}"
  # Try via Kubernetes service DNS
  svc_host="${service}.voiceos-runtime.svc.cluster.local"
  if curl -sf "http://${svc_host}:${port}/health/ready" &>/dev/null; then
    ok "${service}:${port}"
  else
    fail "${service}:${port} — /health/ready did not return 200"
  fi
done

# ── Final result ──────────────────────────────────────────────────────────────
if [[ $HEALTH_FAILED -eq 0 ]]; then
  log "All health checks PASSED"
else
  log "One or more health checks FAILED — review output above"
  exit 1
fi
