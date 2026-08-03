#!/usr/bin/env bash
# ==============================================================================
# VoiceOS GPU Deployment Orchestrator
# 10-step deterministic deployment for the VoiceOS v2 GPU inference stack.
#
# Usage (as root):
#   sudo bash deployment/gpu/deploy.sh
#   sudo bash deployment/gpu/deploy.sh --skip-models   # skip model download
#   sudo bash deployment/gpu/deploy.sh --step 7        # resume from step 7
#
# Idempotent: each step checks for existing compliant state and skips if met.
# Never destroys a compliant component to re-install it.
#
# Spec reference: docs/deployment/GPU_DEPLOYMENT_MANIFEST.md
# ==============================================================================

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
VOICEOS_HOME="/opt/voiceos-gpu"
VENV="${VOICEOS_HOME}/venv"
VENV_PY="${VENV}/bin/python"
VENV_PIP="${VENV}/bin/pip"
PYTHON_VERSION="3.12"
DEPLOY_USER="${SUDO_USER:-ubuntu}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

NVIDIA_DRIVER_SPEC="580.126.20"
TORCH_VERSION="2.11.0"
TORCH_CUDA="cu130"
VLLM_VERSION="0.24.0"
FASTER_WHISPER_VERSION="1.2.1"
CTRANSLATE2_VERSION="4.8.0"
TRANSFORMERS_VERSION="5.12.1"
FASTAPI_VERSION="0.136.3"
UVICORN_VERSION="0.49.0"

START_STEP="${1:-1}"
SKIP_MODELS=0

for arg in "$@"; do
    case "${arg}" in
        --skip-models) SKIP_MODELS=1 ;;
        --step) shift; START_STEP="${1:-1}" ;;
    esac
done

TS="$(date '+%Y-%m-%d %H:%M:%S')"
REPORT_DIR="${VOICEOS_HOME}/deployment_reports"
REPORT_FILE="${REPORT_DIR}/DEPLOY_$(date +%Y%m%d_%H%M%S).txt"

log()  { echo "[${TS}] DEPLOY  $*" | tee -a "${REPORT_FILE}"; TS="$(date '+%Y-%m-%d %H:%M:%S')"; }
ok()   { echo "[${TS}] OK      $*" | tee -a "${REPORT_FILE}"; }
skip() { echo "[${TS}] SKIP    $*" | tee -a "${REPORT_FILE}"; }
fail() { echo "[${TS}] FAIL    $*" | tee -a "${REPORT_FILE}"; exit 1; }

_step() {
    local n="$1"; shift
    if [[ ${n} -lt ${START_STEP} ]]; then
        return 0
    fi
    echo
    echo "============================================================"
    log "STEP ${n}: $*"
    echo "============================================================"
}

_venv_py() { sudo -u "${DEPLOY_USER}" "${VENV_PY}" -c "$1" 2>/dev/null; }
_venv_pip_install() { sudo -u "${DEPLOY_USER}" "${VENV_PIP}" install --quiet "$@"; }

# Ensure report directory exists even before step 1
mkdir -p "${REPORT_DIR}"

log "=== VoiceOS GPU Deployment Orchestrator ==="
log "Repo    : ${REPO_ROOT}"
log "Home    : ${VOICEOS_HOME}"
log "User    : ${DEPLOY_USER}"
log "Report  : ${REPORT_FILE}"

# ── STEP 1: Pre-Deployment Audit ─────────────────────────────────────────────
_step 1 "Pre-Deployment Audit"
log "Capturing node state..."
bash "${REPO_ROOT}/deployment/gpu/audit.sh" 2>&1 | tee -a "${REPORT_FILE}" || true
ok "Audit captured to ${REPORT_FILE}"

# ── STEP 2: OS Verification ───────────────────────────────────────────────────
_step 2 "OS Verification"
OS_ID="$(lsb_release -si 2>/dev/null || echo 'unknown')"
OS_RELEASE="$(lsb_release -sr 2>/dev/null || echo 'unknown')"
KERNEL="$(uname -r)"
log "OS: ${OS_ID} ${OS_RELEASE} — kernel ${KERNEL}"
if [[ "${OS_ID}" != "Ubuntu" ]]; then
    fail "OS is ${OS_ID}, spec requires Ubuntu. Abort."
fi
log "OS check: Ubuntu ${OS_RELEASE}"

# ── STEP 3: System Package Installation ───────────────────────────────────────
_step 3 "System Packages"
apt-get update -qq
apt-get install -y \
    curl wget git unzip jq \
    build-essential pkg-config \
    ca-certificates gnupg lsb-release \
    net-tools htop iotop \
    software-properties-common pciutils \
    2>/dev/null | tail -5
