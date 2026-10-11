#!/usr/bin/env bash
# jentiv16_fix_round3.sh — close remaining testify failures
set -uo pipefail
SUDO='echo 123456 | sudo -S'
APP=/opt/voiceos/app
STACK=/opt/voiceos/monitoring-stack
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

log "===== JentiV16 fix round 3 ====="

log "A) install + start redis-server (unblocks bff /system/health)"
eval "$SUDO apt-get install -y redis-server" 2>&1 | tail -5 || true
eval "$SUDO systemctl enable --now redis-server" 2>&1 | tail -3 || true

log "B) /home/jenti/VoiceOS is the symlink target for /opt/voiceos/app — leave it alone"
# pytest discovers rootdir from the pyproject.toml it finds; the correct
# rootdir is reported as /home/jenti/VoiceOS because that is the canonical
# path. All future test runs will cd there explicitly.

log "C) rewrite prometheus-single-host.yml — add scheduler scrape + 6th rule file"
cat > /tmp/prometheus-single-host.yml <<'YAML'
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    cluster: voiceos-cpu-node
    environment: dev
    sd_profile: single-host

rule_files:
  - "recording_rules.yml"
  - "alert_rules/slo_alerts.yml"
  - "alert_rules/infrastructure.yml"
  - "alert_rules/application.yml"
  - "alert_rules/business.yml"
  - "alert_rules/ai_services.yml"
  - "alert_rules/voiceos_bff_dialer.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - "localhost:9093"

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  - job_name: voiceos-scheduler
    static_configs:
      - targets: ["localhost:9108"]
        labels:
          service: scheduler
          node: jentiv16

  - job_name: voiceos-webapi
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:8000"]
        labels:
          service: webapi
          node: jentiv16

  - job_name: voiceos-bff
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:3000"]
        labels:
          service: bff
          node: jentiv16

  - job_name: alertmanager
    static_configs:
      - targets: ["localhost:9093"]

  - job_name: loki
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:3100"]

  - job_name: promtail
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:9080"]

  - job_name: jaeger
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:14269"]
YAML
eval "$SUDO cp /tmp/prometheus-single-host.yml $APP/monitoring/prometheus/prometheus-single-host.yml" | tail -3
eval "$SUDO chmod a+r $APP/monitoring/prometheus/prometheus-single-host.yml" | tail -3

log "D) grafana datasource + dashboard provisioning"
eval "$SUDO mkdir -p $STACK/grafana-provisioning/datasources $STACK/grafana-provisioning/dashboards" | tail -3
eval "$SUDO chown -R $(id -u):$(id -g) $STACK/grafana-provisioning" | tail -3
cat > $STACK/grafana-provisioning/datasources/datasources.yml <<'YAML'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: true
  - name: Loki
    type: loki
    access: proxy
    url: http://localhost:3100
    editable: true
YAML
cat > $STACK/grafana-provisioning/dashboards/dashboards.yml <<'YAML'
apiVersion: 1
providers:
  - name: voiceos
    folder: VoiceOS
    type: file
    disableDeletion: false
    editable: true
    allowUiUpdates: true
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
YAML
# Permissions on provisioning dir
eval "$SUDO chown -R 472:472 $STACK/grafana-provisioning" | tail -3
eval "$SUDO chmod -R a+rX $STACK/grafana-provisioning" | tail -3

log "E) rewrite compose to mount dashboards dir read-only"
cat > /tmp/voiceos-compose.yml <<'YAML'
services:
  prometheus:
    image: prom/prometheus:v2.54.1
    container_name: voiceos-prometheus
    network_mode: host
    volumes:
      - /opt/voiceos/app/monitoring/prometheus/prometheus-single-host.yml:/etc/prometheus/prometheus.yml:ro
      - /opt/voiceos/app/monitoring/prometheus:/etc/prometheus/base:ro
      - /opt/voiceos/app/monitoring/prometheus/alert_rules:/etc/prometheus/alert_rules:ro
      - /opt/voiceos/app/monitoring/prometheus/recording_rules.yml:/etc/prometheus/recording_rules.yml:ro
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
      - /opt/voiceos/app/monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro
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

log "F) chmod a+rX on monitoring tree (recursive)"
eval "$SUDO chmod -R a+rX $APP/monitoring" | tail -3

log "G) docker compose up -d (restart affected services)"
cd $STACK
eval "$SUDO docker compose up -d --force-recreate prometheus grafana" 2>&1 | tail -10

log "H) restart bff so it picks up Redis now that it's running"
eval "$SUDO systemctl restart voiceos-bff.service" | tail -3

log "I) wait 30s for grafana to provision + prometheus to scrape"
sleep 30

log "===== docker status ====="
docker ps --format 'table {{.Names}}\t{{.Status}}' 2>&1 | head -15
log "===== prometheus targets ====="
curl -s 'http://localhost:9090/api/v1/targets?state=active' | python3 -c "
import json,sys
d=json.load(sys.stdin)
for t in d.get('data',{}).get('activeTargets',[]):
    print(t.get('scrapePool',''), '->', t.get('scrapeUrl',''), '=', t.get('health',''))
" 2>&1 | head -20
log "===== grafana dashboards ====="
curl -s -u admin:admin 'http://localhost:3001/api/search?type=dash-db' | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('dashboards:', len(d))
for x in d: print(' -', x.get('title'))
" 2>&1 | head -20
log "===== bff health ====="
curl -s -o /dev/null -w 'bff/system/health: HTTP %{http_code} time=%{time_total}s\n' --max-time 8 http://localhost:3000/system/health 2>&1

log "===== DONE round 3 ====="
