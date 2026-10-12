#!/usr/bin/env bash
# ==============================================================================
# VoiceOS Twilio Credentials Provisioning (Client-1 pilot, A7)
#
# Idempotent: seeds Twilio account_sid + auth_token (+ optional
# messaging_service_sid) into Vault KV at `secret/voiceos/twilio`.
# Reads from the standard `secret/voiceos/*` mount so the existing
# `voiceos-app-policy` (scripts/vault/voiceos-app-policy.hcl) already
# grants read access — no policy change required.
#
# Run after scripts/vault/bootstrap_vault.sh and once the Client-1
# Twilio production account is issued (do NOT seed sandbox creds here —
# sandbox stays in developer .env, per CLAUDE.md Security).
#
# Usage:
#   export VAULT_ADDR=http://127.0.0.1:8200
#   export VAULT_TOKEN=<voiceos-app token, e.g. from /opt/vault/app_token.json>
#   bash scripts/vault/provision_twilio_credentials.sh
#
# Consumed by: src/services/media_gateway/auth.py, src/services/web_api/main.py
# via TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN env vars exported by
# scripts/vault/gen_env.py.
# ==============================================================================

set -euo pipefail

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] TWILIO-CREDS $*"; }

: "${VAULT_ADDR:?VAULT_ADDR must be set}"
: "${VAULT_TOKEN:?VAULT_TOKEN must be set (voiceos-app token)}"

# ── Twilio account SID + auth token (mandatory) ─────────────────────────────
# Both are issued by Twilio at account creation. Auth token rotates on demand;
# use `vault kv put ... auth_token=<new>` to rotate — every consumer reads
# via gen_env.py at process start, so a restart picks up the new value.
if ! vault kv get secret/voiceos/twilio &>/dev/null; then
  echo "secret/voiceos/twilio is not set."
  read -r -p "Twilio Account SID (starts with 'AC'): " TW_SID
  read -r -s -p "Twilio Auth Token: " TW_TOKEN; echo
  read -r -p "Twilio Messaging Service SID (optional, starts with 'MG', or blank): " TW_MSG

  if [[ ! "${TW_SID}" =~ ^AC[A-Za-z0-9]{32}$ ]]; then
    log "ERROR: Account SID must match ^AC[A-Za-z0-9]{32}$. Aborting."
    exit 1
  fi
  if [[ -z "${TW_TOKEN}" ]]; then
    log "ERROR: Auth token cannot be empty. Aborting."
    exit 1
  fi

  if [[ -n "${TW_MSG}" ]]; then
    if [[ ! "${TW_MSG}" =~ ^MG[A-Za-z0-9]{32}$ ]]; then
      log "ERROR: Messaging Service SID must match ^MG[A-Za-z0-9]{32}$. Aborting."
      exit 1
    fi
    vault kv put secret/voiceos/twilio \
      account_sid="${TW_SID}" \
      auth_token="${TW_TOKEN}" \
      messaging_service_sid="${TW_MSG}" >/dev/null
  else
    vault kv put secret/voiceos/twilio \
      account_sid="${TW_SID}" \
      auth_token="${TW_TOKEN}" >/dev/null
  fi

  unset TW_SID TW_TOKEN TW_MSG
  log "Twilio credentials seeded into Vault at secret/voiceos/twilio."
else
  log "secret/voiceos/twilio already set — skipping."
  log "To rotate the auth token: vault kv patch secret/voiceos/twilio auth_token=<new>"
fi

log "Done. Restart media-gateway/web-api so gen_env.py picks up the new values:"
log "  systemctl restart voiceos-media-gateway voiceos-webapi"