ok "System packages installed"

# ── STEP 4: NVIDIA Driver ─────────────────────────────────────────────────────
_step 4 "NVIDIA Driver"
if nvidia-smi &>/dev/null; then
    DRIVER_VER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
    log "NVIDIA driver present: ${DRIVER_VER} (spec: ${NVIDIA_DRIVER_SPEC})"
    if [[ "${DRIVER_VER}" == "${NVIDIA_DRIVER_SPEC}" ]]; then
        skip "Driver ${DRIVER_VER} matches spec — no reinstall needed"
    else
        log "WARNING: Driver ${DRIVER_VER} differs from spec ${NVIDIA_DRIVER_SPEC}"
        log "Continuing — see GPU_COMPATIBILITY_MATRIX.md for version guidance"
    fi
else
    fail "nvidia-smi not found. Install NVIDIA driver ${NVIDIA_DRIVER_SPEC} and reboot, then re-run."
fi

# ── STEP 5: Python 3.12 ───────────────────────────────────────────────────────
_step 5 "Python ${PYTHON_VERSION}"
if ! command -v "python${PYTHON_VERSION}" &>/dev/null; then
    log "Python ${PYTHON_VERSION} not found — installing via deadsnakes PPA..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y "python${PYTHON_VERSION}" "python${PYTHON_VERSION}-venv" "python${PYTHON_VERSION}-dev"
    ok "Python ${PYTHON_VERSION} installed"
else
    PY_VER="$(python${PYTHON_VERSION} --version 2>/dev/null)"
    skip "Python ${PY_VER} already present"
fi

# ── STEP 6: Python Virtual Environment ────────────────────────────────────────
_step 6 "Python Virtual Environment"
if [[ -x "${VENV_PY}" ]]; then
    INSTALLED_PY_VER="$("${VENV_PY}" --version 2>/dev/null)"
    skip "venv already present — ${INSTALLED_PY_VER}"
else
    log "Creating venv at ${VENV}..."
    mkdir -p "${VOICEOS_HOME}"
    chown "${DEPLOY_USER}:${DEPLOY_USER}" "${VOICEOS_HOME}"
    sudo -u "${DEPLOY_USER}" "python${PYTHON_VERSION}" -m venv "${VENV}"
    sudo -u "${DEPLOY_USER}" "${VENV_PIP}" install --quiet --upgrade pip wheel setuptools
    ok "venv created at ${VENV}"
fi

# ── STEP 7: Directory Structure ───────────────────────────────────────────────
_step 7 "Directory Structure"
for DIR in \
    "${VOICEOS_HOME}/logs" \
    "${VOICEOS_HOME}/models/whisper-large-v3-turbo" \
    "${VOICEOS_HOME}/models/qwen2.5-7b-fp8" \
    "${VOICEOS_HOME}/models/veena-fp16" \
    "${VOICEOS_HOME}/models/snac-24khz" \
    "${VOICEOS_HOME}/cache" \
    "${VOICEOS_HOME}/services/stt" \
    "${VOICEOS_HOME}/services/llm" \
    "${VOICEOS_HOME}/services/tts" \
    "${VOICEOS_HOME}/deployment_reports"
do
    mkdir -p "${DIR}"
done
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${VOICEOS_HOME}"
ok "Directory structure created under ${VOICEOS_HOME}"

# ── STEP 8: Python Dependencies ───────────────────────────────────────────────
_step 8 "Python Dependencies"

_install_if_needed() {
    local pkg="$1"
    local version="$2"
    local import_name="${3:-$pkg}"
    INSTALLED="$(_venv_py "import ${import_name}; print(${import_name}.__version__)" 2>/dev/null || echo 'missing')"
    if [[ "${INSTALLED}" == "${version}" ]]; then
        skip "${pkg} ${version} already installed"
    else
        log "Installing ${pkg}==${version} (current: ${INSTALLED})..."
        _venv_pip_install "${pkg}==${version}"
        ok "${pkg} ${version} installed"
    fi
}

# PyTorch must be installed from the correct CUDA index URL
TORCH_INSTALLED="$(_venv_py "import torch; print(torch.__version__)" 2>/dev/null || echo 'missing')"
if [[ "${TORCH_INSTALLED}" == "${TORCH_VERSION}+${TORCH_CUDA}" ]]; then
    skip "torch ${TORCH_VERSION}+${TORCH_CUDA} already installed"
else
    log "Installing torch ${TORCH_VERSION}+${TORCH_CUDA}..."
    _venv_pip_install \
        "torch==${TORCH_VERSION}" \
        --index-url "https://download.pytorch.org/whl/${TORCH_CUDA}"
    ok "torch ${TORCH_VERSION}+${TORCH_CUDA} installed"
