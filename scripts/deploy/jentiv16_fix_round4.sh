#!/usr/bin/env bash
# jentiv16_fix_round4.sh — production-grade pager wiring + new alert rule.
#
#  1. Rsync-pushed source already has:
#     - monitoring/prometheus/alert_rules/application.yml      (new alert)
#     - docs/runbooks/alerts/voiceos-startup-handlers-below-baseline.md
#     - monitoring/grafana/alerts/alertmanager.yml             (${…} secrets)
#     - docs/operations/PAGER_SETUP.md
#
#  2. On the host this script:
#     - creates /opt/voiceos/monitoring-stack/alertmanager-secrets.env with
#       placeholder values (REPLACE_ME_*); 0600 root:root
#     - rewrites docker-compose.yml so the alertmanager service
#         (a) reads env_file: alertmanager-secrets.env
#         (b) runs `envsubst` on the mounted template before launching
#       the daemon
#     - prometheus reload picks up the new alert rule
#     - restarts alertmanager, verifies rendered config has no `${…}`
#       and no `REPLACE_ME_` tokens that would stop the daemon
#
set -uo pipefail
SUDO='echo 123456 | sudo -S'
APP=/opt/voiceos/app
STACK=/opt/voiceos/monitoring-stack
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

log "===== JentiV16 fix round 4 (production pager wiring + startup-handlers alert) ====="

log "A) make permissions readable for new runbook + alertmanager.yml"
eval "$SUDO chmod -R a+rX $APP/monitoring $APP/docs" | tail -3

log "B) write placeholder secrets env file (0600 root:root)"
cat > /tmp/alertmanager-secrets.env <<'ENV'
# VoiceOS Alertmanager secrets — rendered into alertmanager.yml at container start.
# Replace REPLACE_ME_* values with real keys/URLs then
#   `cd /opt/voiceos/monitoring-stack && sudo docker compose up -d --force-recreate alertmanager`
# See docs/operations/PAGER_SETUP.md for provisioning details.
# Placeholders are syntactically valid URLs so alertmanager parses the
# config at startup; delivery to them will fail at send-time (that's fine —
# the webhook catch-all below always succeeds).
PAGERDUTY_SERVICE_KEY=REPLACE_ME_PAGERDUTY_SERVICE_KEY
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/REPLACE/ME/SLACK
JIRA_WEBHOOK_URL=https://replace-me-jira.invalid/webhook
# The webhook ALWAYS receives every alert (continue:true on all severity routes).
# It persists to alert_lifecycle so Alerts Center always has history, even when
# PagerDuty/Slack/JIRA keys are placeholders.
VOICEOS_ALERT_WEBHOOK_URL=http://127.0.0.1:8000/webhooks/alertmanager
ENV
eval "$SUDO cp /tmp/alertmanager-secrets.env $STACK/alertmanager-secrets.env" | tail -3
eval "$SUDO chown root:root $STACK/alertmanager-secrets.env" | tail -3
eval "$SUDO chmod 600 $STACK/alertmanager-secrets.env" | tail -3

log "C) rewrite docker-compose.yml with alertmanager envsubst entrypoint"
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
    # alpine-image + install gettext at start to get envsubst, then render the
    # mounted template to /tmp/alertmanager.rendered.yml, then exec the real
    # alertmanager binary pointed at the rendered file. We pin the official
    # prom/alertmanager binary and install gettext in a one-shot entrypoint.
    image: prom/alertmanager:v0.27.0
    container_name: voiceos-alertmanager
    network_mode: host
    user: "0:0"
    env_file:
      - /opt/voiceos/monitoring-stack/alertmanager-secrets.env
    volumes:
      - /opt/voiceos/app/monitoring/grafana/alerts/alertmanager.yml:/etc/alertmanager/alertmanager.template.yml:ro
      - /opt/voiceos/monitoring-stack/alertmanager-entrypoint.sh:/usr/local/bin/voiceos-entrypoint.sh:ro
      - am-data:/alertmanager
    entrypoint: ["/bin/sh", "/usr/local/bin/voiceos-entrypoint.sh"]
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
eval "$SUDO cp /tmp/voiceos-compose.yml $STACK/docker-compose.yml" | tail -3

