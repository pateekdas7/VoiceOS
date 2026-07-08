#!/usr/bin/env bash
# ==============================================================================
# VoiceOS Vault Bootstrap (Sprint-019, V4 Ch7)
#
# Idempotent install + configure + initialize of a self-hosted HashiCorp
# Vault instance for a single-node, non-HA deployment. No cloud Vault/KMS
# account exists for this project — same self-managed-infrastructure
# precedent as scripts/pki/generate_mtls_certs.py's self-managed mTLS CA.
#
# This closes a real reproducibility gap found during a Sprint-019 Phase 2
# post-hoc audit: Vault was originally installed/configured entirely by
# hand (interactively) with no script capturing the steps — see
# CPU_NODE_STATE.md §7.8/§18 for the full incident writeup (the container's
# capability bounding set excludes cap_ipc_lock, which the Vault package's
# postinstall sets as a file capability, so the kernel refuses to exec the
# binary at all until that capability is stripped).
#
# Usage:
#   sudo bash scripts/vault/bootstrap_vault.sh
#
# Safe to re-run: every step checks current state before acting. The one
# genuinely one-time, non-idempotent action is `vault operator init` —
# guarded by checking for /opt/vault/init.json first, since re-running init
# against an already-initialized Vault would fail loudly anyway (Vault
# itself refuses to double-initialize), but we check first to avoid the
# confusing error and to make the "already done" case a clean no-op log line.
#
# Output: /opt/vault/init.json (unseal key + root token, mode 600) and
# /opt/vault/app_token.json (voiceos-app-scoped renewable token, mode 600).
# BOTH FILES MUST BE BACKED UP SECURELY OFF-NODE — losing init.json with no
# other unseal key means the Vault data at /opt/vault/data becomes
# permanently unrecoverable.
# ==============================================================================

set -euo pipefail

VAULT_HOME="/opt/vault"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] VAULT $*"; }

# /sys/health intentionally returns non-2xx for expected states (501 Not
# Initialized, 503 Sealed, 429 Standby) — `curl -f` treats any of those as
# failure even though Vault answered correctly. Only a real connection
# failure (curl exit 7) means Vault isn't up.
vault_reachable() {
  curl -s -o /dev/null "${VAULT_ADDR}/v1/sys/health"
  [[ $? -ne 7 ]]
}

# ── 1. Install Vault (idempotent) ────────────────────────────────────────────
if ! command -v vault &>/dev/null; then
  log "Installing HashiCorp Vault..."
  wget -q -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/hashicorp.list
  apt-get update -qq
  apt-get install -y -qq vault
  log "Installed: $(vault --version)"
else
  log "Vault already installed: $(vault --version)"
fi

# ── 2. Container fix: strip cap_ipc_lock if it blocks exec ──────────────────
# Only relevant in containers whose capability bounding set excludes
# cap_ipc_lock (common under containerd/K8s) — see the module docstring.
# Detection: try executing `vault --version`; a bare file-capability EPERM
# manifests as a non-zero exit with no output at all.
if getcap /usr/bin/vault 2>/dev/null | grep -q cap_ipc_lock; then
  if ! /usr/bin/vault --version &>/dev/null; then
    log "vault --version fails with cap_ipc_lock set — stripping it (disable_mlock is already set in vault.hcl below)"
    setcap -r /usr/bin/vault
  fi
fi

# ── 3. Directories + config (idempotent — always resync from the repo template) ──
mkdir -p "${VAULT_HOME}"/{data,config,logs}
id vault &>/dev/null && chown -R vault:vault "${VAULT_HOME}/data" "${VAULT_HOME}/logs" 2>/dev/null || true
cp "${SCRIPT_DIR}/vault.hcl" "${VAULT_HOME}/config/vault.hcl"
cp "${SCRIPT_DIR}/voiceos-app-policy.hcl" "${VAULT_HOME}/config/voiceos-app-policy.hcl"
id vault &>/dev/null && chown vault:vault "${VAULT_HOME}/config/vault.hcl" 2>/dev/null || true

