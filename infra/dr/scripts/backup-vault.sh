#!/usr/bin/env bash
# VoiceOS v2 -- Vault file-backend snapshot (A9).
#
# Single-node Vault is a file-backend at /opt/vault/data. `vault operator
# raft snapshot` does NOT apply here (that's for the integrated Raft
# storage backend). For file storage the correct backup is:
#
#   1. Seal Vault briefly OR read /opt/vault/data during a quiescent window,
#      because Vault writes atomically per-file — a tar taken while Vault
#      is writing captures a valid, consistent-on-disk state as long as no
#      write is mid-flight for a specific key. Sealing is the conservative
#      option; skipping it is acceptable for point-in-time snapshots taken
#      during known-low-write windows (nightly).
#
#   2. tar+gzip the storage dir + init.json + config.hcl + unseal keys
#      (unseal keys are the RECOVERY PATH — if you lose them, an untarred
#      backup is unusable, since Vault refuses to serve until unsealed).
#
# This script implements option (2) with an optional --seal flag.
#
# Usage:
#   sudo bash infra/dr/scripts/backup-vault.sh
#   sudo bash infra/dr/scripts/backup-vault.sh --seal    # safest
#   sudo bash infra/dr/scripts/backup-vault.sh --restore <archive.tar.gz>
#
# Output: /opt/voiceos/backups/vault/vault-YYYYMMDDHHMMSS.tar.gz
#
# Companion: infra/dr/runbooks/vault-snapshot.md
set -euo pipefail

BACKUP_DIR="${VAULT_BACKUP_DIR:-/opt/voiceos/backups/vault}"
VAULT_DATA_DIR="${VAULT_DATA_DIR:-/opt/vault/data}"
VAULT_CONFIG_DIR="${VAULT_CONFIG_DIR:-/etc/vault.d}"
VAULT_INIT_FILE="${VAULT_INIT_FILE:-/opt/vault/init.json}"
DO_SEAL=0
RESTORE_ARCHIVE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seal) DO_SEAL=1; shift ;;
    --restore) RESTORE_ARCHIVE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

log() { echo "[backup-vault] $(date -u +%FT%TZ) $*"; }

# ── Restore path ─────────────────────────────────────────────────────────────
if [[ -n "${RESTORE_ARCHIVE}" ]]; then
  if [[ ! -f "${RESTORE_ARCHIVE}" ]]; then
    log "ERROR: archive '${RESTORE_ARCHIVE}' not found."
    exit 1
  fi
  log "Restoring Vault from ${RESTORE_ARCHIVE}."
  log "Stopping vault.service..."
  systemctl stop vault.service || true
  log "Backing up current /opt/vault/data to /opt/vault/data.pre-restore-$(date +%s)..."
  if [[ -d "${VAULT_DATA_DIR}" ]]; then
    mv "${VAULT_DATA_DIR}" "${VAULT_DATA_DIR}.pre-restore-$(date +%s)"
  fi
  log "Extracting archive into /..."
  tar -xzpf "${RESTORE_ARCHIVE}" -C /
  log "Restarting vault.service..."
  systemctl start vault.service
  sleep 3
  log "Vault status (expect 'Sealed=true' until you unseal):"
  vault status || true
  log "Now unseal:  vault operator unseal <key1>; vault operator unseal <key2>; vault operator unseal <key3>"
  log "Or use the systemd oneshot: systemctl start voiceos-vault-unseal.service"
  exit 0
fi

# ── Backup path ──────────────────────────────────────────────────────────────
mkdir -p "${BACKUP_DIR}"
ARCHIVE="${BACKUP_DIR}/vault-$(date +%Y%m%d%H%M%S).tar.gz"

if [[ "${DO_SEAL}" -eq 1 ]]; then
  log "Sealing Vault (safest — writes are quiesced during tar)..."
  : "${VAULT_ADDR:?VAULT_ADDR must be set when --seal is used}"
  : "${VAULT_TOKEN:?VAULT_TOKEN must be set (root or an admin token)}"
  vault operator seal
  SEALED=1
else
  SEALED=0
  log "Not sealing — tar will run against a live file-backend. Do this only during a low-write window."
fi

log "Creating archive ${ARCHIVE}..."
INCLUDES=("${VAULT_DATA_DIR}" "${VAULT_CONFIG_DIR}")
[[ -f "${VAULT_INIT_FILE}" ]] && INCLUDES+=("${VAULT_INIT_FILE}")

tar -czpf "${ARCHIVE}" "${INCLUDES[@]}" 2>/dev/null

log "Archive size: $(du -h "${ARCHIVE}" | awk '{print $1}')"
log "Verifying archive integrity (tar tzf listing)..."
tar -tzf "${ARCHIVE}" >/dev/null

if [[ "${SEALED}" -eq 1 ]]; then
  log "Unsealing Vault to restore service..."
  systemctl start voiceos-vault-unseal.service 2>/dev/null || \
    log "voiceos-vault-unseal.service unavailable — unseal manually with 'vault operator unseal <key>' x3."
fi

log "Retention: caller is responsible for pruning ${BACKUP_DIR} — no auto-rotation here."
log "SHA256: $(sha256sum "${ARCHIVE}" | awk '{print $1}')"
log "DONE. Store ${ARCHIVE} off-box (S3/scp) — a Vault backup collocated with Vault is not a backup."