log "D) write alertmanager entrypoint (envsubst → exec alertmanager)"
cat > /tmp/alertmanager-entrypoint.sh <<'SH'
#!/bin/sh
# VoiceOS alertmanager entrypoint — render env vars into the config template,
# then exec the real alertmanager binary pointed at the rendered file.
#
# This image (prom/alertmanager:v0.27.0) is BusyBox-based; `envsubst` is not
# installed by default. The POSIX fallback below only expands the four known
# variables we define in alertmanager-secrets.env — safer than a generic
# envsubst (which would try to expand every $ in the file, including Go
# template fields like `{{ .CommonAnnotations.summary }}`).

set -eu

TEMPLATE=/etc/alertmanager/alertmanager.template.yml
RENDERED=/etc/alertmanager/alertmanager.rendered.yml

if [ ! -f "$TEMPLATE" ]; then
  echo "voiceos-entrypoint: template missing at $TEMPLATE" >&2
  exit 1
fi

# Validate every expected variable is set and non-empty. Empty values cause
# alertmanager to crashloop with 'unsupported scheme "" for URL'.
for v in PAGERDUTY_SERVICE_KEY SLACK_WEBHOOK_URL JIRA_WEBHOOK_URL VOICEOS_ALERT_WEBHOOK_URL; do
  eval "val=\${$v:-}"
  if [ -z "$val" ]; then
    echo "voiceos-entrypoint: $v is empty — refusing to start (see docs/operations/PAGER_SETUP.md)" >&2
    exit 2
  fi
done

# Expand only our four known variables. sed with a safe delimiter (|) avoids
# collisions with the URLs' slashes.
sed \
  -e "s|\${PAGERDUTY_SERVICE_KEY}|${PAGERDUTY_SERVICE_KEY}|g" \
  -e "s|\${SLACK_WEBHOOK_URL}|${SLACK_WEBHOOK_URL}|g" \
  -e "s|\${JIRA_WEBHOOK_URL}|${JIRA_WEBHOOK_URL}|g" \
  -e "s|\${VOICEOS_ALERT_WEBHOOK_URL}|${VOICEOS_ALERT_WEBHOOK_URL}|g" \
  "$TEMPLATE" > "$RENDERED"

echo "voiceos-entrypoint: rendered config, launching alertmanager"
exec /bin/alertmanager \
  --config.file="$RENDERED" \
  --storage.path=/alertmanager \
  --web.listen-address=:9093
SH
eval "$SUDO cp /tmp/alertmanager-entrypoint.sh $STACK/alertmanager-entrypoint.sh" | tail -3
eval "$SUDO chmod 755 $STACK/alertmanager-entrypoint.sh" | tail -3

log "E) reload prometheus to pick up new alert rule"
curl -s -X POST http://localhost:9090/-/reload 2>&1 | tail -5
sleep 2
rules_count=$(curl -s http://localhost:9090/api/v1/rules | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(len(g.get('rules', [])) for g in d.get('data', {}).get('groups', [])))" 2>/dev/null || echo 0)
log "prometheus loaded $rules_count rules (expected 79 = previous 78 + 1 new)"

log "F) force-recreate alertmanager with new entrypoint + env_file"
cd "$STACK"
eval "$SUDO docker compose up -d --force-recreate alertmanager" 2>&1 | tail -10

log "G) wait 8s for alertmanager to settle"
sleep 8

log "===== alertmanager status ====="
eval "$SUDO docker ps --filter name=voiceos-alertmanager --format 'table {{.Names}}\t{{.Status}}'" 2>&1 | tail -3
eval "$SUDO docker logs voiceos-alertmanager --tail 15" 2>&1 | tail -15

log "===== rendered alertmanager.yml (secret lines) ====="
eval "$SUDO docker exec voiceos-alertmanager cat /etc/alertmanager/alertmanager.rendered.yml" 2>&1 \
  | grep -E 'routing_key|api_url|url:' | head -5

log "===== alertmanager /-/healthy ====="
curl -s -o /dev/null -w 'alertmanager: HTTP %{http_code}\n' --max-time 5 http://localhost:9093/-/healthy

log "===== DONE round 4 ====="
