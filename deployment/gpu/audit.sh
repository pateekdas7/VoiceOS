#!/usr/bin/env bash
# ==============================================================================
# VoiceOS GPU Node Audit Script
# Captures complete current state of the GPU node for comparison against spec.
#
# Usage:
#   bash deployment/gpu/audit.sh
#   bash deployment/gpu/audit.sh | tee deployment/gpu/deployment_reports/AUDIT_$(date +%Y%m%d).txt
#
# Output: structured human-readable report to stdout.
# Safe to run on a live production node — read-only, no side effects.
#
# Spec reference: docs/deployment/GPU_RUNTIME_SPEC.md
# ==============================================================================

set -uo pipefail

VOICEOS_HOME="/opt/voiceos-gpu"
VENV_PYTHON="${VOICEOS_HOME}/venv/bin/python"

TS="$(date '+%Y-%m-%d %H:%M:%S %Z')"
HOST="$(hostname -f 2>/dev/null || hostname)"

_h1() { echo; echo "=== $* ==="; }
_h2() { echo "--- $* ---"; }
_row() { printf "  %-36s %s\n" "$1" "$2"; }
_missing() { printf "  %-36s %s\n" "$1" "MISSING"; }

_py() {
    if [[ -x "${VENV_PYTHON}" ]]; then
        "${VENV_PYTHON}" -c "$1" 2>/dev/null || echo "error"
    else
        echo "venv not found"
    fi
}

_pkg() {
    _py "import $1; print($1.__version__)" 2>/dev/null || echo "not installed"
}

_dirsize() {
    if [[ -d "$1" ]]; then
        du -sm "$1" 2>/dev/null | cut -f1
    else
        echo "0 (missing)"
    fi
}

_filecount() {
    if [[ -d "$1" ]]; then
        find "$1" -type f 2>/dev/null | wc -l
    else
        echo "0 (missing)"
    fi
}

echo "============================================================"
echo "  VoiceOS GPU Node Audit"
echo "  Host      : ${HOST}"
echo "  Timestamp : ${TS}"
echo "  Script    : deployment/gpu/audit.sh"
echo "  Spec ref  : docs/deployment/GPU_RUNTIME_SPEC.md"
echo "============================================================"

# ── OS ────────────────────────────────────────────────────────────────────────
_h1 "OS"
_row "Distro"        "$(lsb_release -d 2>/dev/null | cut -f2 || cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '"')"
_row "Kernel"        "$(uname -r)"
_row "Architecture"  "$(uname -m)"
_row "Uptime"        "$(uptime -p 2>/dev/null || uptime)"

# ── CPU / RAM / Disk ─────────────────────────────────────────────────────────
_h1 "CPU / RAM / Disk"
_row "CPU model"     "$(grep 'model name' /proc/cpuinfo 2>/dev/null | head -1 | cut -d: -f2 | xargs || echo 'unknown')"
_row "CPU cores"     "$(nproc)"
RAM_TOTAL_MB="$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 'unknown')"
RAM_AVAIL_MB="$(free -m 2>/dev/null | awk '/^Mem:/{print $7}' || echo 'unknown')"
_row "RAM total"     "${RAM_TOTAL_MB} MB ($(( RAM_TOTAL_MB / 1024 )) GB)"
_row "RAM available" "${RAM_AVAIL_MB} MB"

DISK_USED="$(df -BG "${VOICEOS_HOME}" 2>/dev/null | awk 'NR==2{print $3}' || df -BG / 2>/dev/null | awk 'NR==2{print $3}')"
DISK_FREE="$(df -BG "${VOICEOS_HOME}" 2>/dev/null | awk 'NR==2{print $4}' || df -BG / 2>/dev/null | awk 'NR==2{print $4}')"
DISK_TOTAL="$(df -BG "${VOICEOS_HOME}" 2>/dev/null | awk 'NR==2{print $2}' || df -BG / 2>/dev/null | awk 'NR==2{print $2}')"
_row "Disk (${VOICEOS_HOME})" "Used: ${DISK_USED} / Total: ${DISK_TOTAL} / Free: ${DISK_FREE}"

