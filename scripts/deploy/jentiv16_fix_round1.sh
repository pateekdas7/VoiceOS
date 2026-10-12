#!/usr/bin/env bash
# jentiv16_fix_round1.sh — fix all failures from first full deploy on JentiV16
set -euo pipefail
SUDO='echo 123456 | sudo -S'
APP=/opt/voiceos/app
VENV=/opt/voiceos/.venv
STACK=/opt/voiceos/monitoring-stack
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

log "===== JentiV16 fix round 1 ====="

log "A) chmod a+rX on repo monitoring config dirs (docker containers can't read 0600 files)"
eval "$SUDO chmod -R a+rX /opt/voiceos/app/monitoring" | tail -3

log "B) ensure stack dirs exist with grafana uid 472"
eval "$SUDO mkdir -p $STACK/grafana-provisioning/plugins $STACK/grafana-provisioning/alerting $STACK/grafana-provisioning/dashboards $STACK/grafana-provisioning/datasources" | tail -3
eval "$SUDO chown -R 472:472 $STACK/grafana-provisioning" | tail -3
eval "$SUDO chmod -R a+rX $STACK/grafana-provisioning" | tail -3

log "C) rewrite docker-compose.yml — grafana on 3001 to avoid collision with bff:3000"
cat > /tmp/voiceos-compose.yml <<'YAML'
version: "3.8"
services:
  prometheus:
    image: prom/prometheus:v2.54.1
    container_name: voiceos-prometheus
    network_mode: host
    volumes:
      - /opt/voiceos/app/monitoring/prometheus/prometheus-single-host.yml:/etc/prometheus/prometheus.yml:ro
      - /opt/voiceos/app/monitoring/prometheus/alert_rules:/etc/prometheus/alert_rules:ro
      - prom-data:/prometheus
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --web.enable-lifecycle
      - --web.listen-address=:9090
    restart: unless-stopped

  alertmanager:
    image: prom/alertmanager:v0.27.0
    container_name: voiceos-alertmanager
    network_mode: host
    volumes:
      - /opt/voiceos/app/monitoring/grafana/alerts/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
      - am-data:/alertmanager
    command:
      - --config.file=/etc/alertmanager/alertmanager.yml
      - --storage.path=/alertmanager
      - --web.listen-address=:9093
    restart: unless-stopped

  grafana:
    image: grafana/grafana:11.1.3
    container_name: voiceos-grafana
    network_mode: host
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_SERVER_HTTP_PORT=3001
      - GF_PATHS_PROVISIONING=/etc/grafana/provisioning
    volumes:
      - graf-data:/var/lib/grafana
      - /opt/voiceos/monitoring-stack/grafana-provisioning:/etc/grafana/provisioning:ro
    restart: unless-stopped

  loki:
    image: grafana/loki:3.1.1
    container_name: voiceos-loki
    network_mode: host
    command: -config.file=/etc/loki/local-config.yaml -server.http-listen-port=3100
    volumes:
      - loki-data:/loki
    restart: unless-stopped

  promtail:
    image: grafana/promtail:3.1.1
    container_name: voiceos-promtail
    network_mode: host
    volumes:
      - /opt/voiceos/app/monitoring/logging/promtail.yml:/etc/promtail/config.yml:ro
      - /var/log:/var/log:ro
      - /run/log/journal:/run/log/journal:ro
    command: -config.file=/etc/promtail/config.yml
    restart: unless-stopped

  jaeger:
    image: jaegertracing/all-in-one:1.60
    container_name: voiceos-jaeger
    network_mode: host
    environment:
      - COLLECTOR_OTLP_ENABLED=true
      - QUERY_BASE_PATH=/
    restart: unless-stopped

volumes:
  prom-data:
  am-data:
  graf-data:
  loki-data:
YAML
cp /tmp/voiceos-compose.yml $STACK/docker-compose.yml

log "D) root npm install for bff.js + dialer_worker.js"
cd "$APP"
if [ -f package.json ]; then
  npm install --no-audit --no-fund 2>&1 | tail -5 || true
fi

log "E) rewrite webapi unit with --factory create_app"
cat > /tmp/voiceos-webapi.service <<SVC
[Unit]
Description=VoiceOS webapi
After=network.target postgresql.service

[Service]
Type=simple
User=jenti
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$VENV/bin/uvicorn --factory src.services.web_api.main:create_app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
eval "$SUDO cp /tmp/voiceos-webapi.service /etc/systemd/system/" | tail -3

log "F) create bff systemd unit (bff.js at repo root)"
cat > /tmp/voiceos-bff.service <<SVC
[Unit]
Description=VoiceOS BFF
After=network.target voiceos-webapi.service

[Service]
Type=simple
User=jenti
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=/usr/bin/node $APP/bff.js
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
eval "$SUDO cp /tmp/voiceos-bff.service /etc/systemd/system/" | tail -3

log "G) create dialer-worker systemd unit"
cat > /tmp/voiceos-dialer-worker.service <<SVC
[Unit]
Description=VoiceOS dialer-worker
After=network.target

[Service]
Type=simple
User=jenti
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=/usr/bin/node $APP/dialer_worker.js
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
eval "$SUDO cp /tmp/voiceos-dialer-worker.service /etc/systemd/system/" | tail -3

log "H) restart docker stack with new compose"
cd "$STACK"
eval "$SUDO docker compose down" 2>&1 | tail -10 || true
eval "$SUDO docker compose up -d" 2>&1 | tail -15 || true

log "I) daemon-reload + enable + restart app services"
eval "$SUDO systemctl daemon-reload" | tail -3
for svc in voiceos-webapi voiceos-bff voiceos-dialer-worker; do
  eval "$SUDO systemctl enable --now ${svc}.service" 2>&1 | tail -3
  eval "$SUDO systemctl restart ${svc}.service" 2>&1 | tail -3
done

log "J) wait 45s for everything to settle"
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
  "bff http://localhost:3000/healthz" \
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

log "===== DONE ====="
