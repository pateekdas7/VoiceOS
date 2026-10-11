#!/usr/bin/env bash
# Full VoiceOS deployment on jenti@JentiV16 (WSL2, 4 cores, 3.8 GB RAM).
# Idempotent: safe to re-run. Deploys everything EXCEPT GPU model-serving.
#
# Phases:
#   A. apt install node, docker, postgres-contrib, mongo tooling
#   B. git clone /opt/voiceos/app at latest w17-support
#   C. python venv + pip install from requirements.txt
#   D. npm install in bff, frontend, dialer-worker
#   E. postgres db setup + alembic migrations
#   F. monitoring stack via docker-compose (prometheus/grafana/loki/alertmanager/promtail/jaeger)
#   G. systemd units for scheduler + voiceos app services
#   H. start all app services
#   I. health-check all endpoints + scrape metrics
#   J. run full test suite + capture report

set -u
SUDO="echo 123456 | sudo -S"
STATUS=/tmp/voiceos_deploy.status
LOG=/tmp/voiceos_deploy.log
APP=/opt/voiceos/app
VENV=/opt/voiceos/.venv
PGUSER=voiceos
PGDB=voiceos
PGPASS=voiceos_dev_pw
export DEBIAN_FRONTEND=noninteractive

say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG" >> "$STATUS"; }
phase() { echo; echo "============================================================"; echo "PHASE $*"; echo "============================================================"; say "PHASE $*"; }
run() { echo "+ $*"; "$@" 2>&1 | tee -a "$LOG"; }

: > "$STATUS"
: > "$LOG"
say "START $(date)"

phase "A — apt install prerequisites"
eval "$SUDO apt-get update" | tail -3
eval "$SUDO apt-get install -y curl ca-certificates gnupg lsb-release python3-venv python3-dev build-essential libpq-dev postgresql-contrib" | tail -5

# Node 20 LTS
if ! node --version 2>/dev/null | grep -q "^v2"; then
  say "installing Node 20 LTS"
  curl -fsSL https://deb.nodesource.com/setup_20.x | eval "$SUDO bash -" 2>&1 | tail -5
  eval "$SUDO apt-get install -y nodejs" | tail -3
fi
say "node=$(node --version) npm=$(npm --version)"

# Docker (for monitoring stack)
if ! command -v docker >/dev/null; then
  say "installing docker.io"
  eval "$SUDO apt-get install -y docker.io docker-compose-v2" | tail -5
  eval "$SUDO systemctl enable --now docker" | tail -3
  eval "$SUDO usermod -aG docker jenti" | tail -3
fi
say "docker=$(docker --version 2>&1 | head -1)"

phase "B — git clone/pull /opt/voiceos/app"
if [ ! -d "$APP/.git" ]; then
  eval "$SUDO rm -rf $APP"
  eval "$SUDO mkdir -p /opt/voiceos && $SUDO chown jenti:jenti /opt/voiceos"
  git clone --branch w17-support https://github.com/pateekdas7/VoiceOS.git "$APP" 2>&1 | tail -5
else
  cd "$APP" && git fetch --all 2>&1 | tail -3 && git checkout w17-support && git reset --hard origin/w17-support 2>&1 | tail -3
fi
cd "$APP" && git log -1 --oneline | tee -a "$LOG"

phase "C — python venv + pip install"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip 2>&1 | tail -3
if [ -f "$APP/requirements.txt" ]; then
  say "pip install -r requirements.txt"
  "$VENV/bin/pip" install -q -r "$APP/requirements.txt" 2>&1 | tail -8
elif [ -f "$APP/pyproject.toml" ]; then
  say "pip install -e . (pyproject)"
  "$VENV/bin/pip" install -q -e "$APP" 2>&1 | tail -8
else
  say "no requirements.txt — installing core deps directly"
  "$VENV/bin/pip" install -q starlette uvicorn pydantic psycopg[binary] httpx pytest pytest-cov pyyaml prometheus_client cryptography opentelemetry-api opentelemetry-sdk alembic sqlalchemy redis fastapi python-jose[cryptography] 2>&1 | tail -5
