#!/usr/bin/env bash
# ==============================================================================
# VoiceOS GPU Node Restore Script
# Downloads models and starts all GPU inference services on a bootstrapped node.
# Assumes bootstrap.sh has already been run.
#
# Validated on: NVIDIA L4 / Driver 580.126.20 / CUDA 13.0 (2026-07-02)
#
# Usage:
#   ./restore.sh
#
# What it does:
#   1. Loads environment variables from .env
#   2. Downloads any missing model weights (skips if already present)
#   3. Starts STT (Whisper), LLM (vLLM / Qwen2.5), TTS (Veena) services
#   4. Runs health checks and latency validation
#   5. Signals CPU node GPU Scheduler that this node is ready
#
# Model notes:
#   STT: Whisper large-v3-turbo downloaded by faster-whisper from
#        mobiuslabsgmbh/faster-whisper-large-v3-turbo (CTranslate2 binary).
#        compute_type=int8_float16 (INT8 weights, FP16 activations).
#        FP8 is not supported by CTranslate2 4.8.0 on NVIDIA L4 (SM 8.9).
#
#   LLM: Qwen/Qwen2.5-7B-Instruct-FP8 does not exist on HuggingFace.
#        RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic is the production FP8 repo.
#        W8A8 FP8 (compressed-tensors format) is auto-detected by vLLM from
#        the model's quantization_config — no --dtype or --quantization flag needed.
#
#   TTS: Veena (3B, BF16) from the public maya-research/Veena repo + the SNAC 24 kHz
#        codec (hubertsiuzdak/snac_24khz). The internal registry
#        (models.voiceos.internal/veena-fp16) was unavailable (requires VPN), so
#        Sprint-009 Phase 2 switched to the public repo. Weights are BF16, NOT FP16.
#        The TTS server needs --snac-path pointing at the local SNAC codec.
#
#   GPU util: vLLM runs at --gpu-memory-utilization 0.45 (reduced from 0.55 in Sprint-028).
#        At 0.55, only 569 MiB remained free after all models loaded; ctranslate2's lazy
#        CUDA encoder workspace allocation (~600 MiB) OOM-ed on the first real STT request.
#        At 0.45: vLLM 10,388 MiB + Veena 8,558 MiB + Whisper 1,260 MiB + ctranslate2 ~600 MiB
#        = 20,289 MiB used / 2,745 MiB free on 23,034 MiB L4.
#        DO NOT raise above 0.45 without first verifying STT VRAM headroom.
# ==============================================================================

set -euo pipefail

VOICEOS_GPU_HOME="/opt/voiceos-gpu"
VENV="${VOICEOS_GPU_HOME}/venv"
MANIFEST="${VOICEOS_GPU_HOME}/deployment/model_manifest.yaml"

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAIL — $*"; exit 1; }

log "=== VoiceOS GPU Node Restore ==="

# ── Pre-flight ─────────────────────────────────────────────────────────────────
nvidia-smi &>/dev/null || fail "NVIDIA driver not found. Run bootstrap.sh first."
[[ -f "${VOICEOS_GPU_HOME}/.env" ]] || fail ".env not found. Copy from secure store."
[[ -f "${MANIFEST}" ]] || fail "model_manifest.yaml not found."
[[ -f "${VOICEOS_GPU_HOME}/deployment/download_models.py" ]] || fail "download_models.py not found."

# ── Load environment ──────────────────────────────────────────────────────────
set -a; source "${VOICEOS_GPU_HOME}/.env"; set +a

source "${VENV}/bin/activate"

# ── Verify Vault reachable (Sprint-019, V4 Ch7) ──────────────────────────────
# Vault runs on the CPU node, not here — this only checks reachability
# ahead of a future sprint's GPU-side secrets wiring (no GPU service reads
# from Vault yet; STT/LLM/TTS adapters still read model-serving config
# directly from .env, per Sprint-019.md's own "GPU node is not required
# during this sprint" scope note).
if [[ -n "${VAULT_ADDR:-}" ]]; then
  curl -sf "${VAULT_ADDR}/v1/sys/health" > /dev/null && log "Vault reachable at ${VAULT_ADDR}" \
    || log "WARNING: Vault not reachable at ${VAULT_ADDR} (not yet required by any GPU service)"
fi

# ── Model downloads ────────────────────────────────────────────────────────────
log "Checking and downloading model weights..."
python3 "${VOICEOS_GPU_HOME}/deployment/download_models.py" --manifest "${MANIFEST}"

# ── Start services ─────────────────────────────────────────────────────────────
log "Starting GPU inference services..."

# STT — Whisper Large-v3 Turbo (int8_float16 compute type)
# The STT service (Sprint-009) loads the model via:
#   WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16",
#                download_root=WHISPER_MODEL_PATH)
log "Starting Whisper STT service (port ${STT_SERVICE_PORT:-8100})..."
if systemctl is-enabled voiceos-stt &>/dev/null; then
  systemctl restart voiceos-stt