fi

_install_if_needed "vllm" "${VLLM_VERSION}"
_install_if_needed "faster-whisper" "${FASTER_WHISPER_VERSION}" "faster_whisper"
_install_if_needed "ctranslate2" "${CTRANSLATE2_VERSION}"
_install_if_needed "transformers" "${TRANSFORMERS_VERSION}"
_install_if_needed "fastapi" "${FASTAPI_VERSION}"
_install_if_needed "uvicorn" "${UVICORN_VERSION}"

# Remaining dependencies (install without strict version pinning if not already present)
for DEP in grpcio grpcio-tools prometheus-client numpy pydantic huggingface_hub; do
    INST="$(_venv_py "import ${DEP//[-.]/_}; print('ok')" 2>/dev/null || echo 'missing')"
    if [[ "${INST}" == "ok" ]]; then
        skip "${DEP} already installed"
    else
        log "Installing ${DEP}..."
        _venv_pip_install "${DEP}"
    fi
done

ok "Python dependencies complete"

# ── STEP 9: Model Download ────────────────────────────────────────────────────
_step 9 "Model Download"
if [[ ${SKIP_MODELS} -eq 1 ]]; then
    skip "Model download skipped (--skip-models)"
else
    if [[ -x "${VOICEOS_HOME}/venv/bin/python" ]]; then
        log "Running model downloader..."
        sudo -u "${DEPLOY_USER}" "${VENV_PY}" \
            "${REPO_ROOT}/deployment/gpu/download_models.py" \
            2>&1 | tail -20 || log "WARNING: download_models.py exited non-zero — check above output"
    else
        fail "venv python not found — run step 6 first"
    fi
fi

# ── STEP 10: Service Configuration and Start ──────────────────────────────────
_step 10 "Service Configuration and Start"

# Deploy unit files
for SVC in voiceos-llm voiceos-stt voiceos-tts; do
    SRC="${REPO_ROOT}/deployment/gpu/systemd/${SVC}.service"
    DST="/etc/systemd/system/${SVC}.service"
    if [[ -f "${SRC}" ]]; then
        if [[ -f "${DST}" ]] && diff -q "${SRC}" "${DST}" &>/dev/null; then
            skip "${SVC}.service unit file already matches repo — no reinstall"
        else
            cp "${SRC}" "${DST}"
            ok "Deployed ${SVC}.service"
        fi
    else
        log "WARNING: ${SRC} not found — skipping unit file deploy"
    fi
done

systemctl daemon-reload
ok "systemctl daemon-reload"

for SVC in voiceos-llm voiceos-stt voiceos-tts; do
    systemctl enable "${SVC}" 2>/dev/null || true
    ok "${SVC} enabled"
done

# Check .env before starting
if [[ ! -f "${VOICEOS_HOME}/.env" ]]; then
    log "WARNING: ${VOICEOS_HOME}/.env not found"
    log "Services need environment variables. Copy .env from the secure store."
    log "Skipping service start — provision .env and run: systemctl start voiceos-llm voiceos-stt voiceos-tts"
else
    # Start LLM first; wait for health; then start STT and TTS
    log "Starting voiceos-llm..."
    systemctl start voiceos-llm || log "WARNING: voiceos-llm start returned non-zero"

    log "Waiting for LLM /health (up to 300s)..."
    for i in $(seq 1 60); do
        if curl -sf "http://localhost:8000/health" &>/dev/null; then
            ok "LLM /health ready (${i}×5s = $((i*5))s elapsed)"
            break
        fi
        sleep 5
        if [[ ${i} -eq 60 ]]; then
            fail "LLM did not become healthy after 300s — check: journalctl -u voiceos-llm -n 50"
        fi
    done

    log "Starting voiceos-stt and voiceos-tts..."
    systemctl start voiceos-stt voiceos-tts || log "WARNING: service start returned non-zero"
    sleep 5
    ok "STT and TTS start commands issued"
fi

# ── Final validation ──────────────────────────────────────────────────────────
echo
echo "============================================================"
log "Deployment complete — running basic health check..."
echo "============================================================"
bash "${REPO_ROOT}/deployment/gpu/healthcheck.sh" 2>&1 | tee -a "${REPORT_FILE}" || true

echo
echo "============================================================"
log "NEXT STEP: Run the full validation suite:"
log "  python scripts/gpu_validation_suite.py"
log "  python scripts/gpu_acceptance_gates.py"
log "  python scripts/gpu_deployment_report.py"
log "Deployment report appended to: ${REPORT_FILE}"
echo "============================================================"
