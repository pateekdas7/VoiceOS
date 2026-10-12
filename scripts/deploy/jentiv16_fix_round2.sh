#!/usr/bin/env bash
# jentiv16_fix_round2.sh
#   - webapi: supply GOOGLE_CLIENT_ID/SECRET, FRONTEND_BASE_URL, BFF_PUBLIC_URL via .env
#   - bff: force PORT=3000 in .env so webapi:8000 and bff:3000 don't collide
#   - alertmanager: replace yml with env-free null-receiver config (dev deploy)
#   - loki: wait longer and recheck
set -euo pipefail
SUDO='echo 123456 | sudo -S'
APP=/opt/voiceos/app
STACK=/opt/voiceos/monitoring-stack
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

log "===== JentiV16 fix round 2 ====="

log "A) append missing env vars to $APP/.env (webapi + bff port)"
# Preserve existing contents; append only keys that aren't there yet.
touch "$APP/.env"
add_env() {
  local key="$1" val="$2"
  if ! grep -q "^${key}=" "$APP/.env"; then
    echo "${key}=${val}" >> "$APP/.env"
    log "  + ${key}"
  fi
}
add_env PORT 3000
add_env GOOGLE_CLIENT_ID "jentiv16-dev-google-client-id.apps.googleusercontent.com"
add_env GOOGLE_CLIENT_SECRET "jentiv16-dev-google-client-secret"
add_env FRONTEND_BASE_URL "http://localhost:3000"
add_env BFF_PUBLIC_URL "http://localhost:8000"
# ensure POSTGRES_DSN exists (deploy script set it; add safety fallback)
add_env POSTGRES_DSN "postgres://voiceos:voiceos_dev_pw@127.0.0.1:5432/voiceos"
add_env FRONTEND_ALLOWED_ORIGINS "http://localhost:3000,http://localhost:3001"

log "B) write a dev-friendly alertmanager.yml with no env-var substitution"
cat > /tmp/alertmanager.yml <<'YAML'
# JentiV16 dev deploy — no external paging credentials required.
# Every alert is routed to the local voiceos-webhook on the webapi
# which persists it to the alert_lifecycle table.
global:
  resolve_timeout: 5m

route:
  receiver: voiceos-webhook
  group_by: ["alertname", "service"]
  group_wait: 10s
  group_interval: 2m
  repeat_interval: 1h

inhibit_rules:
  - source_matchers: ["severity=critical"]
    target_matchers: ["severity=warning"]
    equal: ["alertname", "service"]

receivers:
  - name: voiceos-webhook
    webhook_configs:
      - url: "http://127.0.0.1:8000/webhooks/alertmanager"
        send_resolved: true
        max_alerts: 0
YAML
eval "$SUDO cp /tmp/alertmanager.yml $APP/monitoring/grafana/alerts/alertmanager.yml" | tail -3
eval "$SUDO chmod a+r $APP/monitoring/grafana/alerts/alertmanager.yml" | tail -3

log "C) restart affected docker services"
cd "$STACK"
eval "$SUDO docker compose restart alertmanager" 2>&1 | tail -3

log "D) restart app services to pick up new .env"
eval "$SUDO systemctl daemon-reload" | tail -3
for svc in voiceos-webapi voiceos-bff voiceos-dialer-worker voiceos-scheduler; do
  eval "$SUDO systemctl restart ${svc}.service" 2>&1 | tail -3
done

log "E) wait 45s for everything to settle"
sleep 45

log "===== systemd status ====="
for svc in voiceos-scheduler voiceos-webapi voiceos-bff voiceos-dialer-worker; do
  active=$(systemctl is-active "$svc" 2>&1 || true)
  log "systemd $svc = $active"
done
log "===== docker status ====="
docker ps --format 'table {{.Names}}\t{{.Status}}' 2>&1 | head -20

log "===== health checks ====="
for endpoint in \
  "scheduler http://localhost:9108/" \
  "webapi http://localhost:8000/healthz" \
  "webapi-root http://localhost:8000/" \
  "bff http://localhost:3000/healthz" \
  "bff-root http://localhost:3000/" \
  "prometheus http://localhost:9090/-/healthy" \
  "grafana http://localhost:3001/api/health" \
  "alertmanager http://localhost:9093/-/healthy" \
  "loki http://localhost:3100/ready" \
  "jaeger http://localhost:16686/" \
  "promtail http://localhost:9080/ready"; do
  name=${endpoint%% *}
  url=${endpoint#* }
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" || echo "ERR")
  log "HEALTH $name $url → HTTP $code"
done

log "===== webapi crash tail (if any) ====="
echo 123456 | sudo -S journalctl -u voiceos-webapi -n 20 --no-pager 2>&1 | tail -20 || true

log "===== DONE ====="
