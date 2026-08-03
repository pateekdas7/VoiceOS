# GPU Deployment Manifest — VoiceOS v2

**Document type:** Canonical deployment procedure — mandatory  
**Version:** 1.0.0  
**Established:** 2026-08-03  
**References:**  
- [GPU Runtime Specification](GPU_RUNTIME_SPEC.md) — what to deploy  
- [GPU Compatibility Matrix](GPU_COMPATIBILITY_MATRIX.md) — what combinations work  
- [GPU Deployment Checklist](GPU_DEPLOYMENT_CHECKLIST.md) — per-deployment sign-off form  
- Validation suite: `scripts/gpu_validation_suite.py`

> **Mandatory rule:** No GPU server may be deployed, rebuilt, or materially reconfigured without completing every step of this manifest in order. Do not skip steps. Do not continue past a failure. Every deployment produces a signed checklist.

---

## The Deployment Axioms

These rules govern every GPU deployment without exception:

1. **Audit first.** Before installing anything, fully audit the current server state.
2. **Compare against spec.** Every discovered component is compared against `GPU_RUNTIME_SPEC.md`.
3. **Install only what is missing or incompatible.** Never reinstall a component that already meets the spec.
4. **Never re-download existing models.** If a model directory exists and is the correct size, do not re-download.
5. **Never restart a healthy service without reason.** Verification is passive (HTTP health checks) — not disruptive.
6. **Run the full validation suite.** All validations must pass before any service is considered deployed.
7. **No deployment without a PASS report.** If any validation fails, stop — do not proceed.
8. **Document every deviation.** If the deployed state differs from the spec, it must be recorded.

---

## Step 1 — Audit the GPU Server

Run the audit script or execute each command manually. The goal is a complete picture of what is already installed.

```bash
# === 1.1 Operating System ===
lsb_release -a
uname -r

# === 1.2 NVIDIA Driver ===
nvidia-smi
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

# === 1.3 CUDA ===
cat /usr/local/cuda/version.json 2>/dev/null || echo "No CUDA version.json"
ls /usr/local/ | grep cuda
nvcc --version 2>/dev/null || echo "nvcc not in PATH"

# === 1.4 NVIDIA Container Toolkit ===
nvidia-ctk --version 2>/dev/null || echo "nvidia-ctk not installed"
cat /etc/docker/daemon.json 2>/dev/null || echo "No daemon.json"

# === 1.5 Docker ===
docker --version 2>/dev/null || echo "Docker not installed"
systemctl is-active docker 2>/dev/null || echo "Docker not running"

# === 1.6 Python ===
python3.12 --version 2>/dev/null || echo "Python 3.12 not installed"
ls /opt/voiceos-gpu/venv/bin/python 2>/dev/null || echo "venv not created"

# === 1.7 Key Python Packages ===
if [ -f /opt/voiceos-gpu/venv/bin/python ]; then
  /opt/voiceos-gpu/venv/bin/python -c "
import importlib, sys
pkgs = ['torch', 'vllm', 'faster_whisper', 'fastapi', 'uvicorn', 'huggingface_hub']
for p in pkgs:
    try:
        m = importlib.import_module(p)
        print(f'  {p}: {getattr(m, \"__version__\", \"unknown\")}')
    except ImportError:
        print(f'  {p}: NOT INSTALLED')
"
fi

# === 1.8 Models ===
echo "--- Model directories ---"
du -sh /opt/voiceos-gpu/models/* 2>/dev/null || echo "/opt/voiceos-gpu/models/ missing"

# === 1.9 Services ===
for svc in voiceos-llm voiceos-stt voiceos-tts; do
  echo "  ${svc}: $(systemctl is-active ${svc} 2>/dev/null || echo 'not installed')"
done

# === 1.10 Ports ===
for port in 8000 8100 8200; do
  echo "  :${port}: $(ss -tlnp | grep ":${port}" | awk '{print $4, $6}' || echo 'not bound')"
done

# === 1.11 VRAM Usage ===
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
```

**Output:** Record every result. Compare against `GPU_RUNTIME_SPEC.md`.

---

## Step 2 — Compare Against the Runtime Specification

For each component discovered in Step 1, compare against the spec:

