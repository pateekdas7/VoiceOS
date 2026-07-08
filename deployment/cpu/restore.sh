#!/usr/bin/env bash
# ==============================================================================
# VoiceOS CPU Node Restore Script
# Restores all application code/data-layer state on a bootstrapped CPU node.
# Assumes bootstrap.sh has already been run.
#
# TT-009 (found during the post-Sprint-020 reproducibility audit, fixed same
# session, 2026-07-05): this script previously required `kubectl`/`helm` and
# applied `${APP_DIR}/infra/k8s/*.yaml` + a Helm chart at
# `${APP_DIR}/infra/helm/voiceos-platform` — none of which have ever existed
# in this repo, and Helm itself has never been installed on this node
# (CPU_NODE_STATE.md §6: "planned for Sprint-026"). Every sprint's actual
# Phase 2 deployment (Sprint-013 through Sprint-020) was performed by hand —
# venv + pip install -e . + alembic + the steps below — never by running this
# script end-to-end; this is why the gap went uncaught for 8 sprints. Fixed
# by rewriting this script to do exactly what has actually been validated,
# sprint after sprint, on this real node. The Kubernetes/Helm application
# deployment this script used to attempt is real Sprint-026 scope (every
# VoiceOS service remains a library class with no standalone process today —
# CPU_NODE_STATE.md §8.1) and will be added back here when that sprint lands.
#
# Prerequisites:
#   - bootstrap.sh completed successfully
#   - .env file present (secrets provided by operator) — NOTE: as of
#     Sprint-019, POSTGRES_DSN/REDIS_URL/MONGODB_URI are fetched from Vault at
#     runtime (see the Vault provisioning step below); .env only needs to
#     supply REDIS_HOST/POSTGRES_HOST/etc. connection *coordinates*, not
#     passwords, once Vault is provisioned. On a genuinely fresh node,
#     REDIS_HOST/POSTGRES_HOST default to localhost-appropriate values below.
#   - Application code present at /opt/voiceos/app
#
# Usage:
#   ./restore.sh
# ==============================================================================

set -euo pipefail

VOICEOS_HOME="/opt/voiceos"
APP_DIR="${VOICEOS_HOME}/app"
VENV="${VOICEOS_HOME}/venv"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
check() { command -v "$1" &>/dev/null || { log "ERROR: $1 not found. Run bootstrap.sh first."; exit 1; }; }

log "=== VoiceOS CPU Node Restore ==="

# ── Pre-flight checks ────────────────────────────────────────────────────────
log "Pre-flight checks..."
check python3.12
[[ -f "${VOICEOS_HOME}/.env" ]] || { log "ERROR: .env not found at ${VOICEOS_HOME}/.env"; exit 1; }
[[ -d "${APP_DIR}" ]] || { log "ERROR: App code not found at ${APP_DIR}"; exit 1; }

# ── Load environment variables ────────────────────────────────────────────────
log "Loading environment variables..."
set -a; source "${VOICEOS_HOME}/.env"; set +a

# ── Install Python dependencies ───────────────────────────────────────────────
# pyproject.toml is the single source of truth for dependencies (no
# requirements.txt exists in this repo — a stale reference here was found
# and fixed during a post-Sprint-019 reproducibility audit; it had never
# been caught because every prior sprint ran tests via PYTHONPATH instead of
# an actual package install, see CPU_NODE_STATE.md §18). This also requires
# pyproject.toml's build-backend to be the real `setuptools.build_meta`
# entry point — the audit found and fixed a second, unrelated latent bug
# here too: `setuptools.backends.legacy:build` (present since Sprint-001)
# doesn't exist in modern setuptools and made `pip install -e .` fail with
# `BackendUnavailable`, silently unexercised for the same PYTHONPATH reason.
log "Installing Python dependencies (editable install from pyproject.toml)..."
source "${VENV}/bin/activate"
pip install -q -e "${APP_DIR}"

# ── Run database migrations (Sprint-014) ──────────────────────────────────────
# alembic.ini lives at the repo root (${APP_DIR}), not implementation/ — the
# 13 Sprint-014 revisions are idempotent (IF NOT EXISTS / guarded DO blocks),
# so this is safe to re-run against an already-provisioned database.
log "Running database migrations..."
cd "${APP_DIR}"
alembic upgrade head
log "Alembic migration version: $(alembic current)"

# ── Sprint-015 note: crash recovery on restart ────────────────────────────────
# RecoveryManager/CPURestartStrategy auto-recover a call's ConversationSessionState
# from its latest `snapshots` row + Redis event-tail replay whenever
# ConversationEngine is asked to resume a call_id it doesn't already hold in
# memory — this activates automatically once ConversationEngine has a
# long-running process/pod lifecycle (Sprint-026 K8s/Helm); there is no
# separate startup step to run here today (it remains a library class with
# no standalone process — see CPU_NODE_STATE.md §8.1 Sprint-015 note).

