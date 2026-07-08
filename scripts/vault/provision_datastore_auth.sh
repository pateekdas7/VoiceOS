#!/usr/bin/env bash
# ==============================================================================
# VoiceOS Datastore Auth Provisioning (Sprint-019, V4 Ch7)
#
# Idempotent: seeds Postgres/Redis/MongoDB passwords into Vault KV
# (secret/voiceos/{postgres,redis,mongodb}) and enforces Redis requirepass +
# MongoDB --auth. Run after scripts/vault/bootstrap_vault.sh.
#
# Closes a reproducibility gap found during a Sprint-019 Phase 2 post-hoc
# audit: this provisioning was originally done entirely by hand (interactive
# `vault kv put`/`redis-cli CONFIG SET`/`mongosh createUser` commands with no
# script capturing them) — see CPU_NODE_STATE.md §7.1/§7.3/§18.
#
# Usage:
#   export VAULT_ADDR=http://127.0.0.1:8200
#   export VAULT_TOKEN=<voiceos-app token, e.g. from /opt/vault/app_token.json>
#   bash scripts/vault/provision_datastore_auth.sh
#
# Postgres password is NOT auto-generated here — it must already exist as
# the real `voiceos` Postgres role's password (set via `CREATE USER ...
# WITH PASSWORD ...`, bootstrap.sh's documented next-step); this script only
# seeds Vault with whatever value you provide, since generating a random one
# here would silently desync from the real DB role password.
# ==============================================================================

set -euo pipefail

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] DATASTORE-AUTH $*"; }

: "${VAULT_ADDR:?VAULT_ADDR must be set}"
: "${VAULT_TOKEN:?VAULT_TOKEN must be set (voiceos-app token)}"

# ── Postgres: seed Vault with the already-provisioned role password ─────────
if ! vault kv get secret/voiceos/postgres &>/dev/null; then
  echo "secret/voiceos/postgres is not set."
  read -r -s -p "Enter the existing 'voiceos' Postgres role password to seed into Vault: " PG_PW
  echo
  vault kv put secret/voiceos/postgres value="${PG_PW}" >/dev/null
  unset PG_PW
  log "Postgres password seeded into Vault."
else
  log "secret/voiceos/postgres already set — skipping (Postgres auth is unaffected by this script either way)."
fi

# ── Redis: generate + seed if absent, apply live via CONFIG SET (no restart) ──
# Detection note (found running this against a real auth-enforced Redis in a
# Sprint-019 post-hoc audit): checking `redis-cli CONFIG GET requirepass`
# unauthenticated doesn't reliably signal "no password set" once a password
# IS set — the NOAUTH error goes to stderr, `2>/dev/null` discards it, and
# the resulting empty stdout is indistinguishable from "no password
# configured". The reliable check is a real authenticated PING: try it with
# the Vault-stored password first (covers "already correctly configured");
# only fall back to assuming "no password yet" if an *unauthenticated* PING
# also succeeds (proving no password is required at all).
if ! vault kv get secret/voiceos/redis &>/dev/null; then
  log "Generating a new Redis password (hex — URL-safe, see CPU_NODE_STATE.md §18 for why base64 is unsafe here)..."
  vault kv put secret/voiceos/redis value="$(openssl rand -hex 24)" >/dev/null
fi
REDIS_PW="$(vault kv get -field=value secret/voiceos/redis)"

# Check the unauthenticated ping FIRST: `redis-cli -a <anything> ping` returns
# PONG even when Redis has no password configured at all (AUTH against a
# server with no requirepass is a harmless no-op/error that redis-cli doesn't
# treat as fatal, and the subsequent PING succeeds regardless of the password
# given) — so an authed-ping-first check produces a false "already matches"
# positive against a genuinely unauthenticated Redis. An unauthenticated PING
# succeeding is the unambiguous signal: no password is enforced right now.
REDIS_NOAUTH_PING="$(redis-cli ping 2>/dev/null)" || true
if echo "${REDIS_NOAUTH_PING}" | grep -q PONG; then
  log "Applying Redis requirepass (live, via CONFIG SET — no restart, no data loss)..."
  redis-cli CONFIG SET requirepass "${REDIS_PW}" >/dev/null
  grep -q '^requirepass' /etc/redis/redis.conf 2>/dev/null || echo "requirepass ${REDIS_PW}" >> /etc/redis/redis.conf
  log "Redis auth enforced."
else
  REDIS_AUTHED_PING="$(redis-cli -a "${REDIS_PW}" --no-auth-warning ping 2>/dev/null)" || true
  if echo "${REDIS_AUTHED_PING}" | grep -q PONG; then
    log "Redis already requires auth and the Vault-stored password matches — nothing to do."
  else
    log "WARNING: Redis already requires a password, but it does NOT match the Vault-stored value."
    log "  Manual reconciliation needed — see CPU_NODE_STATE.md §7.1. Not overwriting a live password blindly."
  fi
fi
unset REDIS_PW

# ── MongoDB: generate + seed if absent; create a scoped user if auth isn't enforced yet ──
# Detection note (found running this against a real auth-enforced MongoDB in
# the same audit): `mongosh ... | grep -q PATTERN` under `set -o pipefail`
# reports the *pipeline's* exit status as mongosh's own non-zero exit (it
# errors by design when unauthenticated) even when grep DID find the
# pattern — pipefail picks the rightmost non-zero exit among the pipeline's
# commands, not simply grep's. Capturing mongosh's output into a variable
# first (with `|| true` so the failing command substitution doesn't abort
# the script under `set -e`) and grepping the variable avoids this entirely.
if ! vault kv get secret/voiceos/mongodb &>/dev/null; then
  log "Generating a new MongoDB password (hex)..."
  vault kv put secret/voiceos/mongodb value="$(openssl rand -hex 24)" >/dev/null
fi
MONGO_PW="$(vault kv get -field=value secret/voiceos/mongodb)"

MONGO_CHECK_OUTPUT="$(mongosh --quiet --eval 'db.adminCommand({listCollections:1})' 2>&1)" || true
if echo "${MONGO_CHECK_OUTPUT}" | grep -q "requires authentication"; then
  log "MongoDB already requires auth — assuming the existing 'voiceos' user matches the Vault-stored password."
else
  log "MongoDB does not require auth yet — creating a readWrite-on-voiceos-only user (deliberately not root/admin)."
  mongosh --quiet --eval "db.getSiblingDB('voiceos').createUser({user: 'voiceos', pwd: '${MONGO_PW}', roles: [{role: 'readWrite', db: 'voiceos'}]})"
  log "User created. MongoDB itself is NOT yet enforcing --auth — restart mongod with --auth added to enforce it:"
  log "  kill \$(pgrep -f 'mongod --dbpath') && mongod --dbpath /var/lib/mongodb --logpath /var/log/mongodb/mongod.log --fork --bind_ip 127.0.0.1 --auth"
  log "  (Not done automatically by this script — restarting a live database process is an operator decision, not an unattended one.)"
fi
unset MONGO_PW

log "Datastore auth provisioning complete. Use scripts/vault/gen_env.py to build connection strings."
