#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# VoiceOS Phone 2 — One-Shot Termux Setup Script
#
# Replaces the cloud CPU VPS with a second Android phone running Termux.
# Run this on a fresh Termux installation on the second phone.
#
# Prerequisites:
#   - Termux installed (F-Droid build, not Play Store)
#   - Internet access
#   - At least 4 GB free storage
#
# Usage:
#   bash setup_phone2.sh
#
# After setup:
#   1. Edit ~/VoiceOS/deployment/cpu/.env — fill in Twilio creds + Kaggle tunnel URLs
#   2. Run ~/start_voiceos.sh
#   3. Copy cloudflared tunnel URL → Twilio Console webhook URL
# ==============================================================================

set -euo pipefail

VOICEOS_DIR="$HOME/VoiceOS"
PG_DATA="$PREFIX/var/lib/postgresql"
REDIS_CONF="$HOME/redis.conf"
LOG="$HOME/voiceos_setup.log"

log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }
warn() { echo "[$(date '+%H:%M:%S')] WARN: $*" | tee -a "$LOG"; }
die()  { echo "FATAL: $*" >&2; exit 1; }
ok()   { echo "[$(date '+%H:%M:%S')]  OK: $*" | tee -a "$LOG"; }

log "=== VoiceOS Phone 2 Setup ==="
log "Log: $LOG"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. System packages
# ─────────────────────────────────────────────────────────────────────────────
log "--- [1/9] System packages ---"
pkg update -y && pkg upgrade -y
pkg install -y \
    python git tmux curl wget \
    build-essential clang make \
    libffi openssl \
    postgresql redis \
    golang rust
ok "System packages installed"

# ─────────────────────────────────────────────────────────────────────────────
# 2. cloudflared (built from source via Go — the GitHub ARM64 binary is not PIE
#    and won't run on Android. Go on Termux builds PIE automatically.)
# ─────────────────────────────────────────────────────────────────────────────
log "--- [2/9] cloudflared (building from source — takes ~10 min) ---"
if command -v cloudflared &>/dev/null; then
    warn "cloudflared already installed: $(cloudflared version 2>&1 | head -1)"
else
    # Ensure ~/go/bin is on PATH for this session
    export PATH="$PATH:$HOME/go/bin"
    go install github.com/cloudflare/cloudflared/cmd/cloudflared@latest \
        && ok "cloudflared built and installed at ~/go/bin/cloudflared" \
        || warn "cloudflared build failed — use 'ssh -R 80:localhost:8010 nokey@localhost.run' as tunnel instead"
    # Persist PATH for future sessions
    grep -q 'go/bin' "$HOME/.bashrc" 2>/dev/null \
        || echo 'export PATH=$PATH:$HOME/go/bin' >> "$HOME/.bashrc"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. Python packages
# ─────────────────────────────────────────────────────────────────────────────
log "--- [3/9] Python packages ---"
pip install --upgrade pip wheel 'setuptools>=72'

# Core packages from pyproject.toml.
# NOTE: psycopg2-binary (not psycopg2) — binary wheel confirmed working on Termux ARM64.
# NOTE: onnxruntime — used for Silero VAD (CPU-only). If install fails, VAD falls back
#       to energy-based endpointing; the call still works.
# NOTE: uvicorn[standard] installs uvloop + httptools (C extensions). May fail on older
#       Termux — if so, fall back to plain uvicorn at bottom of this section.
pip install \
    "pydantic>=2.7" \
    "prometheus-client>=0.20" \
    "numpy>=1.26" \
    "scipy>=1.13" \
    "httpx>=0.27" \
    "redis>=5.0" \
    "psycopg2-binary>=2.9" \
    "alembic>=1.13" \
    "sqlalchemy>=2.0" \
    "starlette>=0.37" \
    "opentelemetry-api>=1.25" \
    "opentelemetry-sdk>=1.25" \
    "opentelemetry-exporter-otlp-proto-http>=1.25" \
    "pyjwt>=2.8" \
    "cryptography>=42" \
    "hvac>=2.1" \
    "openpyxl>=3.1" \
    "reportlab>=4.2" \
    "pyyaml>=6.0"

# onnxruntime: optional, best-effort
log "  Installing onnxruntime (optional — VAD)..."
pip install "onnxruntime>=1.18" 2>/dev/null \
    && ok "onnxruntime installed" \
    || warn "onnxruntime install failed — VAD will use energy-based endpointing"

# uvicorn with WebSocket support (required for Twilio Media Streams)
log "  Installing uvicorn[standard]..."
pip install "uvicorn[standard]>=0.30" 2>/dev/null \
    && ok "uvicorn[standard] installed" \
    || { warn "uvicorn[standard] failed — trying plain uvicorn + websockets"; pip install "uvicorn>=0.30" "websockets>=12.0"; }