# ── NVIDIA / CUDA ─────────────────────────────────────────────────────────────
_h1 "NVIDIA / CUDA"
if command -v nvidia-smi &>/dev/null; then
    _row "Driver version"  "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
    _row "GPU model"       "$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
    _row "GPU UUID"        "$(nvidia-smi --query-gpu=uuid --format=csv,noheader | head -1)"
    _row "VRAM total"      "$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1) MiB"
    _row "VRAM used"       "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1) MiB"
    _row "VRAM free"       "$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1) MiB"
    _row "GPU utilization" "$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -1)%"
    _row "GPU temp"        "$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | head -1) °C"
    _row "Power draw"      "$(nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits | head -1) W"
else
    _missing "nvidia-smi"
fi

if [[ -f /usr/local/cuda/version.json ]]; then
    CUDA_VER="$(python3 -c "import json; d=json.load(open('/usr/local/cuda/version.json')); print(d.get('cuda',{}).get('version','unknown'))" 2>/dev/null || echo 'parse error')"
    _row "CUDA toolkit"    "${CUDA_VER}"
elif [[ -f /usr/local/cuda/version.txt ]]; then
    _row "CUDA toolkit"    "$(cat /usr/local/cuda/version.txt | head -1)"
else
    _missing "CUDA toolkit (/usr/local/cuda)"
fi

if command -v nvcc &>/dev/null; then
    _row "nvcc"            "$(nvcc --version 2>/dev/null | grep 'release' | awk '{print $5}' | tr -d ',')"
else
    _missing "nvcc"
fi

# ── Python / venv ─────────────────────────────────────────────────────────────
_h1 "Python / venv"
if command -v python3.12 &>/dev/null; then
    _row "Python 3.12 (system)"  "$(python3.12 --version 2>/dev/null)"
else
    _missing "Python 3.12 (system)"
fi

if [[ -x "${VENV_PYTHON}" ]]; then
    _row "venv python"  "$(${VENV_PYTHON} --version 2>/dev/null)"
    _row "venv path"    "${VOICEOS_HOME}/venv"
    PKG_COUNT="$("${VOICEOS_HOME}/venv/bin/pip" list 2>/dev/null | wc -l)"
    _row "venv packages"  "${PKG_COUNT} installed"
else
    _missing "venv (${VOICEOS_HOME}/venv/bin/python)"
fi

# ── Python package versions ────────────────────────────────────────────────────
_h1 "Python Packages (venv)"
_row "torch"               "$(_py "import torch; print(torch.__version__)")"
_row "torch.cuda"          "$(_py "import torch; print(torch.cuda.is_available())")"
_row "torch.cudnn"         "$(_py "import torch; v=torch.backends.cudnn.version(); print(f'{v // 1000}.{(v % 1000) // 100}.{v % 100}' if v else 'unavailable')")"
_row "vllm"                "$(_pkg vllm)"
_row "faster_whisper"      "$(_pkg faster_whisper)"
_row "ctranslate2"         "$(_pkg ctranslate2)"
_row "transformers"        "$(_pkg transformers)"
_row "fastapi"             "$(_pkg fastapi)"
_row "uvicorn"             "$(_pkg uvicorn)"
_row "numpy"               "$(_pkg numpy)"
_row "pydantic"            "$(_pkg pydantic)"
_row "huggingface_hub"     "$(_pkg huggingface_hub)"

# ── Container / Docker ────────────────────────────────────────────────────────
_h1 "Container / Docker"
if command -v docker &>/dev/null; then
    _row "Docker version"  "$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')"
    _row "Docker runtime"  "$(docker info 2>/dev/null | grep 'Default Runtime' | awk '{print $3}' || echo 'unknown')"
else
    _missing "Docker"
fi

if command -v nvidia-ctk &>/dev/null; then
    _row "nvidia-ctk"  "$(nvidia-ctk --version 2>/dev/null | head -1 | awk '{print $NF}')"
else
    _missing "nvidia-ctk"
fi

if [[ -f /etc/docker/daemon.json ]]; then
    _row "daemon.json default-runtime"  "$(python3 -c "import json; print(json.load(open('/etc/docker/daemon.json')).get('default-runtime','not set'))" 2>/dev/null || echo 'parse error')"