else
  setsid python3 "${VOICEOS_GPU_HOME}/services/stt/server.py" \
    --model-path "${WHISPER_MODEL_PATH:-${VOICEOS_GPU_HOME}/models/whisper-large-v3-turbo}" \
    --compute-type "int8_float16" \
    --port "${STT_SERVICE_PORT:-8100}" \
    >> "${VOICEOS_GPU_HOME}/logs/stt.log" 2>&1 < /dev/null &
  echo $! > /tmp/voiceos-stt.pid
fi

# TTS — Veena (3B BF16, maya-research/Veena) + SNAC 24 kHz codec
log "Starting Veena TTS service (port ${TTS_SERVICE_PORT:-8200})..."
if systemctl is-enabled voiceos-tts &>/dev/null; then
  systemctl restart voiceos-tts
else
  setsid python3 "${VOICEOS_GPU_HOME}/services/tts/server.py" \
    --model-path "${VEENA_MODEL_PATH:-${VOICEOS_GPU_HOME}/models/veena-fp16}" \
    --snac-path "${SNAC_MODEL_PATH:-${VOICEOS_GPU_HOME}/models/snac-24khz}" \
    --port "${TTS_SERVICE_PORT:-8200}" \
    >> "${VOICEOS_GPU_HOME}/logs/tts.log" 2>&1 < /dev/null &
  echo $! > /tmp/voiceos-tts.pid
fi

# LLM — Qwen2.5-7B-Instruct-FP8-dynamic via vLLM (longest startup ~90s)
# No --dtype or --quantization flags: vLLM auto-detects compressed-tensors FP8
# from quantization_config in the model's config.json.
# --dtype auto reads torch_dtype=bfloat16 for non-quantized operations.
log "Starting Qwen2.5 LLM via vLLM (port ${LLM_SERVICE_PORT:-8000})..."
if systemctl is-enabled voiceos-llm &>/dev/null; then
  systemctl restart voiceos-llm
else
  setsid "${VENV}/bin/vllm" serve \
    "${QWEN_MODEL_PATH:-${VOICEOS_GPU_HOME}/models/qwen2.5-7b-fp8}" \
    --dtype auto \
    --port "${LLM_SERVICE_PORT:-8000}" \
    --max-model-len 4096 \
    --gpu-memory-utilization "${GPU_MEMORY_FRACTION:-0.45}" \
    --served-model-name "qwen2.5-7b-instruct-fp8" \
    >> "${VOICEOS_GPU_HOME}/logs/llm.log" 2>&1 < /dev/null &
  echo $! > /tmp/voiceos-llm.pid
fi

# ── Wait for all services to be ready ─────────────────────────────────────────
log "Waiting for GPU services to warm up (Whisper: ~10s, Veena: ~10s, vLLM: ~90s)..."
MAX_WAIT=300
ELAPSED=0
INTERVAL=10

wait_for_service() {
  local url="$1"
  local name="$2"
  while ! curl -sf "${url}" &>/dev/null; do
    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
      fail "${name} did not become ready within ${MAX_WAIT}s"
    fi
    log "Waiting for ${name}... (${ELAPSED}s elapsed)"
    sleep "${INTERVAL}"
    ELAPSED=$((ELAPSED + INTERVAL))
  done
  log "OK — ${name} is ready"
}

wait_for_service "http://localhost:${STT_SERVICE_PORT:-8100}/health/ready" "Whisper STT"
wait_for_service "http://localhost:${TTS_SERVICE_PORT:-8200}/health/ready" "Veena TTS"
wait_for_service "http://localhost:${LLM_SERVICE_PORT:-8000}/health" "Qwen2.5 LLM (vLLM)"

# ── VRAM and health check ──────────────────────────────────────────────────────
log "VRAM allocation after all services started:"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader

log "Running health check..."
bash "${VOICEOS_GPU_HOME}/deployment/healthcheck.sh" || fail "Health check failed — see output above"

log "Running latency validation..."
python3 "${VOICEOS_GPU_HOME}/deployment/validate_latency.py" \
  || log "WARNING: Latency validation reported issues — check logs"

# ── Signal CPU node ────────────────────────────────────────────────────────────
log "Signaling CPU node GPU Scheduler..."
if [[ -n "${GPU_SCHEDULER_REPORT_URL:-}" ]]; then
  curl -sf -X POST "${GPU_SCHEDULER_REPORT_URL}/api/v1/gpu-node/ready" \
    -H "Content-Type: application/json" \
    -d "{\"node_id\":\"${HOSTNAME}\",\"status\":\"ready\"}" \
    && log "GPU Scheduler notified" \
    || log "WARNING: Could not notify GPU Scheduler"
fi

log ""
log "=== GPU Restore complete ==="
log "Models serving:"
log "  STT: http://localhost:${STT_SERVICE_PORT:-8100}/health/ready"
log "  LLM: http://localhost:${LLM_SERVICE_PORT:-8000}/health"
log "  TTS: http://localhost:${TTS_SERVICE_PORT:-8200}/health/ready"
log "Run: bash deployment/gpu/healthcheck.sh  to re-verify."