ok "Python packages installed"

# ─────────────────────────────────────────────────────────────────────────────
# 4. Clone VoiceOS repo
# ─────────────────────────────────────────────────────────────────────────────
log "--- [4/9] VoiceOS repository ---"
if [ -d "$VOICEOS_DIR/.git" ]; then
    warn "Repo already exists at $VOICEOS_DIR — pulling latest"
    git -C "$VOICEOS_DIR" pull origin claude/ssh-gpu-cpu-servers-y99fib 2>/dev/null || warn "git pull failed — using existing code"
else
    echo ""
    echo "Enter your VoiceOS GitHub repo URL"
    echo "(e.g. https://github.com/yourname/VoiceOS.git):"
    read -r REPO_URL
    [ -z "$REPO_URL" ] && die "Repo URL cannot be empty"
    git clone --branch claude/ssh-gpu-cpu-servers-y99fib "$REPO_URL" "$VOICEOS_DIR" \
        || git clone "$REPO_URL" "$VOICEOS_DIR"
fi

cd "$VOICEOS_DIR"
pip install -e . --no-deps 2>/dev/null || pip install -e .
ok "VoiceOS repo ready at $VOICEOS_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# 5. PostgreSQL
# ─────────────────────────────────────────────────────────────────────────────
log "--- [5/9] PostgreSQL ---"

if [ ! -d "$PG_DATA" ] || [ -z "$(ls -A "$PG_DATA" 2>/dev/null)" ]; then
    log "  Initializing PostgreSQL data directory..."
    initdb -D "$PG_DATA" --encoding=UTF8 --locale=C
    ok "PostgreSQL initialized"
else
    warn "PostgreSQL data directory exists — skipping initdb"
fi

# Start temporarily to create DB/user
log "  Starting PostgreSQL to create voiceos user/DB..."
pg_ctl -D "$PG_DATA" -l "$HOME/postgres_setup.log" start
sleep 3

# Create user and database (idempotent)
psql -U "$(whoami)" -d postgres -c \
    "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='voiceos') THEN CREATE USER voiceos WITH PASSWORD 'voiceos_dev' CREATEDB; END IF; END \$\$;" \
    2>/dev/null || warn "Could not create voiceos user (may already exist)"

psql -U "$(whoami)" -d postgres -c \
    "SELECT 1 FROM pg_database WHERE datname='voiceos'" | grep -q 1 \
    || psql -U "$(whoami)" -d postgres -c "CREATE DATABASE voiceos OWNER voiceos;"

psql -U voiceos -d voiceos -c "SELECT 1 AS ping;" > /dev/null \
    && ok "PostgreSQL: voiceos DB accessible" \
    || warn "PostgreSQL: connection test failed — check $HOME/postgres_setup.log"

pg_ctl -D "$PG_DATA" stop
ok "PostgreSQL configured (password: voiceos_dev — CHANGE IN .env for production)"

# ─────────────────────────────────────────────────────────────────────────────
# 6. Redis
# ─────────────────────────────────────────────────────────────────────────────
log "--- [6/9] Redis ---"
cat > "$REDIS_CONF" <<'REDIS_EOF'
bind 127.0.0.1
port 6379
daemonize no
loglevel notice
databases 16
# Persistence (AOF + RDB)
appendonly yes
appendfsync everysec
save 900 1
save 300 10
save 60 10000
dir /data/data/com.termux/files/home
maxmemory-policy volatile-ttl
REDIS_EOF
ok "Redis config written to $REDIS_CONF"

# ─────────────────────────────────────────────────────────────────────────────
# 7. .env file
# ─────────────────────────────────────────────────────────────────────────────
log "--- [7/9] Environment variables ---"
ENV_FILE="$VOICEOS_DIR/deployment/cpu/.env"

if [ -f "$ENV_FILE" ]; then
    warn ".env already exists — skipping (edit manually if needed)"
else
cat > "$ENV_FILE" <<'ENV_EOF'
# ==============================================================================
# VoiceOS Phone 2 — Local .env
# Fill in every CHANGE_ME before running start_voiceos.sh
# NEVER commit this file.
# ==============================================================================

SERVICE_ENV=production
LOG_LEVEL=INFO

# ── Redis (local, no password for dev) ────────────────────────────────────────
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=

# ── EventBus ──────────────────────────────────────────────────────────────────
EVENT_BUS_STREAM=voiceos-events
EVENT_BUS_DLQ=dlq:voiceos-events
EVENT_BUS_CONSUMER_GROUP=main-group
EVENT_BUS_MAX_RETRIES=3

