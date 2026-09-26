#!/usr/bin/env bash
# Start the VoiceOS Python web_api (admin monitoring, Google OAuth, ops intelligence)
# on port 8001 alongside bff.js (port 8000).
#
# Required env vars: POSTGRES_DSN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
#                    FRONTEND_BASE_URL, BFF_PUBLIC_URL
# Optional: PROMETHEUS_URL, LOKI_URL, JAEGER_URL, GPU_HOST,
#           WEB_API_PRIVATE_KEY_PATH, WEB_API_PUBLIC_KEY_PATH,
#           VOICEOS_WEBHOOK_URL (for Alertmanager receiver override)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

# Source .env if present (local dev convenience)
if [[ -f deployment/cpu/.env ]]; then
  # shellcheck disable=SC1091
  set -a; source deployment/cpu/.env; set +a
fi

PORT="${WEBAPI_PORT:-8001}"
WORKERS="${WEBAPI_WORKERS:-1}"

exec uvicorn \
  "src.services.web_api.main:create_app" \
  --factory \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$WORKERS" \
  --log-level info \
  "$@"