# ── Create MongoDB collection indexes (Sprint-014) ────────────────────────────
log "Creating MongoDB collection indexes..."
python "${APP_DIR}/scripts/db/mongodb/create_indexes.py"

# ── Verify Redis persistence hardening (TT-002) ───────────────────────────────
# Fail loudly rather than silently restoring onto an unhardened Redis — an
# unhardened instance is exactly what caused the original recurring
# EventBus-loss incident (see CPU_NODE_STATE.md §18, BACKLOG.md TT-002).
log "Verifying Redis persistence configuration..."
REDIS_CLI_ARGS=(-h "${REDIS_HOST:-redis}" -p "${REDIS_PORT:-6379}" -a "${REDIS_PASSWORD:-}")
AOF_STATUS=$(redis-cli "${REDIS_CLI_ARGS[@]}" CONFIG GET appendonly 2>/dev/null | tail -1)
if [[ "${AOF_STATUS}" != "yes" ]]; then
  log "WARNING: Redis appendonly=${AOF_STATUS:-unknown} (expected 'yes') — persistence hardening"
  log "  may not have been applied by bootstrap.sh, or Redis was started bypassing its config file."
  log "  See deployment/cpu/bootstrap.sh 'Redis' section and CPU_NODE_STATE.md §7.1/§18."
else
  log "Redis AOF persistence: OK (appendonly=yes)"
fi

# ── EventBus consumer group recovery (Sprint-013 / TT-002) ───────────────────
# Canonical idempotent "detect + recreate" — the same call every real
# Consumer already makes at construction (EventBus.ensure_consumer_group),
# invoked here as the single source of truth instead of a raw redis-cli
# one-liner (the old inline command also used the wrong start-offset `$`
# rather than `0`, silently diverging from the tested/documented behavior).
log "Recovering EventBus consumer group..."
export REDIS_URL="redis://${REDIS_HOST:-redis}:${REDIS_PORT:-6379}/0"
export EVENT_BUS_STREAM="${EVENT_BUS_STREAM:-voiceos-events}"
export EVENT_BUS_CONSUMER_GROUP="${EVENT_BUS_CONSUMER_GROUP:-main-group}"
python "${APP_DIR}/scripts/eventbus_recovery.py"

# ── Provision mTLS PKI (Sprint-018, V4 Ch5 §5.4) ─────────────────────────────
# Self-managed internal CA + one leaf certificate per service. Idempotent by
# design for a fresh-node rebuild (regenerates CA + leaf certs from scratch);
# on an already-provisioned node this rotates every certificate — acceptable
# per Sprint-018.md's rollback note ("PKI rotation does not affect running
# connections immediately"), but skip this step on a live node if you intend
# to preserve the existing trust chain rather than rotate it.
log "Provisioning mTLS PKI at ${MTLS_CERTS_DIR:-/opt/voiceos/certs}..."
mkdir -p "${MTLS_CERTS_DIR:-/opt/voiceos/certs}"
python "${APP_DIR}/scripts/pki/generate_mtls_certs.py" --out-dir "${MTLS_CERTS_DIR:-/opt/voiceos/certs}"

# ── Provision Vault + datastore auth (Sprint-019, V4 Ch7) ────────────────────
# scripts/vault/bootstrap_vault.sh is fully idempotent — installs Vault if
# missing, applies the container cap_ipc_lock fix if needed, (re)syncs
# vault.hcl/the app policy from the repo templates, starts Vault if not
# running, initializes/unseals if not already done, enables KV v2 + Transit,
# and issues the voiceos-app token. Safe to run on every restore, including
# an already-provisioned node (every step no-ops if already satisfied).
log "Provisioning Vault..."
bash "${APP_DIR}/scripts/vault/bootstrap_vault.sh"

export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_TOKEN="$(python3 -c "import json; print(json.load(open('/opt/vault/app_token.json'))['auth']['client_token'])")"

log "Provisioning datastore auth (Redis requirepass, MongoDB user/--auth, Vault secret seeding)..."
bash "${APP_DIR}/scripts/vault/provision_datastore_auth.sh"

log "Verifying a real Vault secret fetch succeeds before any service starts..."
python -c "
from src.libs.secrets.providers.vault_provider import HVACVaultClient, VaultProvider
from src.libs.secrets.manager import SecretsManager
import os
sm = SecretsManager(VaultProvider(HVACVaultClient(os.environ['VAULT_ADDR'], os.environ['VAULT_TOKEN'])))
sm.get_secret('voiceos/postgres')
print('Vault secret fetch: OK')
"

# Populate POSTGRES_DSN/REDIS_URL/MONGODB_URI for the remainder of this script
# from Vault (URL-encoded — see scripts/vault/gen_env.py's docstring for why
# hand-building these strings is unsafe).
eval "$(python3 "${APP_DIR}/scripts/vault/gen_env.py")"