| Component | Spec Version/Value | Audited Value | Match? | Action Required |
|---|---|---|---|---|
| OS | Ubuntu 22.04 LTS | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| Kernel | ≥ 5.15.0 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| NVIDIA Driver | 580.126.20 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| CUDA | 13.0 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| NVIDIA Container Toolkit | 1.19.0 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| Docker | 29.3.1 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| Python | 3.12.13 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| venv at `/opt/voiceos-gpu/venv` | Exists | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| torch | 2.11.0+cu130 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| vllm | 0.24.0 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| faster-whisper | 1.2.1 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| transformers | 5.12.1 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| fastapi | 0.136.3 | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| Whisper model | ~1,547 MB at `/opt/voiceos-gpu/models/whisper-large-v3-turbo/` | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| Qwen model | ~8,306 MB at `/opt/voiceos-gpu/models/qwen2.5-7b-fp8/` | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| Veena model | Exists at `/opt/voiceos-gpu/models/veena-fp16/` | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| SNAC model | Exists at `/opt/voiceos-gpu/models/snac-24khz/` | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| voiceos-llm.service | active | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| voiceos-stt.service | active | _(fill in)_ | _(Y/N)_ | _(fill in)_ |
| voiceos-tts.service | active | _(fill in)_ | _(Y/N)_ | _(fill in)_ |

---

## Step 3 — Identify Missing or Incompatible Components

From the comparison table in Step 2, compile the list of components that need action:

- **Missing:** Component is absent and must be installed.
- **Incompatible:** Component is present but at the wrong version — must be remediated.
- **Compliant:** Component matches spec — no action required.

**Decision rule:**
- If a component is **Compliant** — skip its installation step.
- If a component is **Missing** — install it.
- If a component is **Incompatible** — determine the remediation (upgrade, downgrade, or reinstall) and document it in the deployment log.

---

## Step 4 — Install Only What Is Missing

Run only the installation steps for components identified as Missing or Incompatible in Step 3.  
**Do not run installation steps for components that are already Compliant.**

### 4.1 System Packages (if missing)
```bash
apt-get update -qq
apt-get install -y \
  curl wget git unzip jq \
  build-essential pkg-config \
  ca-certificates gnupg lsb-release \
  net-tools htop iotop \
  software-properties-common pciutils
```

### 4.2 NVIDIA Driver (if missing — usually pre-installed on GPU VMs)
```bash
apt-get install -y ubuntu-drivers-common
ubuntu-drivers autoinstall
# ⚠️ REBOOT REQUIRED. Re-run this manifest after reboot.
```

### 4.3 CUDA Toolkit (if missing)
```bash
# CUDA 13.0 is the spec version. If the VM ships with CUDA 12.x, that is also acceptable.
# Check: ls /usr/local/cuda*
# Only install if entirely absent:
wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
apt-get update -qq
apt-get install -y cuda-toolkit-12-1   # minimum compatible version
```

### 4.4 NVIDIA Container Toolkit (if missing)
```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > \
  /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update -qq && apt-get install -y nvidia-container-toolkit
```

### 4.5 Docker (if missing)
```bash
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker
```

### 4.6 Docker NVIDIA Runtime (if missing or misconfigured)
```bash
cat > /etc/docker/daemon.json <<'EOF'
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": { "args": [], "path": "nvidia-container-runtime" }
  }
}
EOF
systemctl restart docker
```

### 4.7 Python 3.12 (if missing)
```bash
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -qq
apt-get install -y python3.12 python3.12-venv python3.12-dev
```

### 4.8 Virtual Environment (if missing)
```bash
DEPLOY_USER="${SUDO_USER:-ubuntu}"
mkdir -p /opt/voiceos-gpu/{venv,models/whisper-large-v3-turbo,models/qwen2.5-7b-fp8,models/veena-fp16,models/snac-24khz,cache/whisper,cache/qwen,cache/veena,logs,services/stt,services/llm,services/tts,deployment}
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" /opt/voiceos-gpu
sudo -u "${DEPLOY_USER}" python3.12 -m venv /opt/voiceos-gpu/venv
sudo -u "${DEPLOY_USER}" /opt/voiceos-gpu/venv/bin/pip install --quiet --upgrade pip wheel setuptools
```

### 4.9 PyTorch (if missing or wrong version)
```bash
/opt/voiceos-gpu/venv/bin/pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0
```

### 4.10 vLLM (if missing or wrong version)
```bash
/opt/voiceos-gpu/venv/bin/pip install vllm==0.24.0
```

### 4.11 Remaining Python Dependencies (if missing)
```bash
/opt/voiceos-gpu/venv/bin/pip install \
  faster-whisper==1.2.1 \
  grpcio==1.81.1 grpcio-tools==1.81.1 \
  fastapi==0.136.3 uvicorn==0.49.0 \
  prometheus-client==0.25.0 \
  opentelemetry-sdk==1.43.0 opentelemetry-exporter-otlp==1.43.0 \
  huggingface_hub==1.21.0
```

### 4.12 Download Models (only if model directories are empty or absent)

