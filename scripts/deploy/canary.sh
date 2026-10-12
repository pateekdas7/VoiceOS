#!/usr/bin/env bash
# canary.sh — nginx weight-based canary deployment
# Usage:
#   ./canary.sh start  --image <img> --tag <tag> [--weight <0-100>]
#   ./canary.sh shift  --weight <0-100>
#   ./canary.sh finish
#   ./canary.sh abort
set -euo pipefail

APP_DIR=/opt/voiceos/app
NGINX_CONF=/etc/nginx/sites-available/voiceos
CANARY_STATE=/opt/voiceos/canary_state.json
LOG=/opt/voiceos/logs/canary.log

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }
die() { log "ERROR: $*"; exit 1; }

cmd_start() {
  local image="" tag="" weight=10
  while [[ $# -gt 0 ]]; do
    case $1 in
      --image) image=$2; shift 2;;
      --tag)   tag=$2;   shift 2;;
      --weight) weight=$2; shift 2;;
      *) die "Unknown arg: $1";;
    esac
  done
  [[ -n "$image" && -n "$tag" ]] || die "--image and --tag required"
  [[ $weight -ge 0 && $weight -le 100 ]] || die "--weight must be 0-100"

  if [[ -f "$CANARY_STATE" ]]; then
    die "Canary already in progress. Run 'abort' first."
  fi

  local stable_tag
  stable_tag=$(jq -r '.stable_tag // "current"' "$CANARY_STATE" 2>/dev/null || echo "current")

  log "Starting canary: image=$image tag=$tag weight=$weight%"

  # Write state
  jq -n \
    --arg image "$image" \
    --arg tag "$tag" \
    --argjson weight "$weight" \
    --arg started "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{"image":$image,"tag":$tag,"weight":$weight,"started":$started,"phase":"canary"}' \
    > "$CANARY_STATE"

  cmd_apply_weight "$weight"
  log "Canary started at ${weight}% traffic"
}

cmd_shift() {
  local weight=""
  while [[ $# -gt 0 ]]; do
    case $1 in
      --weight) weight=$2; shift 2;;
      *) die "Unknown arg: $1";;
    esac
  done
  [[ -n "$weight" ]] || die "--weight required"
  [[ -f "$CANARY_STATE" ]] || die "No canary in progress"

  jq --argjson w "$weight" '.weight = $w' "$CANARY_STATE" > "${CANARY_STATE}.tmp"
  mv "${CANARY_STATE}.tmp" "$CANARY_STATE"

  cmd_apply_weight "$weight"
  log "Canary shifted to ${weight}% traffic"
}

cmd_finish() {
  [[ -f "$CANARY_STATE" ]] || die "No canary in progress"
  local tag
  tag=$(jq -r '.tag' "$CANARY_STATE")
  log "Finishing canary: promoting tag=$tag to 100%"

  # Route all traffic to canary (now stable)
  cmd_apply_weight 100

  jq '.phase = "promoted"' "$CANARY_STATE" > "${CANARY_STATE}.tmp"
  mv "${CANARY_STATE}.tmp" "$CANARY_STATE"

  rm -f "$CANARY_STATE"
  log "Canary promoted to stable — all traffic on tag=$tag"
}

cmd_abort() {
  [[ -f "$CANARY_STATE" ]] || { log "No canary in progress — nothing to abort"; exit 0; }
  local tag
  tag=$(jq -r '.tag' "$CANARY_STATE")
  log "Aborting canary (tag=$tag) — restoring 100% to stable"

  cmd_apply_weight 0

  rm -f "$CANARY_STATE"
  log "Canary aborted — stable is now receiving 100% traffic"
}

cmd_status() {
  if [[ ! -f "$CANARY_STATE" ]]; then
    echo "No canary in progress"
    return 0
  fi
  jq . "$CANARY_STATE"
}

cmd_apply_weight() {
  local canary_weight=$1
  local stable_weight=$((100 - canary_weight))

  # Rewrite nginx upstream weights
  # Expects upstream block:
  #   upstream voiceos_bff { server 127.0.0.1:8000 weight=W; server 127.0.0.1:8002 weight=W; }
  # We use sed to replace weight values in the upstream block
  local tmp
  tmp=$(mktemp)

  python3 - "$NGINX_CONF" "$stable_weight" "$canary_weight" "$tmp" <<'PYEOF'
import sys, re

conf_path, stable_w, canary_w, out_path = sys.argv[1:]
stable_w, canary_w = int(stable_w), int(canary_w)

with open(conf_path) as f:
    conf = f.read()

# Match upstream voiceos_bff block and rewrite weights
def rewrite_upstream(m):
    block = m.group(0)
    lines = block.split('\n')
    rewritten = []
    server_idx = 0
    for line in lines:
        sm = re.match(r'(\s*server\s+\S+)\s+weight=\d+(.*)', line)
        if sm:
            w = stable_w if server_idx == 0 else canary_w
            line = f"{sm.group(1)} weight={w}{sm.group(2)}"
            server_idx += 1
        rewritten.append(line)
    return '\n'.join(rewritten)

conf = re.sub(
    r'upstream voiceos_bff \{[^}]+\}',
    rewrite_upstream,
    conf,
    flags=re.DOTALL
)

with open(out_path, 'w') as f:
    f.write(conf)
PYEOF

  echo 'mamata@1976' | sudo -S cp "$tmp" "$NGINX_CONF"
  rm -f "$tmp"

  if echo 'mamata@1976' | sudo -S nginx -t 2>/dev/null; then
    echo 'mamata@1976' | sudo -S nginx -s reload
    log "nginx reloaded: stable=${stable_weight}% canary=${canary_weight}%"
  else
    log "ERROR: nginx config invalid — rolling back"
    echo 'mamata@1976' | sudo -S cp "${NGINX_CONF}.bak" "$NGINX_CONF" 2>/dev/null || true
    echo 'mamata@1976' | sudo -S nginx -s reload
    die "nginx config rejected — stable config restored"
  fi
}

# --- entrypoint ---
[[ $# -ge 1 ]] || { echo "Usage: $0 <start|shift|finish|abort|status> [options]"; exit 1; }
COMMAND=$1; shift

case $COMMAND in
  start)  cmd_start  "$@";;
  shift)  cmd_shift  "$@";;
  finish) cmd_finish;;
  abort)  cmd_abort;;
  status) cmd_status;;
  *)      die "Unknown command: $COMMAND";;
esac