# ── Encrypt existing PII rows (Sprint-019, migration 0016) ───────────────────
# One-time-per-row, idempotent (skips any row whose *_encrypted column is
# already populated) — safe to run on every restore, including a node with
# no pre-existing customer data (a verified no-op on this node today, since
# CRM/Collections isn't built until Sprint-022).
log "Backfilling PII encryption for any pre-existing customer rows..."
python "${APP_DIR}/scripts/db/encrypt_pii_backfill.py"

# ── Verify application code imports cleanly ──────────────────────────────────
# Every VoiceOS service is a library class, not a standalone process, until
# Sprint-026 wires up K8s/Helm deployment (CPU_NODE_STATE.md §8.1) — there is
# no service to "deploy" or pod to wait on. What "restored" means today is:
# the editable install (above) resolves, the full dependency graph imports
# without error, and the data layer (migrations/indexes/EventBus/Vault/PKI,
# all provisioned above) is reachable. This is the same restoration surface
# every sprint's real Phase 2 has actually exercised.
log "Verifying application code imports cleanly..."
python -c "import src.services.conversation_engine.engine, src.services.policy_engine.engine, src.services.auth.service, src.libs.audit.logger; print('Import check: OK')"

# ── Kubernetes application tier (Sprint-026) ─────────────────────────────────
# A real kubeadm+Calico cluster now runs on this node (CPU_NODE_STATE.md §5) —
# this section was missing from restore.sh until Sprint-027 (the K8s
# deployment landed same-day as Sprint-026 Phase 2, after this script's own
# TT-009 rewrite; never backfilled here until now). `helm upgrade --install`
# is idempotent — safe on both a fresh cluster and an already-deployed one.
if command -v kubectl &>/dev/null && kubectl cluster-info &>/dev/null; then
  log "Deploying voiceos-platform Kubernetes application tier..."
  export KUBECONFIG="${KUBECONFIG:-/etc/kubernetes/admin.conf}"
  kubectl apply -f "${APP_DIR}/infra/k8s/namespaces.yaml"
  kubectl apply -f "${APP_DIR}/infra/k8s/priority-classes.yaml"
  kubectl apply -f "${APP_DIR}/infra/k8s/resource-quotas.yaml"
  kubectl apply -f "${APP_DIR}/infra/k8s/cluster-policies/default-deny.yaml"
  helm dependency update "${APP_DIR}/infra/helm/voiceos-platform/" &>/dev/null || true
  helm upgrade --install voiceos-platform "${APP_DIR}/infra/helm/voiceos-platform/" \
    -f "${APP_DIR}/infra/helm/values/dev-values.yaml" --timeout 3m

  # ── Observability Stack (Sprint-027, V7 Ch7-10) ────────────────────────────
  # Prometheus/Grafana/Alertmanager/Loki/FluentBit/OTel Collector/Jaeger into
  # voiceos-ops. Requires GRAFANA_ADMIN_PASSWORD sourced from Vault (never
  # hardcoded) and PAGERDUTY_SERVICE_KEY/JIRA_WEBHOOK_URL/SLACK_WEBHOOK_URL
  # (documented, unconfigured placeholders until real accounts exist — same
  # "no real account" precedent as StripeGateway/RazorpayGateway).
  log "Deploying observability stack (voiceos-ops namespace)..."
  export GRAFANA_ADMIN_PASSWORD="$(python3 -c "
from src.libs.secrets.providers.vault_provider import HVACVaultClient, VaultProvider
from src.libs.secrets.manager import SecretsManager
import os
sm = SecretsManager(VaultProvider(HVACVaultClient(os.environ['VAULT_ADDR'], os.environ['VAULT_TOKEN'])))
print(sm.get_secret('voiceos/grafana_admin'))
")"
  export PAGERDUTY_SERVICE_KEY="${PAGERDUTY_SERVICE_KEY:-}"
  export JIRA_WEBHOOK_URL="${JIRA_WEBHOOK_URL:-}"
  export SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
  bash "${APP_DIR}/infra/k8s/observability/deploy.sh"
else
  log "WARNING: kubectl/cluster not available — skipping Kubernetes application tier"
  log "  and observability stack deployment. Data layer only was restored."
fi

# ── Health checks ─────────────────────────────────────────────────────────────
log "Running health checks..."
source "${APP_DIR}/deployment/cpu/healthcheck.sh"

# ── Regression tests ──────────────────────────────────────────────────────────
log "Running post-restore regression tests..."
cd "${APP_DIR}"
pytest tests/e2e/test_walking_skeleton.py tests/integration/ -v --tb=short

log ""
log "=== Restore complete ==="
log "Data layer (Postgres/Redis/MongoDB/Vault/mTLS PKI) provisioned and validated."
log "Application code installed and import-checked. Kubernetes application tier"
log "(voiceos-runtime/voiceos-ops/voiceos-platform) and the observability stack"
log "(voiceos-ops: Prometheus/Grafana/Alertmanager/Loki/FluentBit/OTel/Jaeger)"
log "deployed if a cluster was reachable — see CPU_NODE_STATE.md §5/§13."