fi
say "python deps installed: $("$VENV/bin/pip" list 2>/dev/null | wc -l) packages"

phase "D — npm install in bff + frontend + dialer-worker"
for d in bff frontend dialer-worker; do
  if [ -d "$APP/$d" ] && [ -f "$APP/$d/package.json" ]; then
    cd "$APP/$d"
    say "npm install in $d"
    npm install --no-audit --no-fund --loglevel=error 2>&1 | tail -5
  fi
done

phase "E — postgres database + alembic migrations"
# Create user + db idempotently
eval "$SUDO -u postgres psql -c \"DO \\\$\\\$ BEGIN IF NOT EXISTS (SELECT FROM pg_user WHERE usename='$PGUSER') THEN CREATE ROLE $PGUSER LOGIN PASSWORD '$PGPASS'; END IF; END \\\$\\\$;\"" 2>&1 | tail -3
eval "$SUDO -u postgres psql -tc \"SELECT 1 FROM pg_database WHERE datname='$PGDB'\" | grep -q 1 || $SUDO -u postgres createdb -O $PGUSER $PGDB" 2>&1 | tail -3
eval "$SUDO -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE $PGDB TO $PGUSER;\"" 2>&1 | tail -3

export POSTGRES_DSN="postgresql://$PGUSER:$PGPASS@localhost:5432/$PGDB"
export DATABASE_URL="$POSTGRES_DSN"
echo "POSTGRES_DSN=$POSTGRES_DSN" > /tmp/voiceos_env

cd "$APP"
if [ -f "$APP/alembic.ini" ]; then
  say "running alembic upgrade head"
  "$VENV/bin/alembic" -c "$APP/alembic.ini" upgrade head 2>&1 | tail -15 || say "alembic failed — some tables may be missing (will inspect manually)"
fi

phase "F — monitoring stack via docker-compose"
mkdir -p /opt/voiceos/monitoring-stack
cat > /opt/voiceos/monitoring-stack/docker-compose.yml <<'YAML'
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
      - GF_SERVER_HTTP_PORT=3000
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
    volumes:
      - loki-data:/loki
    command: -config.file=/etc/loki/local-config.yaml -server.http-listen-port=3100
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

# Grafana provisioning (Prometheus + Loki datasources + dashboards)
mkdir -p /opt/voiceos/monitoring-stack/grafana-provisioning/datasources
mkdir -p /opt/voiceos/monitoring-stack/grafana-provisioning/dashboards
cat > /opt/voiceos/monitoring-stack/grafana-provisioning/datasources/datasources.yml <<'YAML'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
  - name: Loki
    type: loki
    access: proxy
    url: http://localhost:3100
YAML
cat > /opt/voiceos/monitoring-stack/grafana-provisioning/dashboards/dashboards.yml <<'YAML'
apiVersion: 1
providers:
  - name: voiceos
    folder: VoiceOS
    type: file
    options:
      path: /var/lib/grafana/dashboards
YAML

say "docker compose up -d (monitoring)"
cd /opt/voiceos/monitoring-stack
eval "$SUDO docker compose up -d" 2>&1 | tail -15

phase "G — voiceos systemd units"
# scheduler unit (standalone G5)
cat > /tmp/voiceos-scheduler.service <<SVC
[Unit]
Description=VoiceOS monitoring scheduler (Phase 5/G5)
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=jenti
WorkingDirectory=$APP
Environment=POSTGRES_DSN=$POSTGRES_DSN
Environment=SCHEDULER_METRICS_PORT=9108
Environment=ALERT_ESCALATION_INTERVAL_S=60
Environment=GPU_POLL_INTERVAL_S=30
ExecStart=$VENV/bin/python -m scripts.workers.scheduler
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
eval "$SUDO cp /tmp/voiceos-scheduler.service /etc/systemd/system/" 2>&1 | tail -3
eval "$SUDO systemctl daemon-reload" 2>&1 | tail -3
eval "$SUDO systemctl enable --now voiceos-scheduler.service" 2>&1 | tail -3
sleep 2
eval "$SUDO systemctl status voiceos-scheduler.service --no-pager | head -8"

