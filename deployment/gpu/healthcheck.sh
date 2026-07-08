#!/usr/bin/env bash
# ==============================================================================
# VoiceOS GPU Node Health Check Script
# Verifies all GPU inference services are healthy.
# Sourced by restore.sh; can also be run standalone.
# ==============================================================================

set -euo pipefail

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] HEALTH $*"; }
ok()   { log "OK  — $*"; }
fail() { log "FAIL — $*"; HEALTH_FAILED=1; }

HEALTH_FAILED=0

# ── NVIDIA driver ─────────────────────────────────────────────────────────────
if nvidia-smi &>/dev/null; then
  VRAM_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
  VRAM_TOTAL=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
  ok "NVIDIA driver — VRAM: ${VRAM_USED}/${VRAM_TOTAL} MiB used"
else
  fail "NVIDIA driver — nvidia-smi failed"
fi

# ── GPU inference services ────────────────────────────────────────────────────
STT_PORT="${STT_SERVICE_PORT:-8100}"
LLM_PORT="${LLM_SERVICE_PORT:-8000}"
TTS_PORT="${TTS_SERVICE_PORT:-8200}"

# STT (Whisper)
if curl -sf "http://localhost:${STT_PORT}/health/ready" &>/dev/null; then
  ok "Whisper STT :${STT_PORT}"
else
  fail "Whisper STT :${STT_PORT} — /health/ready did not return 200"
fi

# LLM (vLLM / Qwen2.5)
if curl -sf "http://localhost:${LLM_PORT}/health" &>/dev/null; then
  ok "Qwen2.5 LLM :${LLM_PORT}"
else
  fail "Qwen2.5 LLM :${LLM_PORT} — /health did not return 200"
fi

# TTS (Veena)
if curl -sf "http://localhost:${TTS_PORT}/health/ready" &>/dev/null; then
  ok "Veena TTS :${TTS_PORT}"
else
  fail "Veena TTS :${TTS_PORT} — /health/ready did not return 200"
fi

# ── VRAM budget check ─────────────────────────────────────────────────────────
VRAM_USED_MB=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
VRAM_BUDGET=23034  # Actual NVIDIA L4 capacity; models target 24576 MB across multi-GPU
if [[ $VRAM_USED_MB -le $VRAM_BUDGET ]]; then
  ok "VRAM within capacity: ${VRAM_USED_MB} MiB ≤ ${VRAM_BUDGET} MiB"
else
  fail "VRAM over capacity: ${VRAM_USED_MB} MiB > ${VRAM_BUDGET} MiB"
fi

# ── Final result ──────────────────────────────────────────────────────────────
if [[ $HEALTH_FAILED -eq 0 ]]; then
  log "All GPU health checks PASSED"
else
  log "One or more GPU health checks FAILED — review output above"
  exit 1
fi
