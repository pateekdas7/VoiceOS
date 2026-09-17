#!/usr/bin/env bash
# Phase 16d — Production Rollback
#
# Rolls back to a specified git SHA and restarts all services.
# Full rollback target: < 15 minutes.
#
# Usage:
#   bash scripts/deploy/rollback.sh --to <sha>
#   bash scripts/deploy/rollback.sh --to HEAD~1  [back one commit]
#
# The script will NOT run 'git checkout' by default (destructive).
# Pass --execute to actually perform the rollback.

set -euo pipefail

TARGET_SHA=""
EXECUTE=false
DB_DSN="${DATABASE_URL:-postgresql://localhost/voiceos}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --to)      TARGET_SHA="$2"; shift 2 ;;
    --execute) EXECUTE=true; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$TARGET_SHA" ]]; then
  echo "Usage: $0 --to <sha> [--execute]"
  echo ""
  echo "  Current HEAD: $(git rev-parse HEAD)"
  echo "  Previous:     $(git rev-parse HEAD~1)"
  exit 1
fi

RESOLVED_SHA=$(git rev-parse "$TARGET_SHA" 2>/dev/null || echo "UNKNOWN")

echo ""
echo "VoiceOS Production Rollback"
echo "═══════════════════════════"
echo "  From:    $(git rev-parse HEAD)"
echo "  To:      $RESOLVED_SHA ($TARGET_SHA)"
$EXECUTE || echo "  Mode:    DRY RUN — pass --execute to actually rollback"
echo "  Started: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo ""

run() {
  $EXECUTE && "$@" || echo "  [dry-run] $*"
}

START_TS=$(date +%s)

# ── 1. Stop dialer_worker first (drain in-flight calls) ───────────────────────
echo "[1] Stopping dialer_worker (SIGTERM for graceful drain)..."
run systemctl stop voiceos-dialer-worker
$EXECUTE && sleep 5 || true

# ── 2. Stop voice runtime (drain gate) ───────────────────────────────────────
echo "[2] Stopping voice runtime (drain gate, waits for active calls)..."
run systemctl stop voiceos-voice-runtime
$EXECUTE && sleep 5 || true

# ── 3. Check if Alembic downgrade needed ──────────────────────────────────────
echo "[3] Checking if Alembic downgrade is needed..."
CURRENT_HEAD=$(alembic current 2>/dev/null | tail -1 || echo "unknown")
echo "    Current Alembic head: $CURRENT_HEAD"

COMMITS_AHEAD=$(git log "$RESOLVED_SHA"..HEAD --oneline | wc -l | tr -d ' ')
if [[ "$COMMITS_AHEAD" -gt 0 ]]; then
  # Check if any rolled-back commits contain migration changes
  MIGRATION_CHANGES=$(git log "$RESOLVED_SHA"..HEAD --name-only -- 'alembic/versions/*.py' | grep -c '\.py' || true)
  if [[ "$MIGRATION_CHANGES" -gt 0 ]]; then
    echo "    WARNING: $MIGRATION_CHANGES migration file(s) will be rolled back"
    echo "    Running: alembic downgrade -1"
    run alembic downgrade -1
  else
    echo "    No migration changes in rolled-back commits — skipping alembic downgrade"
  fi
fi

# ── 4. Git checkout ───────────────────────────────────────────────────────────
echo "[4] Checking out rollback target: $RESOLVED_SHA..."
run git checkout "$RESOLVED_SHA"

# ── 5. Install previous systemd units ─────────────────────────────────────────
echo "[5] Re-installing systemd units from rollback target..."
run cp scripts/systemd/voiceos-bff.service /etc/systemd/system/
run cp scripts/systemd/voiceos-webapi.service /etc/systemd/system/
run cp scripts/systemd/voiceos-voice-runtime.service /etc/systemd/system/
run cp scripts/systemd/voiceos-dialer-worker.service /etc/systemd/system/
run systemctl daemon-reload

# ── 6. Restart services in reverse order ──────────────────────────────────────
echo "[6] Restarting services..."
for unit in voiceos-webapi voiceos-bff voiceos-voice-runtime voiceos-dialer-worker; do
  run systemctl restart "$unit"
  echo "    restarted $unit"
done

# ── 7. Health checks ──────────────────────────────────────────────────────────
if $EXECUTE; then
  echo ""
  echo "[7] Health checks..."
  sleep 5
  for svc in "bff.js:http://localhost:8000/health" "web_api:http://localhost:8001/health"; do
    name="${svc%%:*}"
    url="${svc#*:}"
    if curl -sf "$url" > /dev/null 2>&1; then
      echo "    ✓ $name healthy"
    else
      echo "    ✗ $name not responding at $url — check logs"
    fi
  done
fi

END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))

echo ""
echo "═══════════════════════════════════════"
if $EXECUTE; then
  echo "  Rollback complete in ${ELAPSED}s"
  if [[ $ELAPSED -gt 900 ]]; then
    echo "  WARNING: Rollback took ${ELAPSED}s (target < 900s / 15 minutes)"
  else
    echo "  ✓ Within 15-minute target"
  fi
else
  echo "  [dry-run] Rollback would complete in approx 1–3 min"
  echo "  Run with --execute to perform the rollback"
fi
echo "  Target SHA: $RESOLVED_SHA"
echo "  Completed:  $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