# ── PostgreSQL (local) ────────────────────────────────────────────────────────
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=voiceos
POSTGRES_USER=voiceos
POSTGRES_PASSWORD=voiceos_dev

# ── MongoDB (optional — skip if not using) ────────────────────────────────────
# MONGO_URI=mongodb://voiceos:voiceos_dev@127.0.0.1:27017/voiceos

# ── GPU Node (Kaggle T4×2 tunnel URLs) ───────────────────────────────────────
# Get these from Kaggle kernel output after starting the GPU kernel.
# Each service exposes its own Cloudflare Quick Tunnel URL.
GPU_NODE_HOST=CHANGE_ME
LLM_BASE_URL=https://CHANGE_ME.trycloudflare.com
STT_BASE_URL=https://CHANGE_ME.trycloudflare.com
TTS_BASE_URL=https://CHANGE_ME.trycloudflare.com
GPU_SCHEDULER_URL=http://CHANGE_ME:8084

# ── Twilio ────────────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID=CHANGE_ME
TWILIO_AUTH_TOKEN=CHANGE_ME
DEFAULT_TENANT_ID=client-0

# ── Media Gateway ─────────────────────────────────────────────────────────────
# Port the Twilio WebSocket entrypoint listens on
MEDIA_GATEWAY_PORT=8010
# Your cloudflared tunnel URL for this phone (set after step 4 of start_voiceos.sh)
# Format: wss://xxxx-yyyy.trycloudflare.com  (no trailing slash, no path)
PUBLIC_WS_BASE_URL=wss://CHANGE_ME.trycloudflare.com

# ── Web API ────────────────────────────────────────────────────────────────────
WEBAPI_PORT=8001
WEBAPI_WORKERS=1

# ── Auth ──────────────────────────────────────────────────────────────────────
# Generate: python3 -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET=CHANGE_ME
JWT_PUBLIC_KEY_PATH=/data/data/com.termux/files/home/VoiceOS/certs/jwt_public_key.pem
JWT_ISSUER=voiceos
JWT_AUDIENCE=voiceos
JWT_EXPIRY_SECONDS=3600

# ── Vault (optional — skip if not running HashiCorp Vault) ───────────────────
VAULT_ADDR=http://127.0.0.1:8200
VAULT_TOKEN=CHANGE_ME

# ── Observability ─────────────────────────────────────────────────────────────
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
SERVICE_NAME=voiceos-conversation-engine

# ── Recordings ────────────────────────────────────────────────────────────────
RECORDINGS_DIR=/data/data/com.termux/files/home/voiceos-recordings
CALL_RECORDING_DIR=/data/data/com.termux/files/home/voiceos-recordings

# ── Misc ──────────────────────────────────────────────────────────────────────
AI_GOVERNANCE_HUMAN_REVIEW_THRESHOLD=0.9
CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
CIRCUIT_BREAKER_WINDOW_SECONDS=30
CIRCUIT_BREAKER_COOLDOWN_SECONDS=30
AUDIT_LOG_RETENTION_DAYS=30
CONSENT_REQUIRED=true
LENDER_NAME=Rajat Finance
STT_LANGUAGE=hi
ENV_EOF

ok ".env created at $ENV_FILE"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 8. Directories
# ─────────────────────────────────────────────────────────────────────────────
log "--- [8/9] Directories ---"
mkdir -p \
    "$HOME/voiceos-recordings" \
    "$HOME/voiceos-logs" \
    "$VOICEOS_DIR/certs"
ok "Directories created"

# ─────────────────────────────────────────────────────────────────────────────
# 9. Startup script
# ─────────────────────────────────────────────────────────────────────────────
log "--- [9/9] Startup script ---"
cat > "$HOME/start_voiceos.sh" <<STARTEOF
#!/data/data/com.termux/files/usr/bin/bash
# Start all VoiceOS services in a named tmux session.
# Run this every time after rebooting the phone.
# Attach with: tmux attach -t voiceos
set -euo pipefail

VOICEOS_DIR="\$HOME/VoiceOS"
PG_DATA="$PREFIX/var/lib/postgresql"
REDIS_CONF="\$HOME/redis.conf"
ENV_FILE="\$VOICEOS_DIR/deployment/cpu/.env"

# Sanity check
if grep -q 'CHANGE_ME' "\$ENV_FILE" 2>/dev/null; then
    echo "ERROR: \$ENV_FILE still has CHANGE_ME placeholders."
    echo "Edit it first, then rerun this script."
    exit 1
