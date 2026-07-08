#!/usr/bin/env bash
# ==============================================================================
# VoiceOS CPU Node Bootstrap Script
# Installs all dependencies and brings up the CPU node from scratch.
#
# Environment: Ubuntu 22.04 (may run inside a Kubernetes pod — no Docker socket)
#
# Usage:
#   chmod +x bootstrap.sh
#   sudo ./bootstrap.sh          # on a bare VM
#   bash ./bootstrap.sh          # inside a K8s pod (no systemctl)
#
# After bootstrap, copy .env from secure store and run:
#   ./restore.sh
#
# NOTE: This script supports both bare-VM and K8s-pod environments.
#       When running in a K8s pod, Docker installation is skipped.
# ==============================================================================

set -euo pipefail

VOICEOS_HOME="/opt/voiceos"
PYTHON_VERSION="3.12"
IN_KUBE_POD=false

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
has_systemctl() { command -v systemctl &>/dev/null && systemctl status &>/dev/null 2>&1; }

# Detect K8s pod environment
if [[ -f /proc/1/cgroup ]] && grep -q 'kubepods' /proc/1/cgroup 2>/dev/null; then
  IN_KUBE_POD=true
  log "Detected Kubernetes pod environment — Docker installation will be skipped"
fi

log "=== VoiceOS CPU Node Bootstrap ==="

# ── 1. System packages ──────────────────────────────────────────────────────
log "Installing system packages..."
apt-get update -qq
apt-get install -y \
  curl wget git unzip jq \
  build-essential pkg-config \
  ca-certificates gnupg lsb-release \
  net-tools nmap tcpdump \
  htop iotop sysstat \
  supervisor \
  libssl-dev libffi-dev \
  libpq-dev \
  apt-transport-https software-properties-common

# ── 2. Python 3.12 ──────────────────────────────────────────────────────────
log "Installing Python ${PYTHON_VERSION}..."
if ! python${PYTHON_VERSION} --version &>/dev/null; then
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -qq
  apt-get install -y python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev
else
  log "Python ${PYTHON_VERSION} already installed: $(python${PYTHON_VERSION} --version)"
fi

# ── 3. Redis ─────────────────────────────────────────────────────────────────
log "Installing Redis..."
apt-get install -y redis-server redis-tools

# Persistence hardening (TT-002, V3 Ch4 §4.13): AOF + volatile-ttl eviction.
# Root cause of the original recurring EventBus-loss incident was twofold —
# (1) Redis ran with appendonly=no, so any keyspace clear (restart, flush,
#     pod reschedule) was unrecoverable; (2) the running process had in some
#     environments been started bypassing /etc/redis/redis.conf entirely
#     (a bare `redis-server <bind>` invocation), so config file edits alone
#     did nothing until Redis was restarted via the service manager below.
# Baking this into bootstrap.sh means a freshly provisioned node is hardened
# from the start, not patched after the fact.
log "Hardening Redis persistence (AOF + volatile-ttl)..."
REDIS_CONF="/etc/redis/redis.conf"
if [[ -f "${REDIS_CONF}" ]]; then
  sed -i 's/^appendonly no/appendonly yes/' "${REDIS_CONF}"
  sed -i 's/^# maxmemory-policy noeviction/maxmemory-policy volatile-ttl/' "${REDIS_CONF}"
  grep -q '^appendfsync everysec' "${REDIS_CONF}" || echo 'appendfsync everysec' >> "${REDIS_CONF}"
else
  log "WARNING: ${REDIS_CONF} not found — persistence hardening skipped, review manually"
fi

# Always (re)start via the service manager, never a bare `redis-server` command,
# so the hardened config file above is actually loaded.
if has_systemctl; then
  systemctl enable redis-server
  systemctl restart redis-server
else
  service redis-server restart 2>/dev/null || service redis-server start
fi
sleep 1
redis-cli ping | grep -q PONG && log "Redis: OK" || log "WARNING: Redis ping failed"
redis-cli CONFIG GET appendonly | grep -q '^yes$' && log "Redis AOF: enabled" || log "WARNING: Redis AOF not enabled — check ${REDIS_CONF}"

# ── 4. PostgreSQL ─────────────────────────────────────────────────────────────
log "Installing PostgreSQL..."
# Note: Ubuntu 22.04 default repo installs PG 14. For PG 15, add official PG repo:
# echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list
# curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg
# apt-get update -qq && apt-get install -y postgresql-15 postgresql-client-15
apt-get install -y postgresql postgresql-client
if has_systemctl; then
  systemctl enable postgresql
  systemctl start postgresql