**CHECK FIRST — do not download if the model already exists:**
```bash
du -sh /opt/voiceos-gpu/models/whisper-large-v3-turbo/
# Expected: ~1.5G   (if correct, skip Whisper download)

du -sh /opt/voiceos-gpu/models/qwen2.5-7b-fp8/
# Expected: ~8.2G   (if correct, skip Qwen download)

du -sh /opt/voiceos-gpu/models/veena-fp16/
# Expected: exists and non-empty (if correct, skip Veena download)

du -sh /opt/voiceos-gpu/models/snac-24khz/
# Expected: exists and non-empty (if correct, skip SNAC download)
```

**Download Whisper (only if absent):**
```bash
/opt/voiceos-gpu/venv/bin/python deployment/gpu/download_models.py --model whisper
```

**Download Qwen2.5-7B FP8 (only if absent):**
```bash
/opt/voiceos-gpu/venv/bin/python deployment/gpu/download_models.py --model qwen
```

**Download Veena + SNAC (only if absent):**
```bash
/opt/voiceos-gpu/venv/bin/python deployment/gpu/download_models.py --model tts
```

### 4.13 Deploy Service Files (if missing or updated)
```bash
# Copy inference server scripts
cp deployment/gpu/services/stt/server.py /opt/voiceos-gpu/services/stt/server.py
cp deployment/gpu/services/tts/server.py /opt/voiceos-gpu/services/tts/server.py

# Install systemd units
sudo cp deployment/gpu/systemd/voiceos-llm.service /etc/systemd/system/
sudo cp deployment/gpu/systemd/voiceos-stt.service /etc/systemd/system/
sudo cp deployment/gpu/systemd/voiceos-tts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable voiceos-llm voiceos-stt voiceos-tts
```

### 4.14 Configure Environment (if `.env` is absent)
```bash
# Provision from the secrets store (Vault). Never hardcode credentials.
# Template: deployment/gpu/.env.example
cp deployment/gpu/.env.example /opt/voiceos-gpu/.env
# Then fill in actual values from Vault
```

---

## Step 5 — Never Reinstall Compatible Components

Before executing any installation step in Step 4, confirm the component is actually missing or incompatible. This prevents:

- Re-downloading 8+ GB of model weights that are already present
- Reinstalling PyTorch and vLLM (which takes 15–20 minutes)
- Restarting healthy inference services (which disrupts live calls)
- Overwriting a functioning `.env` with the template

**The audit in Step 1 is the guard. If the audit shows "Compliant", skip.**

---

## Step 6 — Never Re-Download Existing Models

Model downloads are expensive (8+ GB for Qwen, network time, disk I/O). Use these checks before any download:

```bash
# Whisper: should be ~1,547 MB
WHISPER_SIZE=$(du -sm /opt/voiceos-gpu/models/whisper-large-v3-turbo/ 2>/dev/null | cut -f1)
[ "${WHISPER_SIZE:-0}" -gt 1400 ] && echo "Whisper: SKIP DOWNLOAD" || echo "Whisper: DOWNLOAD NEEDED"

# Qwen: should be ~8,306 MB (2 shards)
QWEN_SIZE=$(du -sm /opt/voiceos-gpu/models/qwen2.5-7b-fp8/ 2>/dev/null | cut -f1)
[ "${QWEN_SIZE:-0}" -gt 7500 ] && echo "Qwen: SKIP DOWNLOAD" || echo "Qwen: DOWNLOAD NEEDED"

# Veena: directory must exist and contain files
VEENA_FILES=$(ls /opt/voiceos-gpu/models/veena-fp16/ 2>/dev/null | wc -l)
[ "${VEENA_FILES:-0}" -gt 0 ] && echo "Veena: SKIP DOWNLOAD" || echo "Veena: DOWNLOAD NEEDED"

# SNAC: directory must exist and contain files
SNAC_FILES=$(ls /opt/voiceos-gpu/models/snac-24khz/ 2>/dev/null | wc -l)
[ "${SNAC_FILES:-0}" -gt 0 ] && echo "SNAC: SKIP DOWNLOAD" || echo "SNAC: DOWNLOAD NEEDED"
```

---

## Step 7 — Configure Services

After all components are installed:

1. **Verify the `.env` file** is populated with correct values (not the template defaults).
2. **Verify systemd units** are installed, enabled, and the unit files match the repo source:
   ```bash
   diff /etc/systemd/system/voiceos-llm.service deployment/gpu/systemd/voiceos-llm.service
   diff /etc/systemd/system/voiceos-stt.service deployment/gpu/systemd/voiceos-stt.service
   diff /etc/systemd/system/voiceos-tts.service deployment/gpu/systemd/voiceos-tts.service
   # All diffs should produce no output
   ```