phase "H — start app services (webapi, bff, dialer-worker)"
# Build .env
cat > "$APP/.env" <<ENV
POSTGRES_DSN=$POSTGRES_DSN
DATABASE_URL=$POSTGRES_DSN
REDIS_URL=redis://localhost:6379/0
PROMETHEUS_URL=http://localhost:9090
LOKI_URL=http://localhost:3100
JAEGER_URL=http://localhost:16686
NODE_ENV=production
JWT_SECRET=jenti_v16_dev_secret_please_rotate
ENV

# webapi (uvicorn)
cat > /tmp/voiceos-webapi.service <<SVC
[Unit]
Description=VoiceOS webapi
After=network.target postgresql.service

[Service]
Type=simple
User=jenti
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$VENV/bin/uvicorn src.services.web_api.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
eval "$SUDO cp /tmp/voiceos-webapi.service /etc/systemd/system/" 2>&1 | tail -3

# bff (node)
if [ -f "$APP/bff/package.json" ]; then
cat > /tmp/voiceos-bff.service <<SVC
[Unit]
Description=VoiceOS BFF
After=network.target voiceos-webapi.service

[Service]
Type=simple
User=jenti
WorkingDirectory=$APP/bff
EnvironmentFile=$APP/.env
ExecStart=/usr/bin/node $APP/bff/bff.js
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
eval "$SUDO cp /tmp/voiceos-bff.service /etc/systemd/system/" 2>&1 | tail -3
fi

# dialer-worker
if [ -f "$APP/dialer-worker/package.json" ]; then
cat > /tmp/voiceos-dialer-worker.service <<SVC
[Unit]
Description=VoiceOS dialer-worker
After=network.target redis-server.service

[Service]
Type=simple
User=jenti
WorkingDirectory=$APP/dialer-worker
EnvironmentFile=$APP/.env
ExecStart=/usr/bin/node $APP/dialer-worker/dialer_worker.js
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
eval "$SUDO cp /tmp/voiceos-dialer-worker.service /etc/systemd/system/" 2>&1 | tail -3
fi

eval "$SUDO systemctl daemon-reload" 2>&1 | tail -3
for svc in voiceos-webapi voiceos-bff voiceos-dialer-worker; do
  eval "$SUDO systemctl enable --now ${svc}.service" 2>&1 | tail -3
  sleep 2
done
sleep 8

phase "I — health checks"
for pair in "scheduler:9108" "webapi:8000/healthz" "bff:3000" "prometheus:9090/-/healthy" "grafana:3000/api/health" "alertmanager:9093/-/healthy" "loki:3100/ready" "jaeger:16686/" "promtail:9080/ready"; do
  name=${pair%%:*}; path=${pair#*:}
  url="http://localhost:$path"
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url")
  say "HEALTH $name $url → HTTP $code"
done

phase "J — test suite"
cd "$APP"
"$VENV/bin/pytest" --no-cov --no-header -q \
  tests/unit/monitoring/test_phase1_scheduler_metrics.py \
  tests/unit/web_api/test_phase2_route_order.py \
  tests/unit/monitoring/test_phase3_registry_and_cardinality.py \
  2>&1 | tail -20

say "=== CI gates ==="
"$VENV/bin/python" scripts/ci/alertmanager_config_drift.py 2>&1 | tail -5
"$VENV/bin/python" scripts/ci/check_runbook_coverage.py 2>&1 | tail -5
"$VENV/bin/python" scripts/grafana/generate_dashboards.py --check 2>&1 | tail -5

say "=== metric scrape sample ==="
curl -s http://localhost:9108/metrics 2>&1 | grep -E "^voiceos_" | sort -u | head -20 | tee -a "$LOG"

say "END $(date)"
echo "FULL_LOG=$LOG"
echo "STATUS=$STATUS"