# ── 4. Start Vault (no systemd/PID-1 in this container — see CPU_NODE_STATE.md §18) ──
if ! vault_reachable; then
  log "Starting Vault server..."
  if id vault &>/dev/null; then
    su -s /bin/bash vault -c "setsid /usr/bin/vault server -config=${VAULT_HOME}/config/vault.hcl > ${VAULT_HOME}/logs/vault.log 2>&1 < /dev/null &"
  else
    setsid /usr/bin/vault server -config="${VAULT_HOME}/config/vault.hcl" > "${VAULT_HOME}/logs/vault.log" 2>&1 < /dev/null &
  fi
  sleep 3
  vault_reachable || { log "ERROR: Vault did not come up — check ${VAULT_HOME}/logs/vault.log"; exit 1; }
else
  log "Vault already running at ${VAULT_ADDR}"
fi
export VAULT_ADDR

# ── 5. Initialize (one-time, ever — guarded) ─────────────────────────────────
if [[ ! -f "${VAULT_HOME}/init.json" ]]; then
  log "Initializing Vault (single key-share/threshold — appropriate for this single-node, non-HA deployment)..."
  vault operator init -key-shares=1 -key-threshold=1 -format=json > "${VAULT_HOME}/init.json"
  chmod 600 "${VAULT_HOME}/init.json"
  log "!!! ${VAULT_HOME}/init.json holds the unseal key + root token — back it up securely off-node NOW. !!!"
  log "!!! Losing it with Vault sealed means /opt/vault/data becomes permanently unrecoverable. !!!"
else
  log "Vault already initialized (${VAULT_HOME}/init.json present)"
fi

# ── 6. Unseal if needed ───────────────────────────────────────────────────────
# `vault status` itself exits 2 when sealed (by CLI convention) — under
# `pipefail` that poisons `| grep -q`'s pipeline exit status regardless of
# what grep matches, so capture the output first and grep the variable.
VAULT_STATUS_OUTPUT="$(vault status 2>&1 || true)"
if echo "${VAULT_STATUS_OUTPUT}" | grep -q "^Sealed.*true"; then
  log "Unsealing Vault..."
  UNSEAL_KEY="$(python3 -c "import json; print(json.load(open('${VAULT_HOME}/init.json'))['unseal_keys_b64'][0])")"
  vault operator unseal "${UNSEAL_KEY}"
else
  log "Vault already unsealed"
fi

ROOT_TOKEN="$(python3 -c "import json; print(json.load(open('${VAULT_HOME}/init.json'))['root_token'])")"
export VAULT_TOKEN="${ROOT_TOKEN}"

# ── 7. Enable secrets engines (idempotent) ───────────────────────────────────
vault secrets list -format=json | python3 -c "import json,sys; sys.exit(0 if 'secret/' in json.load(sys.stdin) else 1)" \
  || { log "Enabling KV v2 at secret/..."; vault secrets enable -path=secret -version=2 kv; }
vault secrets list -format=json | python3 -c "import json,sys; sys.exit(0 if 'transit/' in json.load(sys.stdin) else 1)" \
  || { log "Enabling Transit at transit/..."; vault secrets enable transit; }

# ── 8. App policy + scoped token (idempotent) ────────────────────────────────
vault policy write voiceos-app "${VAULT_HOME}/config/voiceos-app-policy.hcl"
if [[ ! -f "${VAULT_HOME}/app_token.json" ]]; then
  log "Issuing voiceos-app-scoped token (768h period, renewable)..."
  vault token create -policy=voiceos-app -period=768h -format=json > "${VAULT_HOME}/app_token.json"
  chmod 600 "${VAULT_HOME}/app_token.json"
else
  log "App token already issued (${VAULT_HOME}/app_token.json present)"
fi

log "Vault bootstrap complete. VAULT_ADDR=${VAULT_ADDR}"
log "Next: run scripts/vault/provision_datastore_auth.sh to seed Postgres/Redis/MongoDB"
log "credentials into Vault and enforce Redis/MongoDB auth, then:"
log "  source <(python3 scripts/vault/gen_env.py)"