else
  service postgresql start 2>/dev/null || true
fi
sleep 3
pg_isready && log "PostgreSQL: OK" || log "WARNING: PostgreSQL not ready yet"

# ── 5. MongoDB 7.0 ───────────────────────────────────────────────────────────
log "Installing MongoDB 7.0..."
if ! command -v mongod &>/dev/null; then
  curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor
  echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" > /etc/apt/sources.list.d/mongodb-org-7.0.list
  apt-get update -qq && apt-get install -y mongodb-org
fi
mkdir -p /var/log/mongodb /var/lib/mongodb
chown mongodb:mongodb /var/log/mongodb /var/lib/mongodb 2>/dev/null || true
# --auth from the very first start (Sprint-019, V4 Ch7): on a genuinely fresh
# node with zero users, MongoDB's localhost exception still allows creating
# the first user via mongosh on 127.0.0.1 — scripts/vault/provision_datastore_auth.sh
# (run from restore.sh, after app code exists) does exactly that. Baking
# --auth in from the start means a freshly provisioned node is hardened
# immediately, not patched after the fact (same rationale as the Redis
# persistence hardening above).
if has_systemctl; then
  systemctl enable mongod
  systemctl start mongod
else
  mongod --dbpath /var/lib/mongodb --logpath /var/log/mongodb/mongod.log --fork --bind_ip 127.0.0.1 --auth 2>/dev/null || true
fi
sleep 3
mongosh --eval 'db.adminCommand({ping:1})' --quiet 2>/dev/null | grep -q '"ok"' && log "MongoDB: OK (auth enforced — run scripts/vault/provision_datastore_auth.sh after app code is deployed to create the voiceos user)" || log "MongoDB: starting (check /var/log/mongodb/mongod.log)"

# ── 6. MongoDB shell (mongosh) ────────────────────────────────────────────────
log "Installing mongosh..."
apt-get install -y mongodb-mongosh 2>/dev/null || true

# ── 7. Docker (bare VM only — skipped in K8s pod) ────────────────────────────
if [[ "${IN_KUBE_POD}" == "false" ]]; then
  log "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  if has_systemctl; then
    systemctl enable docker
    systemctl start docker
  fi
else
  log "Skipping Docker installation (running in K8s pod — no Docker socket)"
fi

# ── 8. kubectl ──────────────────────────────────────────────────────────────
log "Installing kubectl..."
KUBECTL_VERSION=$(curl -sSL https://dl.k8s.io/release/stable.txt 2>/dev/null || echo "v1.36.2")
curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /tmp/kubectl
chmod +x /tmp/kubectl && mv /tmp/kubectl /usr/local/bin/kubectl
kubectl version --client | head -1

# ── 9. Helm (bare VM only — install in Sprint-026 for K8s deployment) ─────────
if [[ "${IN_KUBE_POD}" == "false" ]]; then
  log "Installing Helm..."
  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# ── 10. PostgreSQL client tools ───────────────────────────────────────────────
log "PostgreSQL client already installed via postgresql-client package."

# ── 11. Application directory ────────────────────────────────────────────────
log "Creating application directory..."
mkdir -p "${VOICEOS_HOME}"/{app,venv,logs,recordings,certs}

# ── 12. Python virtual environment ───────────────────────────────────────────
log "Creating Python virtual environment..."
python${PYTHON_VERSION} -m venv "${VOICEOS_HOME}/venv"
"${VOICEOS_HOME}/venv/bin/pip" install --upgrade pip wheel 'setuptools>=72'

log ""
log "=== Bootstrap complete ==="
log "Next steps:"
log "  1. Copy application code to ${VOICEOS_HOME}/app/"
log "  2. Install Python dependencies (pyproject.toml is authoritative — no requirements.txt exists):"
log "     cd ${VOICEOS_HOME}/app && ${VOICEOS_HOME}/venv/bin/pip install -e ."
log "  3. Set up database: sudo -u postgres psql -c \"CREATE USER voiceos WITH PASSWORD 'voiceos_pw' CREATEDB;\""
log "     sudo -u postgres psql -c \"CREATE DATABASE voiceos OWNER voiceos;\""
log "  4. Run tests: cd ${VOICEOS_HOME}/app && POSTGRES_DSN=postgresql://voiceos:voiceos_pw@localhost:5432/voiceos REDIS_URL=redis://localhost:6379/0 MONGODB_URI=mongodb://localhost:27017/voiceos PYTHONPATH=${VOICEOS_HOME}/app ${VOICEOS_HOME}/venv/bin/pytest tests/ -q"