3. **Reload and enable** if units changed:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable voiceos-llm voiceos-stt voiceos-tts
   ```
4. **Start services in order** if not already running:
   ```bash
   # Start LLM first — wait for it before starting STT and TTS
   sudo systemctl start voiceos-llm
   
   # Wait for vLLM to be ready (can take up to 90s)
   echo "Waiting for vLLM to be ready..."
   for i in $(seq 1 30); do
     curl -sf http://localhost:8000/health && echo "vLLM ready after ${i}x5s" && break
     sleep 5
   done
   
   # Start STT and TTS
   sudo systemctl start voiceos-stt voiceos-tts
   sleep 30   # Allow STT (~7s) and TTS (~28s) to initialize
   ```

---

## Step 8 — Perform Validation

Run the full validation suite. This is not optional.

```bash
# From the VoiceOS repository root (on the GPU node or via SSH):
/opt/voiceos-gpu/venv/bin/python scripts/gpu_validation_suite.py

# The suite prints a PASS/FAIL report for every check.
# All checks must PASS before proceeding to Step 9.
```

The validation suite checks (in order):
1. NVIDIA driver presence and version
2. CUDA availability via PyTorch
3. Python version
4. All pinned dependency versions
5. Model directory existence and minimum size
6. Model loading (GPU memory allocation)
7. VRAM budget (all 3 services within spec)
8. STT service startup and health
9. LLM service startup and health
10. TTS service startup and health
11. Port availability (8000, 8100, 8200 bound and responding)
12. End-to-end STT inference (synthetic audio → transcript)
13. End-to-end LLM inference (prompt → completion)
14. End-to-end TTS inference (text → audio chunks, verify streaming)
15. TTS streaming (verify `Transfer-Encoding: chunked`)
16. VRAM after all services loaded (must be ≤ 23,034 MiB)

---

## Step 9 — Only Deploy After Validation Passes

**A deployment is only complete when the validation suite reports ALL PASS.**

If any validation fails:

1. Stop immediately. Do not mark the deployment complete.
2. Read the FAIL message — it identifies exactly what failed.
3. Diagnose the root cause.
4. Remediate (re-run the relevant installation step).
5. Re-run the full validation suite from the beginning.
6. Repeat until all validations pass.

**The PASS report must be saved.** Save the output of `scripts/gpu_validation_suite.py` as:
```
deployment/gpu/validation_reports/VALIDATION_REPORT_{DATE}.txt
```

This report is the evidence that the deployment met the spec.

---

## Deployment Gate Summary

```
┌─────────────────────────────────────────────────────────────┐
│                    DEPLOYMENT GATE                          │
│                                                             │
│  IF all 16 validations PASS → DEPLOYMENT APPROVED          │
│                                                             │
│  IF any validation FAILS   → DEPLOYMENT BLOCKED           │
│                              Fix the failure.              │
│                              Re-run full suite.            │
│                              Do not proceed.               │
└─────────────────────────────────────────────────────────────┘
```

---

## Emergency Procedures

### Full Node Rebuild from Scratch

If the GPU node is unrecoverable and must be rebuilt from a fresh Ubuntu 22.04 VM:

```bash
# 1. Run the bootstrap script
chmod +x deployment/gpu/bootstrap.sh
sudo ./deployment/gpu/bootstrap.sh

# 2. Provision .env from secrets store
# (Get from Vault — never hardcode)

# 3. Run restore script (downloads models if absent, starts services)
chmod +x deployment/gpu/restore.sh
./deployment/gpu/restore.sh

# 4. Run validation suite
/opt/voiceos-gpu/venv/bin/python scripts/gpu_validation_suite.py

# 5. All validations must PASS before announcing the node ready
```

### Service Restart (without full rebuild)

If a specific service is unhealthy and must be restarted:

```bash
# Check current health first
curl -sf http://localhost:8100/health/ready && echo "STT healthy" || echo "STT unhealthy"
curl -sf http://localhost:8000/health && echo "LLM healthy" || echo "LLM unhealthy"
curl -sf http://localhost:8200/health/ready && echo "TTS healthy" || echo "TTS unhealthy"

# Restart only the unhealthy service
sudo systemctl restart voiceos-stt    # STT only
sudo systemctl restart voiceos-tts    # TTS only
# For LLM restart: STT and TTS must stop first (they depend on VRAM allocation)
sudo systemctl stop voiceos-stt voiceos-tts
sudo systemctl restart voiceos-llm
sleep 90
sudo systemctl start voiceos-stt voiceos-tts

# Re-verify health after restart
bash deployment/gpu/healthcheck.sh
```

---

## Document History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-08-03 | Claude Code | Initial creation — canonical deployment procedure based on Sprint-009 through Sprint-027 operational experience |
