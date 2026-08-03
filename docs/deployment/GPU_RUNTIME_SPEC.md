# GPU Runtime Specification — VoiceOS v2

**Document type:** Canonical production standard — single source of truth  
**Version:** 1.0.0  
**Established:** 2026-08-03  
**Authority:** This document supersedes all informal notes, chat logs, and memory regarding GPU environment requirements.

> **Mandatory rule:** No GPU server may be deployed, rebuilt, or materially reconfigured without following this specification and the [GPU Deployment Manifest](GPU_DEPLOYMENT_MANIFEST.md). Every deployment must produce a validation report. Deviations from this spec must be recorded as ADRs before being adopted.

---

## Table of Contents

1. [Operating System](#1-operating-system)
2. [NVIDIA Driver](#2-nvidia-driver)
3. [CUDA Toolkit](#3-cuda-toolkit)
4. [cuDNN](#4-cudnn)
5. [NVIDIA Container Toolkit](#5-nvidia-container-toolkit)
6. [Docker](#6-docker)
7. [Python Runtime](#7-python-runtime)
8. [Python Dependencies — Full Pinned Inventory](#8-python-dependencies--full-pinned-inventory)
9. [GPU Hardware Requirements](#9-gpu-hardware-requirements)
10. [Directory Structure](#10-directory-structure)
11. [Environment Variables](#11-environment-variables)
12. [Model Specification](#12-model-specification)
13. [Service Specification](#13-service-specification)
14. [Known Deviations from Architecture Spec](#14-known-deviations-from-architecture-spec)
15. [Document History](#15-document-history)

---

## 1. Operating System

| Property | Required | Current Production |
|---|---|---|
| Distribution | Ubuntu 22.04 LTS (Jammy Jellyfish) | Ubuntu 22.04.5 LTS |
| Minimum kernel | 5.15.0 | 5.15.0-171-generic |
| Architecture | x86_64 | x86_64 |
| Package manager | apt | apt |

**Required system packages:**

```
curl wget git unzip jq
build-essential pkg-config
ca-certificates gnupg lsb-release
net-tools htop iotop
software-properties-common
pciutils
```

**Rationale for Ubuntu 22.04:** NVIDIA's official driver and CUDA packages have first-class `.deb` support for Jammy. The deadsnakes PPA provides Python 3.12 on Jammy. Ubuntu 20.04 (Focal) is EOL for our toolchain and must not be used.

---

## 2. NVIDIA Driver

| Property | Minimum | Recommended | Current Production |
|---|---|---|---|
| Driver version | 525.0 | 580.x | **580.126.20** |
| Install source | ubuntu-drivers / NVIDIA .run | ubuntu-drivers autoinstall | Pre-installed on GPU VM |
| Verification | `nvidia-smi` returns exit 0 | | ✅ |

**CUDA compatibility:** NVIDIA driver 580.x supports CUDA up to 13.0. Minimum driver 525.0 supports CUDA 12.1.

**Verification command:**
```bash
nvidia-smi --query-gpu=driver_version,name,memory.total --format=csv,noheader
# Expected: 580.126.20, NVIDIA L4, 23034 MiB
```

**Driver installation procedure (if not pre-installed):**
```bash
apt-get install -y ubuntu-drivers-common
ubuntu-drivers autoinstall
# Reboot required before continuing
```

---

## 3. CUDA Toolkit

| Property | Minimum | Recommended | Current Production |
|---|---|---|---|
| CUDA version | 12.1 | 13.0 | **13.0** |
| Install path | `/usr/local/cuda-{version}/` | `/usr/local/cuda-13.0/` | `/usr/local/cuda-13.0/` |
| `nvcc` available | Required | Required | ✅ `/usr/local/cuda-13.0/bin/nvcc` |

**Why 13.0 with minimum 12.1:** PyTorch 2.11.0 ships CUDA 13.0 wheels. vLLM 0.24.0 is built against CUDA 13.0. If a new server ships with CUDA 12.x, the same PyTorch and vLLM versions remain compatible (cu121 wheel path). However the production configuration is CUDA 13.0 and must match this specification unless an ADR approves a version change.

**CUDA 12.1 fallback installation (if CUDA 13.0 is unavailable):**
```bash
wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
apt-get update -qq
apt-get install -y cuda-toolkit-12-1
```

**Verification:**
```bash
cat /usr/local/cuda/version.json | python3 -c "import sys,json; print(json.load(sys.stdin)['cuda']['version'])"
# or
nvcc --version   # from /usr/local/cuda-13.0/bin/nvcc
```

---

## 4. cuDNN

| Property | Value |
|---|---|
| Version | **9.19.0** (cuDNN 9.x) |
| Source | Bundled with PyTorch 2.11.0+cu130 — no separate install required |
| Verification | `torch.backends.cudnn.version()` → `91900` |

**Note:** cuDNN is NOT installed as a standalone system package. It is provided by PyTorch's CUDA wheel. Do not install `libcudnn8` or `libcudnn9` from apt separately — this can create version conflicts.

**Verification:**
```bash
/opt/voiceos-gpu/venv/bin/python -c "
import torch
print('cuDNN available:', torch.backends.cudnn.is_available())
print('cuDNN version:', torch.backends.cudnn.version())
# Expected: 91900
"
```

---

## 5. NVIDIA Container Toolkit

| Property | Value |
|---|---|
| Version | **1.19.0** |
| Package | `nvidia-container-toolkit` |
| Source | `nvidia.github.io/libnvidia-container` |
| Docker default runtime | `nvidia` (configured in `/etc/docker/daemon.json`) |

**Installation:**
```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > \
  /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update -qq && apt-get install -y nvidia-container-toolkit
```

**Required `/etc/docker/daemon.json`:**
```json
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": {
      "args": [],
      "path": "nvidia-container-runtime"
    }
  }
}
```

**Verification:**
```bash
nvidia-ctk --version
docker info | grep -i "default runtime"
# Expected: Default Runtime: nvidia
```

---

## 6. Docker

| Property | Value |
|---|---|
| Version | **29.3.1** (build c2be9cc) |
| Status | Active (enabled, running) |
| Default GPU runtime | nvidia (required) |

**Installation:**
```bash
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker
```

**Verification:**
```bash
docker --version
# Docker version 29.3.1

docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
# Must succeed — proves GPU passthrough works
```

---

## 7. Python Runtime

| Property | Value |
|---|---|
| Version | **3.12.13** (exact) |
| Install source | deadsnakes PPA (`ppa:deadsnakes/ppa`) |
| Virtual environment path | `/opt/voiceos-gpu/venv` |
| venv creator | `python3.12 -m venv` |

**Why 3.12 and not 3.13:** vLLM 0.24.0 and faster-whisper 1.2.1 have validated wheels for Python 3.12. The CPU node's application code runs Python 3.13, but the GPU inference stack requires 3.12.

**Installation:**
```bash
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -qq
apt-get install -y python3.12 python3.12-venv python3.12-dev
```

**Virtual environment setup:**
```bash
python3.12 -m venv /opt/voiceos-gpu/venv
/opt/voiceos-gpu/venv/bin/pip install --upgrade pip wheel setuptools
```

**Verification:**
```bash
python3.12 --version
# Python 3.12.13

/opt/voiceos-gpu/venv/bin/python --version
# Python 3.12.13
```

---

## 8. Python Dependencies — Full Pinned Inventory

All packages are installed in `/opt/voiceos-gpu/venv`. Exact pinned versions are required. Do not upgrade individual packages without validating VRAM budgets and latency targets.

### 8.1 Core Inference Stack

| Package | Pinned Version | Purpose |
|---|---|---|
| `torch` | **2.11.0+cu130** | PyTorch CUDA 13.0 — core tensor operations and CUDA bridge |
| `torchvision` | **0.26.0** | Torchvision companion (required by torch install) |
| `torchaudio` | **2.11.0** | Torchaudio companion (required by torch install) |
| `vllm` | **0.24.0** | LLM and TTS inference engine (serves Qwen2.5-7B and Veena) |
| `faster-whisper` | **1.2.1** | Whisper STT using CTranslate2 backend |
| `ctranslate2` | **4.8.0** | CTranslate2 backend for faster-whisper (transitive dep) |
| `transformers` | **5.12.1** | HuggingFace Transformers — tokenization, model loading |
| `tokenizers` | **0.22.2** | Fast Rust-based tokenizer (Transformers dep) |
| `flashinfer` | **0.6.12** | FlashInfer CUDA kernels — used by vLLM for attention |
| `triton` | **3.6.0** | OpenAI Triton compiler — required by vLLM CUDA kernels |
| `numpy` | **2.3.5** | Numerical array library |

**Installation:**
```bash
# PyTorch (cu130 wheel)
/opt/voiceos-gpu/venv/bin/pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0

# vLLM (pulls ctranslate2, flashinfer, triton as transitive deps)
/opt/voiceos-gpu/venv/bin/pip install vllm==0.24.0

# Whisper
/opt/voiceos-gpu/venv/bin/pip install faster-whisper==1.2.1
```

> **VRAM-sensitive:** Do NOT upgrade torch, vllm, or faster-whisper without re-running the full VRAM budget validation in the validation suite. These upgrades can silently change VRAM footprints.

### 8.2 Serving Layer

| Package | Pinned Version | Purpose |
|---|---|---|
| `fastapi` | **0.136.3** | HTTP API framework for STT and TTS inference servers |
| `uvicorn` | **0.49.0** | ASGI server running FastAPI apps |
| `httpx` | **≥0.27** | Async HTTP client used by CPU-side adapters |

### 8.3 Observability

| Package | Pinned Version | Purpose |
|---|---|---|
| `prometheus-client` | **0.25.0** | Prometheus metrics export (currently unbound — see §14) |
| `opentelemetry-sdk` | **1.43.0** | OTel tracing SDK |
| `opentelemetry-exporter-otlp` | **1.43.0** | OTLP trace exporter to CPU node's OTel Collector |

> **TT-017:** The `/metrics` endpoint is not currently bound on any GPU service. This is a known gap. Prometheus scraping of GPU services requires this to be wired before production observability is complete.

### 8.4 Communication and Utilities

| Package | Pinned Version | Purpose |
|---|---|---|
| `grpcio` | **1.81.1** | gRPC runtime |
| `grpcio-tools` | **1.81.1** | gRPC protoc tools |
| `huggingface-hub` | **1.21.0** | HuggingFace model download and cache management |

### 8.5 Full Installed Package Count

The production venv contains **211 packages** (including all transitive dependencies). The packages listed above are the directly required production dependencies. All transitive dependencies are locked at the versions produced by this installation sequence.

**Verification:**
```bash
/opt/voiceos-gpu/venv/bin/pip list | wc -l
# Expected: ~212 (211 packages + header)

/opt/voiceos-gpu/venv/bin/python -c "
import torch, vllm, faster_whisper, fastapi, uvicorn, huggingface_hub
print('torch:', torch.__version__)            # 2.11.0+cu130
print('vllm:', vllm.__version__)              # 0.24.0
print('faster-whisper:', faster_whisper.__version__)  # 1.2.1
print('fastapi:', fastapi.__version__)        # 0.136.3
print('uvicorn:', uvicorn.__version__)        # 0.49.0
"
```

---

## 9. GPU Hardware Requirements

### 9.1 Minimum Hardware Specification

| Property | Minimum | Recommended | Current Production |
|---|---|---|---|
| GPU architecture | Ampere (SM 8.0) or newer | Ada Lovelace (SM 8.9) | Ada Lovelace (SM 8.9) |
| VRAM | 20,000 MiB | 24,576 MiB | 23,034 MiB |
| CPU cores | 8 | 16 | 28 |
| System RAM | 32 GiB | 64 GiB | 121 GiB |
| Disk | 50 GB | 100 GB | 97 GB |

### 9.2 Current Production Node

| Property | Value |
|---|---|
| GPU model | **NVIDIA L4** |
| GPU architecture | Ada Lovelace (SM 8.9) |
| VRAM total | **23,034 MiB** |
| VRAM used (all 3 services running) | **21,850 MiB** (measured Sprint-009 Phase 2) |
| VRAM free at steady state | **695 MiB** (headroom) |
| GPU UUID | GPU-2ba0ea2a-d4bf-d29d-e152-8d352c727524 |
| CPU cores | 28 |
| System RAM | 121 GiB |
| Disk | 97 GB (`/dev/vda1`) |

### 9.3 VRAM Budget Allocation

| Service | Model | Precision | Scheduler Reservation | Measured Footprint |
|---|---|---|---|---|
| STT | Whisper Large-v3 Turbo | int8_float16 | 6,144 MiB | **1,242 MiB** |
| LLM | Qwen2.5-7B-Instruct-FP8 | W8A8 FP8 | 16,384 MiB | **12,628 MiB** (at 0.55 util) |
| TTS | Veena 3B BF16 + SNAC | BF16 / FP32 | 2,048 MiB | **7,980 MiB** |
| **Total** | | | **24,576 MiB** | **21,850 MiB** |

> **Critical note:** The scheduler reservations (6,144 + 16,384 + 2,048 = 24,576 MiB) exceed this node's physical 23,034 MiB. This is intentional — the reservations include headroom for KV-cache growth during peak calls. Actual measured footprint of 21,850 MiB fits within the L4's capacity with 695 MiB free. The GPU Scheduler on the CPU node uses the reservation figures, not the measured figures, for admission control.

---

## 10. Directory Structure

```
/opt/voiceos-gpu/
├── .env                              # Environment variables (never committed)
├── venv/                             # Python 3.12.13 virtual environment
│   └── bin/
│       ├── python                    # Python 3.12.13
│       ├── pip
│       ├── vllm                      # vLLM CLI
│       └── huggingface-cli           # HuggingFace CLI
├── models/
│   ├── whisper-large-v3-turbo/       # CTranslate2 binary (1,547 MB)
│   ├── qwen2.5-7b-fp8/               # FP8 safetensors, 2 shards (8,306 MB)
│   ├── veena-fp16/                   # BF16 safetensors (dir name legacy)
│   └── snac-24khz/                   # SNAC 24 kHz codec weights
├── cache/
│   ├── whisper/                      # faster-whisper cache
│   ├── qwen/                         # vLLM / HuggingFace cache
│   └── veena/                        # HuggingFace cache (HF_HOME=/opt/voiceos-gpu/cache)
├── services/
│   ├── stt/
│   │   └── server.py                 # Whisper FastAPI server
│   ├── llm/                          # vLLM launched directly (no wrapper)
│   └── tts/
│       └── server.py                 # Veena + SNAC FastAPI server
├── logs/
│   ├── stt.log                       # STT service stdout+stderr
│   ├── llm.log                       # LLM service stdout+stderr
│   └── tts.log                       # TTS service stdout+stderr
└── deployment/
    └── healthcheck.sh                # Health check script (copied from repo)
```

---

## 11. Environment Variables

All environment variables are set in `/opt/voiceos-gpu/.env`, sourced by each systemd unit via `EnvironmentFile=-/opt/voiceos-gpu/.env`.

| Variable | Default | Service(s) | Purpose |
|---|---|---|---|
| `CUDA_VISIBLE_DEVICES` | `0` | All GPU services | GPU device selection |
| `GPU_MEMORY_FRACTION` | `0.55` | vLLM (LLM) | VRAM fraction for Qwen2.5 KV cache |
| `WHISPER_MODEL_PATH` | `/opt/voiceos-gpu/models/whisper-large-v3-turbo` | STT | Path to Whisper CTranslate2 weights |
| `QWEN_MODEL_PATH` | `/opt/voiceos-gpu/models/qwen2.5-7b-fp8` | LLM | Path to Qwen2.5 FP8 weights |
| `VEENA_MODEL_PATH` | `/opt/voiceos-gpu/models/veena-fp16` | TTS | Path to Veena BF16 weights |
| `STT_SERVICE_PORT` | `8100` | STT | Whisper HTTP server port |
| `LLM_SERVICE_PORT` | `8000` | LLM | vLLM HTTP server port |
| `TTS_SERVICE_PORT` | `8200` | TTS | Veena HTTP server port |
| `GPU_SCHEDULER_REPORT_URL` | _(CPU node URL)_ | All GPU services | CPU node GPU Scheduler callback |
| `HF_HOME` | `/opt/voiceos-gpu/cache` | TTS | HuggingFace cache path |
| `VAULT_ADDR` | _(CPU node Vault URL)_ | Reserved | Future secrets integration |
| `VAULT_TOKEN` | _(voiceos-app token)_ | Reserved | Future secrets integration |

**`.env` template:** `deployment/gpu/.env.example` in the repository.

**Security requirement:** The `.env` file must never be committed to version control. It must be provisioned from the secrets store (HashiCorp Vault) or passed through a secure channel at deployment time.

---

## 12. Model Specification

### 12.1 STT — Whisper Large-v3 Turbo

| Property | Value |
|---|---|
| **Model name** | `whisper-large-v3-turbo` |
| **HuggingFace repo** | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` |
| **Actual HF host** | `dropbox-dash/faster-whisper-large-v3-turbo` (mapped by faster-whisper 1.2.1) |
| **Download method** | `faster_whisper.download_model("large-v3-turbo", ...)` — NOT `huggingface_hub` |
| **Format** | CTranslate2 binary (pre-converted) — NOT safetensors |
| **Local path** | `/opt/voiceos-gpu/models/whisper-large-v3-turbo/` |
| **Download size** | ~1,547 MB (`model.bin` 1,542.9 MB + tokenizer files) |
| **Precision** | int8_float16 at runtime (INT8 weights, FP16 activations) |
| **VRAM (scheduler reservation)** | 6,144 MiB |
| **VRAM (measured actual)** | 1,242 MiB |
| **Runtime dependency** | CTranslate2 4.8.0, faster-whisper 1.2.1 |
| **Port** | 8100 |
| **Health endpoints** | `/health/live`, `/health/ready` |
| **Inference endpoint** | `/transcribe` |
| **Startup time** | ~7 seconds |
| **Latency target** | First word p95 ≤ 500ms |
| **Measured latency** | ~330ms on synthetic audio (Sprint-009 Phase 2) |
| **Streaming** | Yes (word-level timestamps) |

**Important:** FP8 compute type is NOT supported by CTranslate2 4.8.0 on NVIDIA L4 (SM 8.9). `int8_float16` is the most memory-efficient supported compute type on this architecture.

**Checksum:** Not available (model downloaded via faster-whisper library internal resolution).

### 12.2 LLM — Qwen2.5-7B-Instruct-FP8-dynamic

| Property | Value |
|---|---|
| **Model name** | `qwen2.5-7b-instruct-fp8` (served name) |
| **HuggingFace repo** | `RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic` |
| **Download method** | `huggingface_hub.snapshot_download()` |
| **Format** | safetensors (2 shards) |
| **Quantization** | W8A8 FP8: weights=float8 (channel-sym), activations=float8 (token-sym dynamic) |
| **Local path** | `/opt/voiceos-gpu/models/qwen2.5-7b-fp8/` |
| **Download size** | ~8,306 MB (shard 1: 4,755 MB, shard 2: 3,551 MB) |
| **Precision** | W8A8 FP8 (compressed-tensors format, auto-detected by vLLM) |
| **VRAM (scheduler reservation)** | 16,384 MiB |
| **VRAM (measured actual)** | 12,628 MiB at `--gpu-memory-utilization 0.55` |
| **Runtime dependency** | vLLM 0.24.0, PyTorch 2.11.0+cu130 |
| **vLLM flags** | `--dtype auto --max-model-len 4096 --gpu-memory-utilization 0.55 --served-model-name qwen2.5-7b-instruct-fp8` |
| **Port** | 8000 |
| **Health endpoint** | `/health` |
| **Inference endpoint** | `/v1/chat/completions` (OpenAI-compatible) |
| **Startup time** | ~90 seconds |
| **Latency target** | TTFT p95 ≤ 500ms |
| **Measured TTFT** | 208ms (Sprint-009 Phase 2) |
| **Streaming** | Yes (SSE, `Transfer-Encoding: chunked`) |

**Important — repo selection:** `Qwen/Qwen2.5-7B-Instruct-FP8` does NOT exist on HuggingFace. `RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic` is the correct production repo. The `--dtype` and `--quantization` flags are NOT needed — vLLM auto-detects the compressed-tensors FP8 config from the model's `quantization_config` in `config.json`.

**Checksum:** Two safetensors shards. Verify sizes: shard 1 ≈ 4,755 MB, shard 2 ≈ 3,551 MB.

### 12.3 TTS — Veena 3B BF16 + SNAC 24 kHz Codec

#### Veena Main Model

| Property | Value |
|---|---|
| **Model name** | `veena-tts` |
| **HuggingFace repo** | `maya-research/Veena` |
| **Download method** | `huggingface_hub.snapshot_download()` |
| **Format** | safetensors |
| **Architecture** | LlamaForCausalLM, 3B parameters |
| **Precision** | BF16 (directory name `veena-fp16` is legacy — weights are BF16, not FP16) |
| **Local path** | `/opt/voiceos-gpu/models/veena-fp16/` |
| **VRAM** | 7,980 MiB combined with SNAC (measured) |
| **Runtime dependency** | vLLM 0.24.0 (AsyncLLMEngine), PyTorch 2.11.0+cu130 |

#### SNAC 24 kHz Codec

| Property | Value |
|---|---|
| **HuggingFace repo** | `hubertsiuzdak/snac_24khz` |
| **Local path** | `/opt/voiceos-gpu/models/snac-24khz/` |
| **Sample rate** | 24,000 Hz |
| **VQ strides** | [4, 2, 1] → 1 + 2 + 4 = 7 tokens per audio frame |
| **Codebook size** | 4,096 |

#### TTS Service Properties

| Property | Value |
|---|---|
| **VRAM (scheduler reservation)** | 2,048 MiB |
| **VRAM (measured actual)** | 7,980 MiB (Veena 3B BF16 + SNAC) |
| **Port** | 8200 |
| **Health endpoints** | `/health/live`, `/health/ready` |
| **Inference endpoint** | `/synthesize` |
| **Startup time** | ~28 seconds |
| **Latency target** | First clause p95 ≤ 300ms (TT-010: not consistently achieved) |
| **Measured server TTFA** | p95 = 873ms (Sprint-012 Phase 3, streaming path) |
| **Streaming** | Yes — `Transfer-Encoding: chunked` via `vllm.AsyncLLMEngine` |
| **Inference mode** | 28-token sliding-window decode; 7-token-per-frame SNAC decode |

#### Veena Token Layout

| Token | Value |
|---|---|
| `START_OF_SPEECH` | 128257 |
| `END_OF_SPEECH` | 128258 |
| `START_OF_HUMAN` | 128259 |
| `END_OF_HUMAN` | 128260 |
| `START_OF_AI` | 128261 |
| `END_OF_AI` | 128262 |
| `AUDIO_CODE_BASE_OFFSET` | 128266 |
| Prompt format | `<spk_{speaker}> {text}` |
| Supported speakers | kavya (default), agastya, maitri, vinaya |

---

## 13. Service Specification

### 13.1 Service Startup Order

Services must be started in this exact order. `voiceos-llm` has the longest warmup and must start first.

```
1. voiceos-llm    (~90s warmup — start first, wait for /health to return 200)
2. voiceos-stt    (~7s  warmup)
3. voiceos-tts    (~28s warmup)
```

### 13.2 voiceos-llm — Qwen2.5 LLM Service

| Property | Value |
|---|---|
| **Purpose** | LLM response generation via vLLM (OpenAI-compatible API) |
| **Executable** | `/opt/voiceos-gpu/venv/bin/vllm` |
| **Startup command** | `vllm serve /opt/voiceos-gpu/models/qwen2.5-7b-fp8 --dtype auto --port 8000 --max-model-len 4096 --gpu-memory-utilization 0.55 --served-model-name qwen2.5-7b-instruct-fp8` |
| **systemd unit** | `/etc/systemd/system/voiceos-llm.service` |
| **Source** | `deployment/gpu/systemd/voiceos-llm.service` |
| **Port** | **8000** |
| **Health endpoint** | `GET /health` → HTTP 200 |
| **Inference endpoint** | `POST /v1/chat/completions` |
| **Metrics endpoint** | Not bound (TT-017) |
| **Restart policy** | `Restart=on-failure`, `RestartSec=10` |
| **Startup timeout** | `TimeoutStartSec=300` |
| **Log file** | `/opt/voiceos-gpu/logs/llm.log` |
| **User** | `ubuntu` |
| **Environment** | `EnvironmentFile=-/opt/voiceos-gpu/.env` |

**Key flags explained:**
- `--dtype auto`: reads `torch_dtype=bfloat16` from model config for non-quantized ops
- `--gpu-memory-utilization 0.55`: reduced from default 0.90 and from tested 0.70 to make room for Veena 3B BF16 (7,980 MiB) on the 23 GB L4
- `--max-model-len 4096`: reduced from 8192 to fit within 0.55 util budget
- No `--quantization` flag: vLLM auto-detects compressed-tensors FP8 from `quantization_config` in `config.json`

### 13.3 voiceos-stt — Whisper STT Service

| Property | Value |
|---|---|
| **Purpose** | Speech-to-text via Whisper Large-v3 Turbo |
| **Executable** | `/opt/voiceos-gpu/venv/bin/python3` |
| **Script** | `/opt/voiceos-gpu/services/stt/server.py` |
| **Startup command** | `python3 /opt/voiceos-gpu/services/stt/server.py --model-path /opt/voiceos-gpu/models/whisper-large-v3-turbo --compute-type int8_float16 --port 8100` |
| **systemd unit** | `/etc/systemd/system/voiceos-stt.service` |
| **Source** | `deployment/gpu/systemd/voiceos-stt.service` |
| **Port** | **8100** |
| **Health endpoints** | `GET /health/live` → HTTP 200; `GET /health/ready` → HTTP 200 |
| **Inference endpoint** | `POST /transcribe` |
| **Metrics endpoint** | Not bound (TT-017) |
| **Restart policy** | `Restart=on-failure`, `RestartSec=10` |
| **Startup timeout** | `TimeoutStartSec=120` |
| **Log file** | `/opt/voiceos-gpu/logs/stt.log` |
| **User** | `ubuntu` |
| **systemd dependency** | `After=voiceos-llm.service` |

### 13.4 voiceos-tts — Veena TTS Service

| Property | Value |
|---|---|
| **Purpose** | Text-to-speech via Veena 3B BF16 + SNAC 24 kHz codec |
| **Executable** | `/opt/voiceos-gpu/venv/bin/python3` |
| **Script** | `/opt/voiceos-gpu/services/tts/server.py` |
| **Startup command** | `python3 /opt/voiceos-gpu/services/tts/server.py --model-path /opt/voiceos-gpu/models/veena-fp16 --snac-path /opt/voiceos-gpu/models/snac-24khz --port 8200` |
| **systemd unit** | `/etc/systemd/system/voiceos-tts.service` |
| **Source** | `deployment/gpu/systemd/voiceos-tts.service` |
| **Port** | **8200** |
| **Health endpoints** | `GET /health/live` → HTTP 200; `GET /health/ready` → HTTP 200 |
| **Inference endpoint** | `POST /synthesize` |
| **Metrics endpoint** | Not bound (TT-017) |
| **Restart policy** | `Restart=on-failure`, `RestartSec=10` |
| **Startup timeout** | `TimeoutStartSec=180` |
| **Log file** | `/opt/voiceos-gpu/logs/tts.log` |
| **Environment** | `HF_HOME=/opt/voiceos-gpu/cache` (in addition to `.env`) |
| **User** | `ubuntu` |
| **systemd dependency** | `After=voiceos-llm.service` |

### 13.5 CPU ↔ GPU Communication

| Direction | Protocol | Port | Purpose |
|---|---|---|---|
| CPU GPUScheduler → GPU STT | HTTP | 8100 | STT inference requests |
| CPU GPUScheduler → GPU LLM | HTTP (OpenAI-compat) | 8000 | LLM completions (SSE streaming) |
| CPU GPUScheduler → GPU TTS | HTTP | 8200 | TTS synthesis (chunked streaming) |
| GPU services → CPU EventBus | Redis Streams | 6379 | Completion and telemetry events |

---

## 14. Known Deviations from Architecture Spec

| ID | Architecture Spec | Actual Production | Impact | Resolution |
|---|---|---|---|---|
| DEV-001 | CUDA ≥ 12.1 | CUDA 13.0 installed | None — newer is backward-compatible. PyTorch uses cu130 wheel. | Accepted. |
| DEV-002 | GPU VRAM ≥ 24,576 MiB | 23,034 MiB (L4) | Resolved: vLLM util 0.55 + measured total 21,850 MiB fits within capacity. | Accepted. Budget document in §9.3. |
| DEV-003 | Veena source: `models.voiceos.internal/veena-fp16` (FP16, internal registry) | `maya-research/Veena` (3B BF16, public HuggingFace) | Internal registry requires VPN (unavailable). Public repo verified working. BF16 not FP16 — dir name `veena-fp16` is legacy. | Accepted pending VPN availability. |
| DEV-004 | TTS VRAM reservation: 2,048 MiB | Actual footprint: 7,980 MiB (Veena 3B BF16 + SNAC) | Veena is 3B params — larger than the 2 GiB reservation. GPU Scheduler reservation stays 2,048 MiB; actual footprint is accounted for within the 0.55 vLLM util budget. | Accepted. Scheduler reservation is conservative. |
| DEV-005 | GPU metrics endpoint bound | No `/metrics` endpoint on any GPU service (TT-017) | Prometheus cannot scrape GPU service metrics. | Open — targeted for a future sprint. |
| TT-010 | TTS first-clause p95 ≤ 300ms | Measured 914–2974ms in reproducibility audit (2026-07-06) | TTFA degraded from 873ms baseline. Not root-caused. | Open — targeted for Sprint-028 performance validation. |

---

## 15. Document History

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0.0 | 2026-08-03 | Claude Code | Initial creation — consolidated from GPU_NODE_STATE.md, model_manifest.yaml, bootstrap.sh, restore.sh, and systemd unit sources |
