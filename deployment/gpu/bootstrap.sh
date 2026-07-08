#!/usr/bin/env bash
# ==============================================================================
# VoiceOS GPU Node Bootstrap Script
# Installs all GPU dependencies and prepares the GPU node from scratch.
# Run on a fresh Ubuntu 22.04 server with an NVIDIA GPU.
#
# Validated on: NVIDIA L4 / Driver 580.126.20 / CUDA 13.0
#
# Usage:
#   chmod +x bootstrap.sh
#   sudo ./bootstrap.sh
#
# After bootstrap:
#   1. Copy .env to /opt/voiceos-gpu/.env (from secure store)
#   2. Run: ./restore.sh  (downloads models in Sprint-009+)
# ==============================================================================

set -euo pipefail

VOICEOS_GPU_HOME="/opt/voiceos-gpu"
PYTHON_VERSION="3.12"

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAIL — $*"; exit 1; }

log "=== VoiceOS GPU Node Bootstrap ==="

# ── 1. System packages ───────────────────────────────────────────────────────
log "Installing system packages..."
apt-get update -qq
apt-get install -y \
  curl wget git unzip jq \
  build-essential pkg-config \
  ca-certificates gnupg lsb-release \
  net-tools htop iotop \
  software-properties-common \
  pciutils

# ── 2. NVIDIA Driver (pre-installed on most GPU cloud VMs) ───────────────────
log "Checking NVIDIA driver..."
if ! nvidia-smi &>/dev/null; then
  log "NVIDIA driver not found. Installing..."
  apt-get install -y ubuntu-drivers-common
  ubuntu-drivers autoinstall
  log "REBOOT REQUIRED after driver install. Re-run this script after reboot."
  exit 0
else
  log "NVIDIA driver: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader)"
fi

# ── 3. CUDA Toolkit (pre-installed on most GPU VMs; install if missing) ──────
log "Checking CUDA toolkit..."
if ! ls /usr/local/cuda/bin/nvcc &>/dev/null; then
  log "CUDA toolkit not found. Installing CUDA 12.1..."
  wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
  dpkg -i cuda-keyring_1.1-1_all.deb
  apt-get update -qq
  apt-get install -y cuda-toolkit-12-1
else
  CUDA_VER=$(cat /usr/local/cuda/version.json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['cuda']['version'])" 2>/dev/null || echo "unknown")
  log "CUDA toolkit already installed: ${CUDA_VER}"
fi

# ── 4. Python 3.12 ──────────────────────────────────────────────────────────
log "Installing Python ${PYTHON_VERSION}..."
if ! python${PYTHON_VERSION} --version &>/dev/null; then
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update -qq
  apt-get install -y python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev
else
  log "Python ${PYTHON_VERSION} already installed: $(python${PYTHON_VERSION} --version)"
fi

# ── 5. Docker + NVIDIA Container Toolkit ────────────────────────────────────
log "Installing Docker..."
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker && systemctl start docker
else
  log "Docker already installed: $(docker --version)"
fi

log "Installing NVIDIA Container Toolkit..."
if ! command -v nvidia-ctk &>/dev/null; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > \
    /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -qq && apt-get install -y nvidia-container-toolkit
else
  log "NVIDIA Container Toolkit already installed: $(nvidia-ctk --version | head -1)"
fi

# Configure Docker default runtime to nvidia
cat > /etc/docker/daemon.json <<'EOF'
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": {
      "args": [],
      "path": "nvidia-container-runtime"
    }
  }
}
EOF
systemctl restart docker
sleep 2
docker info 2>/dev/null | grep -i 'default runtime' | head -1 || log "Check daemon.json — runtime may not have applied"

# ── 6. Application directory structure ────────────────────────────────────────
log "Creating GPU application directories..."
DEPLOY_USER="${SUDO_USER:-ubuntu}"
mkdir -p "${VOICEOS_GPU_HOME}"/{logs,deployment}
mkdir -p "${VOICEOS_GPU_HOME}/venv"
mkdir -p "${VOICEOS_GPU_HOME}/models/whisper-large-v3-turbo"
mkdir -p "${VOICEOS_GPU_HOME}/models/qwen2.5-7b-fp8"
mkdir -p "${VOICEOS_GPU_HOME}/models/veena-fp16"
mkdir -p "${VOICEOS_GPU_HOME}/cache/whisper"
mkdir -p "${VOICEOS_GPU_HOME}/cache/qwen"
mkdir -p "${VOICEOS_GPU_HOME}/cache/veena"
mkdir -p "${VOICEOS_GPU_HOME}/services/stt"
mkdir -p "${VOICEOS_GPU_HOME}/services/llm"
mkdir -p "${VOICEOS_GPU_HOME}/services/tts"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${VOICEOS_GPU_HOME}"

# ── 7. Python virtual environment ────────────────────────────────────────────
log "Creating GPU Python virtual environment (as ${DEPLOY_USER})..."
sudo -u "${DEPLOY_USER}" python${PYTHON_VERSION} -m venv "${VOICEOS_GPU_HOME}/venv"
sudo -u "${DEPLOY_USER}" "${VOICEOS_GPU_HOME}/venv/bin/pip" install --quiet --upgrade pip wheel setuptools

# ── 8. PyTorch (CUDA-compatible) ─────────────────────────────────────────────
log "Installing PyTorch..."
# Note: GPU node may have CUDA 12.x or 13.x. The 'torch' PyPI default wheel
# includes CUDA support and auto-selects the compatible version.
# If a specific CUDA build is needed: --index-url https://download.pytorch.org/whl/cu121
sudo -u "${DEPLOY_USER}" "${VOICEOS_GPU_HOME}/venv/bin/pip" install --quiet \
  torch torchvision torchaudio
log "PyTorch installed: $("${VOICEOS_GPU_HOME}/venv/bin/python" -c 'import torch; print(torch.__version__, "| CUDA:", torch.cuda.is_available())')"

# ── 9. vLLM ──────────────────────────────────────────────────────────────────
log "Installing vLLM (this may take a few minutes)..."
sudo -u "${DEPLOY_USER}" "${VOICEOS_GPU_HOME}/venv/bin/pip" install --quiet vllm
log "vLLM installed: $("${VOICEOS_GPU_HOME}/venv/bin/python" -c 'import vllm; print(vllm.__version__)')"

# ── 10. Additional Python dependencies ────────────────────────────────────────
log "Installing additional Python dependencies..."
sudo -u "${DEPLOY_USER}" "${VOICEOS_GPU_HOME}/venv/bin/pip" install --quiet \
  faster-whisper \
  grpcio grpcio-tools \
  fastapi uvicorn \
  prometheus-client \
  opentelemetry-sdk opentelemetry-exporter-otlp \
  huggingface_hub

log ""
log "=== GPU Bootstrap complete ==="
log "Installed:"
log "  Python ${PYTHON_VERSION}: $("python${PYTHON_VERSION}" --version)"
log "  PyTorch: $("${VOICEOS_GPU_HOME}/venv/bin/python" -c 'import torch; print(torch.__version__)')"
log "  vLLM: $("${VOICEOS_GPU_HOME}/venv/bin/python" -c 'import vllm; print(vllm.__version__)')"
log "  CUDA available: $("${VOICEOS_GPU_HOME}/venv/bin/python" -c 'import torch; print(torch.cuda.is_available())')"
log ""
log "Next steps:"
log "  1. Copy .env to ${VOICEOS_GPU_HOME}/.env (from secure store)"
log "  2. Sprint-009+: Run ./restore.sh to download models and start services"