else
    _missing "/etc/docker/daemon.json"
fi

# ── Models ────────────────────────────────────────────────────────────────────
_h1 "Models"
_row "Whisper dir size"  "$(_dirsize "${VOICEOS_HOME}/models/whisper-large-v3-turbo") MB (spec: ~1,547 MB)"
_row "Whisper file count"  "$(_filecount "${VOICEOS_HOME}/models/whisper-large-v3-turbo") files"
_row "Qwen dir size"  "$(_dirsize "${VOICEOS_HOME}/models/qwen2.5-7b-fp8") MB (spec: ~8,306 MB)"
_row "Qwen file count"  "$(_filecount "${VOICEOS_HOME}/models/qwen2.5-7b-fp8") files"
_row "Veena dir size"  "$(_dirsize "${VOICEOS_HOME}/models/veena-fp16") MB"
_row "Veena file count"  "$(_filecount "${VOICEOS_HOME}/models/veena-fp16") files"

# SNAC may be at snac-24khz or in the HF cache
SNAC_PATH="${VOICEOS_HOME}/models/snac-24khz"
_row "SNAC dir size"  "$(_dirsize "${SNAC_PATH}") MB"
_row "SNAC file count"  "$(_filecount "${SNAC_PATH}") files"

# ── Services / systemd ────────────────────────────────────────────────────────
_h1 "Services (systemd)"
for SVC in voiceos-llm.service voiceos-stt.service voiceos-tts.service; do
    if systemctl list-unit-files "${SVC}" &>/dev/null 2>&1 | grep -q "${SVC}"; then
        STATUS="$(systemctl is-active "${SVC}" 2>/dev/null || echo 'inactive')"
        ENABLED="$(systemctl is-enabled "${SVC}" 2>/dev/null || echo 'unknown')"
        _row "${SVC}"  "${STATUS} / enabled=${ENABLED}"
    else
        _missing "${SVC}"
    fi
done

# ── Ports ─────────────────────────────────────────────────────────────────────
_h1 "Ports"
for PORT in 8000 8100 8200; do
    if ss -tnlp 2>/dev/null | grep -q ":${PORT} " || netstat -tnlp 2>/dev/null | grep -q ":${PORT} "; then
        _row "Port ${PORT}"  "BOUND"
    else
        _row "Port ${PORT}"  "not bound"
    fi
done

# ── Health endpoints ──────────────────────────────────────────────────────────
_h1 "Health Endpoints"
for URL in \
    "http://localhost:8000/health (LLM)" \
    "http://localhost:8100/health/ready (STT ready)" \
    "http://localhost:8100/health/live (STT live)" \
    "http://localhost:8200/health/ready (TTS ready)" \
    "http://localhost:8200/health/live (TTS live)"
do
    ENDPOINT="${URL%% *}"
    LABEL="${URL#* }"
    HTTP_STATUS="$(curl -so /dev/null -w "%{http_code}" --max-time 3 "${ENDPOINT}" 2>/dev/null || echo '000')"
    _row "${LABEL}"  "HTTP ${HTTP_STATUS}"
done

# ── Environment ───────────────────────────────────────────────────────────────
_h1 "Environment"
if [[ -f "${VOICEOS_HOME}/.env" ]]; then
    _row ".env file"  "present ($(wc -l < "${VOICEOS_HOME}/.env") lines)"
else
    _missing ".env (${VOICEOS_HOME}/.env)"
fi

if [[ -d "${VOICEOS_HOME}/logs" ]]; then
    if [[ -w "${VOICEOS_HOME}/logs" ]]; then
        _row "log dir"  "present and writable"
    else
        _row "log dir"  "present but NOT WRITABLE"
    fi
else
    _missing "log dir (${VOICEOS_HOME}/logs)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo
echo "============================================================"
echo "  Audit complete — compare above against:"
echo "  docs/deployment/GPU_RUNTIME_SPEC.md"
echo "  docs/deployment/GPU_COMPATIBILITY_MATRIX.md"
echo "============================================================"