fi

# Kill any existing session
tmux kill-session -t voiceos 2>/dev/null || true
tmux new-session -d -s voiceos -x 220 -y 50

# ── Window 0: PostgreSQL ──────────────────────────────────────────────────────
tmux rename-window -t voiceos:0 'postgres'
tmux send-keys -t voiceos:0 \
    "pg_ctl -D $PG_DATA -l \$HOME/voiceos-logs/postgres.log start && echo '[PG] started'" Enter

sleep 2  # give PG a moment before Redis + app try to connect

# ── Window 1: Redis ───────────────────────────────────────────────────────────
tmux new-window -t voiceos -n 'redis'
tmux send-keys -t voiceos:1 "redis-server \$REDIS_CONF" Enter

sleep 1

# ── Window 2: Media Gateway (Twilio WebSocket) ────────────────────────────────
# python deployment/cpu/app.py --serve  reads MEDIA_GATEWAY_PORT (default 8010)
tmux new-window -t voiceos -n 'media-gw'
tmux send-keys -t voiceos:2 \
    "cd \$VOICEOS_DIR && set -a; source \$ENV_FILE; set +a && python deployment/cpu/app.py --serve" Enter

# ── Window 3: Web API (admin / reporting / call summary endpoints) ────────────
# Served on WEBAPI_PORT (default 8001)
tmux new-window -t voiceos -n 'web-api'
tmux send-keys -t voiceos:3 \
    "cd \$VOICEOS_DIR && bash deployment/cpu/start_webapi.sh" Enter

# ── Window 4: tunnel (exposes media-gw to Twilio) ────────────────────────────
# URL changes every restart. Copy it into .env PUBLIC_WS_BASE_URL and Twilio Console.
# Uses cloudflared if available, otherwise falls back to localhost.run (SSH tunnel).
tmux new-window -t voiceos -n 'tunnel'
if command -v cloudflared &>/dev/null || [ -f "\$HOME/go/bin/cloudflared" ]; then
    CF="\${HOME}/go/bin/cloudflared"
    command -v cloudflared &>/dev/null && CF="cloudflared"
    tmux send-keys -t voiceos:4 "\$CF tunnel --url http://localhost:8010 2>&1 | tee \$HOME/voiceos-logs/tunnel.log" Enter
else
    tmux send-keys -t voiceos:4 "echo 'cloudflared not found — using localhost.run SSH tunnel'; ssh -R 80:localhost:8010 nokey@localhost.run 2>&1 | tee \$HOME/voiceos-logs/tunnel.log" Enter
fi

echo ""
echo "VoiceOS started in tmux session 'voiceos'."
echo ""
echo "  tmux attach -t voiceos        # attach"
echo "  Ctrl+b, 0-4                   # switch windows"
echo "  Ctrl+b, d                     # detach (services keep running)"
echo ""
echo "Windows:"
echo "  0: postgres     1: redis     2: media-gw (port 8010)"
echo "  3: web-api (port 8001)       4: cloudflared"
echo ""
echo "IMPORTANT:"
echo "  After cloudflared shows its tunnel URL:"
echo "  1. Add 'wss://<url>.trycloudflare.com' to .env as PUBLIC_WS_BASE_URL"
echo "  2. Set Twilio webhook to  'https://<url>.trycloudflare.com/voice'"
STARTEOF

chmod +x "$HOME/start_voiceos.sh"
ok "Startup script: ~/start_voiceos.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  VoiceOS Phone 2 setup complete!"
echo "============================================================"
echo ""
echo "Next steps:"
echo ""
echo "  1. Edit deployment config:"
echo "     nano \$VOICEOS_DIR/deployment/cpu/.env"
echo "     Fill in: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,"
echo "              LLM_BASE_URL, STT_BASE_URL, TTS_BASE_URL"
echo "              (from Kaggle kernel output after GPU kernel starts)"
echo ""
echo "  2. Start services:"
echo "     ~/start_voiceos.sh"
echo ""
echo "  3. After cloudflared shows tunnel URL, update .env and Twilio:"
echo "     PUBLIC_WS_BASE_URL=wss://<random>.trycloudflare.com"
echo "     Twilio Console → TwiML App → Voice URL"
echo "        → https://<random>.trycloudflare.com/voice"
echo ""
echo "  4. Test with smoke-test (requires .env filled):"
echo "     cd \$VOICEOS_DIR && python deployment/cpu/app.py --smoke-test"
echo ""
echo "  PostgreSQL password: voiceos_dev  (change in .env)"
echo "  Services log:        ~/voiceos-logs/"
echo "  Setup log:           $LOG"
echo ""
