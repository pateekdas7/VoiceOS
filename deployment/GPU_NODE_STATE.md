# GPU Node State — VoiceOS v2

> **Living document.** Updated after every sprint that introduces GPU changes. Always describes the **complete current state** of the GPU node — not just the latest changes. Use this document to recreate the GPU node from scratch on any fresh server.

**Last updated:** Sprint-028 Phase 2 (2026-07-12) — GPU node restored to new server (217.18.55.120); security fixes deployed (PEN-005/006/007, PEN-009); bootstrap.sh ffmpeg dependency documented. Previously: Sprint-028 (2026-07-11) — Two GPU-side changes deployed:
1. **vLLM `--gpu-memory-utilization` reduced 0.55 → 0.45** (`voiceos-llm.service` ExecStart updated, service reloaded). Root cause: at 0.55 vLLM reserved 12,628 MiB, leaving only 569 MiB free on the L4. ctranslate2 (Whisper backend) lazily allocates its CUDA encoder workspace on the first real `model.encode()` call — not at model load time. This lazy allocation (~600 MiB) failed with CUDA OOM on every real STT inference request. At 0.45 vLLM takes 10,388 MiB (KV cache ≈ 6,200 MiB @ max_model_len=4096), freeing 2,263 extra MiB. STT OOM eliminated. KV cache reduction is acceptable for sequential calls. VRAM now: **20,289 MiB used / 2,745 MiB free**.
2. **STT server mandatory CUDA warmup added** (`deployment/gpu/services/stt/server.py` updated, service restarted). During `_load_model()`, immediately after loading the WhisperModel, a 0.5s silence transcription is run before `_model_ready` is set True. This forces ctranslate2 to pre-allocate and retain its CUDA workspace buffer so all subsequent real requests succeed without any additional large allocation. Warmup time: ~323ms. Service now also has a 300s startup timeout (`TimeoutStartSec=300` in the systemd unit).
Previously: Sprint-027 (2026-07-08) — documentation-only touch: **no GPU deployment/service change this sprint, and the GPU node was not accessed this session** (per the standing "ask first" rule — the user was asked and confirmed no GPU-side work was needed for this sprint's own scope). `monitoring/gpu_fleet/` (`GPUFleetHealthMonitor`, `ModelWarmupOrchestrator`, `FleetVRAMBudget`) is fleet-level *operational overlay* tooling validated against simulated multi-node fixtures (Sprint-027.md's own Phase 1 scope) — it does not run on, or require changes to, this GPU node itself; today's fleet is still the single node described below. OTel trace export from the GPU-side STT/LLM/TTS services to the CPU node's new OTel Collector (`http://<cpu-node>:<nodeport-or-tunnel>/v1/traces`) is **not wired this sprint** — those services never had an OTel SDK integration to begin with (same class of gap as the `/metrics` binding gap noted in `CPU_NODE_STATE.md` §13.5), and wiring it would require touching this node's service code, which was out of scope without a specific need to do so. Previously: Sprint-026 Phase 2 (2026-07-07) — **GPU node accessed this session with explicit user approval** (the standing "ask first" rule was honored — access was requested and approved before any SSH connection). Context: the CPU node was migrated to a new, genuinely unrestricted VM (`101.53.141.75`, see `CPU_NODE_STATE.md`) which now runs a real `kubeadm`+Calico Kubernetes cluster. To complete real K8S-2 GPU-taint/toleration validation, this GPU node was joined to that cluster via `kubeadm join` (after the user approved opening the CPU VM's firewall on port 6443 and regenerating its apiserver certificate with a public SAN). **The join itself succeeded** — `kubelet`/`containerd` were installed here (§17.1 below) and this node briefly appeared in the cluster as `jl-vm-440830`. However, Calico could not complete its own in-cluster bootstrap: the control plane's advertised address (`10.0.2.2`) is NAT-internal to the CPU VM's hypervisor and unroutable from this GPU node's (different cloud provider's) network — a structural "no shared VPC between the two providers" limitation. Real-time iptables DNAT/route/MASQUERADE patches (each individually user-approved) got direct reachability to `10.0.2.2:6443` working, but the identical problem recurred for the next Service ClusterIP (`10.96.0.1`, the in-cluster `kubernetes` service itself) — not a single fixable bug. Rather than keep layering ad-hoc NAT rules on this live GPU inference node, **the join was cleanly reverted**: `kubeadm reset` removed all Kubernetes state, `kubelet` was stopped and disabled, and every iptables/route change was undone. **STT/LLM/TTS were verified healthy (via `systemctl is-active`) before every step, after every step, and at the very end — no inference service was ever restarted, reconfigured, or interrupted.** Filed as `implementation/BACKLOG.md`'s **TT-015**. K8S-2 (GPU taint/toleration) remains validated by real Helm chart/Deployment-spec inspection (`gpu-scheduler` is the only chart requesting the `gpu` node pool + tolerating `nvidia.com/gpu`) and by this real, successful (if reverted) `kubeadm join` — but not by a fully `Ready`, permanently-joined GPU node running a scheduled tainted pod. See TT-015 for the full evidence trail and what a future attempt would need (a VPN/mesh between the two providers). No GPU model/service/systemd-unit configuration was touched. Previously: Sprint-025 Part-3 (2026-07-07) — documentation-only touch: **no GPU deployment/service change** — Part-3's schema extension (migration `0025`) and cross-platform wiring (`ConversationEngine.resolve_runtime_config()`, `CampaignService`/`WebhookService`/`RateLimitMiddleware`/`APIKeyLifecycleService`) is explicitly CPU-side only. Deep STT/LLM/TTS adapter rewiring was considered and deliberately declined — see `implementation/adrs/ADR-002-sprint025-scope-expansion.md` §9 — precisely because it would require its own GPU-side latency-budget validation pass (same class of work as ADR-001), which is out of scope here. Previously: Sprint-025 (2026-07-06) — documentation-only touch: **no GPU deployment/service change this sprint** — Sprint-025.md's own scope explicitly states "GPU node is not required during this sprint; previously deployed GPU services remain running unchanged" (Admin Portal/AI Configuration/Integration Platform/API Platform are pure CPU-side services with no GPU-adapter surface). Note: Sprint-024 (Billing/Metering/Analytics/Reporting/BI Platform) also never updated this file with its own entry — same gap, also a pure-CPU-side sprint, not backfilled here. Previously: Sprint-023 (2026-07-06) — documentation-only touch: **no GPU deployment/service change this sprint** — Sprint-023.md's own scope explicitly states "GPU node is not required during this sprint; previously deployed GPU services remain running unchanged" (Campaign Management/Contact Center/HITL are pure CPU-side services with no GPU-adapter surface). Previously: Sprint-022 (2026-07-06) — documentation-only touch: **no GPU deployment/service change this sprint** — Sprint-022.md's own scope explicitly states "GPU node is not required during this sprint; previously deployed GPU services remain running unchanged" (CRM/Collections are pure CPU-side services with no GPU-adapter surface). Previously: Sprint-021 (2026-07-06) — documentation-only touch: **no GPU deployment/service change this sprint** — Sprint-021.md's own scope explicitly states "GPU node is not required during this sprint; previously deployed GPU services remain running unchanged" (Tenant/Org/User Management are pure CPU-side services with no GPU-adapter surface). Previously: Post-Sprint-020 reproducibility audit (2026-07-06) — `deployment/gpu/validate_latency.py` written for real (TT-009: `restore.sh`/`model_manifest.yaml` had referenced this script since Sprint-009, but it never existed anywhere in the repo) and deployed + run against this live node for the first time. Node re-verified live: GPU UUID, all three systemd services (`voiceos-{stt,llm,tts}`) active, all three `/health*` endpoints ready, models/venv/vLLM version all match this document's prior claims. **New finding (TT-010, implementation/BACKLOG.md):** three direct `/synthesize` calls measured TTS time-to-first-chunk at 914–2974ms — well above both `model_manifest.yaml`'s 300ms target and this document's own previously-recorded 873ms Sprint-012 Phase 3 server-level TTFA p95, with the GPU otherwise idle (0% utilization, no contending processes) at measurement time. Not root-caused this session (out of a reproducibility audit's scope; flagged for follow-up, not fixed). Also observed: `/opt/voiceos-gpu/deployment/` on the live node contains only `healthcheck.sh` — `bootstrap.sh`/`restore.sh`/`model_manifest.yaml`/`download_models.py` were never copied there (this node was provisioned once, by hand, and never rebuilt — unlike the CPU-side gap, the GPU `restore.sh` and everything it references genuinely exist and are internally consistent in the repo; they simply haven't needed to be re-run against this already-running node). A stray, unused `models/whisper-large-v3-turbo-fp8/` directory (not in any doc) was also observed — harmless, likely an abandoned early experiment given FP8 isn't supported by this node's CTranslate2 version (see §8, restore.sh comments). Previously: Sprint-019 (2026-07-05) — documentation-only touch: added reserved `VAULT_ADDR`/`VAULT_TOKEN` env var rows (§12) and a secrets-rotation-grace-window note (§16) ahead of a future sprint's GPU-side secrets wiring. **No GPU deployment/service change this sprint** — Sprint-019.md's own scope explicitly states "GPU node is not required during this sprint; previously deployed GPU services remain running unchanged." The last actual GPU deployment remains Sprint-012 Phase 3 (2026-07-04) — TTS serving layer replaced with vLLM streaming (ADR-001). Backfilled into this document during the Sprint-013 TT-003 documentation cleanup (2026-07-04) — the deployment happened 2026-07-04 but this file was not updated at the time; see CHANGELOG.md's Sprint-012 entry and `implementation/adrs/ADR-001-vllm-tts-streaming.md` for the original record this backfill is sourced from.
**Node identity:** GPU-2ba0ea2a-d4bf-d29d-e152-8d352c727524 (NVIDIA L4 — stable UUID)
**Node access address:** 217.18.55.120 (⚠️ ephemeral — the SSH access IP rotates as the port-forward link is reissued; prior IPs were .19, .78, .74, .96, and .129. Current server (restored 2026-07-12) is a fresh NVIDIA L4 node at .120. Identify the node by its GPU UUID, not the IP.)
**Access key:** `~/.ssh/temporary.pem` (EC2-style PEM; relocated 2026-07-08 out of the repo root during a production-readiness audit — TT-020 — since a real private key sitting in the project working tree is a credential-hygiene risk once the repo goes under version control)
**Access user:** ubuntu
**SSH:** `ssh -i ~/.ssh/temporary.pem -o StrictHostKeyChecking=no ubuntu@<current-ip>`
**Status:** ✅ GPU node fully provisioned. All three inference services (STT/LLM/TTS) deployed and running. STT/LLM validated end-to-end in Sprint-009 Phase 2 (2026-07-03); TTS serving layer replaced with vLLM `AsyncLLMEngine` streaming in Sprint-012 Phase 3 (2026-07-04, ADR-001) — server-level TTFA p95 = 873ms (target 1,500ms ✅), see §8.1. No GPU changes since Sprint-012 Phase 3; Sprint-013 through Sprint-021 did not touch this node (each of Auth/Authz/AI-Governance/Secrets/Encryption/Privacy/PII/Audit/API-Security/AI-Safety/Tenant-Org-User-Management is pure CPU-side, per each sprint's own "GPU node is not required during this sprint" note; no regression check was re-run against the GPU node during those sprints since nothing on the CPU↔GPU path changed).

---

## 1. Operating System

| Property | Value |
|---|---|
| OS | Ubuntu 22.04.5 LTS (Jammy Jellyfish) |
| Kernel | 5.15.0-171-generic |
| Architecture | x86_64 |
| CPU cores | 28 |
| Memory | 121 GiB total |
| Disk | 97 GB (`/dev/vda1`) — 34 GB used after Sprint-008 deployment |

---

## 2. NVIDIA Stack

| Component | Version | Status |
|---|---|---|
| NVIDIA Driver | 580.126.20 | ✅ Installed |
| CUDA Toolkit | 13.0 (at `/usr/local/cuda-13.0/`) | ✅ Installed |
| cuDNN | 9.19.0 (via PyTorch `torch.backends.cudnn.version()` = 91900) | ✅ Available |
| NVIDIA Container Toolkit | 1.19.0 | ✅ Installed |

> **Note:** CUDA 13.0 is installed (spec says ≥12.1). 13.0 is backward compatible. PyTorch and vLLM use the cu130 build. All GPU operations verified working.

```bash
# Verify
nvidia-smi
nvcc --version    # from /usr/local/cuda-13.0/bin/nvcc
nvidia-ctk --version
```

---

## 3. GPU Hardware

| Property | Value |
|---|---|
| GPU model | NVIDIA L4 |
| VRAM total | 23,034 MiB (22.5 GB) |
| VRAM free (idle) | ~22,564 MiB |
| Driver version | 580.126.20 |
| PCIe slot | device 0 (GPU-2ba0ea2a-d4bf-d29d-e152-8d352c727524) |

> **VRAM Budget (updated Sprint-028, 2026-07-11):**
> - Whisper `int8_float16` loads **1,260 MiB** model + **~600 MiB** ctranslate2 CUDA encoder workspace (pre-allocated via warmup inference — Sprint-028 fix; was lazily allocated causing OOM at 0.55 utilization)
> - Qwen FP8 via vLLM `--gpu-memory-utilization 0.45` → **10,388 MiB** (reduced from 12,628 at 0.55; KV cache ≈ 6,200 MiB @ max_model_len=4096 — adequate for sequential calls)
> - Veena (maya-research/Veena, 3B BF16) + SNAC 24 kHz codec → **8,558 MiB**
> - **Measured total (Sprint-028): 20,289 MiB used / 2,745 MiB free** on 23,034 MiB L4
> - Prior Sprint-009 budget (0.55 util): 21,850 MB used / 695 MB free — STT OOM-ed at first real inference because 695 MB < 600 MB ctranslate2 lazy allocation

```bash
nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv,noheader
# Output: NVIDIA L4, 23034 MiB, 22564 MiB, 580.126.20
```

---

## 4. Python

| Property | Value |
|---|---|
| Version | Python 3.12.13 (installed via deadsnakes PPA) |
| Virtual environment | `/opt/voiceos-gpu/venv` |
| Package count | 211 packages |

```bash
# Verify
python3.12 --version    # Python 3.12.13
/opt/voiceos-gpu/venv/bin/python --version   # Python 3.12.13
```

---

## 5. Docker

| Property | Value |
|---|---|
| Docker Engine | 29.3.1 (build c2be9cc) |
| Status | Active (running) |
| Default runtime | **nvidia** (configured) |
| NVIDIA Container Toolkit | Configured as default runtime |

```bash
# Verify Docker status
systemctl is-active docker    # active
docker --version              # Docker version 29.3.1

# Verify NVIDIA default runtime
cat /etc/docker/daemon.json
# {
#   "default-runtime": "nvidia",
#   "runtimes": {
#     "nvidia": { "args": [], "path": "nvidia-container-runtime" }
#   }
# }
```

---

## 6. vLLM

| Property | Value |
|---|---|
| Version | 0.24.0 |
| Installation | `/opt/voiceos-gpu/venv/bin/pip install vllm` |
| CUDA build | cu130 (CUDA 13.0) |
| Purpose | LLM inference server for Qwen2.5 models |

```bash
/opt/voiceos-gpu/venv/bin/python -c "import vllm; print(vllm.__version__)"
# 0.24.0
```

---

## 7. PyTorch

| Property | Value |
|---|---|
| Version | 2.11.0+cu130 |
| CUDA available | True |
| cuDNN available | True (version 91900 = 9.19.0) |
| VRAM accessible | 22,563 MB via `torch.cuda.get_device_properties(0).total_memory` |

```bash
/opt/voiceos-gpu/venv/bin/python -c "
import torch
print('torch', torch.__version__, '| CUDA:', torch.cuda.is_available(), '| cuDNN:', torch.backends.cudnn.is_available())
print('GPU:', torch.cuda.get_device_name(0))
print('VRAM:', torch.cuda.get_device_properties(0).total_memory // 1024**2, 'MB')
"
```

---

## 8. Deployed AI Models

> **All three services deployed and running.** STT/LLM validated end-to-end in Sprint-009 Phase 2 (2026-07-03). TTS serving layer (only) was replaced in Sprint-012 Phase 3 (2026-07-04) — see §8.1. **Production model selections are locked** (2026-07-02); Veena AI FP16 was retained unchanged through the Phase 3 serving-layer rewrite. These remain the permanent model baseline.

### 8.1 TTS Serving Layer — Sprint-012 Phase 3 Update (ADR-001, 2026-07-04)

**Problem:** Sprint-009 Phase 2 measured TTS first-audio latency (TTFA) p95 of 8,730ms against a 1,500ms target (TT-001). Root cause: `deployment/gpu/services/tts/server.py` used `AutoModelForCausalLM.generate()` (HuggingFace batch API — generates all SNAC tokens before returning) and the `/synthesize` endpoint returned a fully-buffered `Response(content=audio_bytes)`. This was an implementation limitation in the serving layer, not a Veena model limitation.

**Fix deployed (`implementation/adrs/ADR-001-vllm-tts-streaming.md`, approved 2026-07-03, deployed 2026-07-04):**

| Component | Before (Sprint-009 Phase 2) | After (Sprint-012 Phase 3) |
|---|---|---|
| Inference engine | `transformers.AutoModelForCausalLM.generate()` (HF batch) | `vllm.AsyncLLMEngine` (same vLLM install already used for Qwen2.5-7B — no new dependency) |
| Token delivery | All tokens generated before any return | `async for request_output in engine.generate()` — integer token IDs streamed incrementally |
| Vocabulary control | None | `OnlyAudioAfterSOS` custom logits processor — restricts generation to the SNAC token range after START_OF_SPEECH, reducing hallucination risk |
| SNAC decode | Single batch decode after full generation | 28-token sliding-window decode: accumulate 4 super-frames, decode on every 7 new tokens, extract only the middle 85.33ms super-frame (`audio[2048:4096]`), discard the outer frames' boundary artifacts (canonical pattern from the maya1/Orpheus-TTS reference implementations — see ADR-001 §2) |
| HTTP response | `Response(content=audio_bytes)` — fully buffered | `StreamingResponse(...)` — `Transfer-Encoding: chunked` |
| CPU-side client | `src/services/tts/adapters/veena_adapter.py`: buffered `resp.content` | `client.stream("POST", ...)` + `resp.aiter_bytes()` — chunked consumption |
| Model / weights | Veena AI FP16 (`maya-research/Veena`) | **Unchanged** — same weights, same VRAM footprint (§3) |

**Validated 2026-07-04:**
- TTS server-level TTFA p95 = **873ms** ✅ (target 1,500ms)
- `Transfer-Encoding: chunked` confirmed on `/synthesize`
- True incremental streaming confirmed (first audio chunk arrives well before full synthesis completes)
- Walking skeleton end-to-end pipeline p50 = 2,355ms / p95 = 6,048ms — higher than the TTS server's own TTFA because `VeenaAdapter`'s clause synthesis is still sequential relative to LLM token streaming (not a GPU-side issue; filed as TT-001-residual, targeted for a future sprint after Reliability/E4)

**Deployment mechanics:** `deployment/gpu/services/tts/server.py` was updated and redeployed to this node (`rsync`/`scp` + service restart per §13); the Veena systemd unit (`voiceos-tts.service`) and its port (8200) are unchanged — only the file's internal implementation changed. `src/services/tts/adapters/veena_adapter.py` (CPU node) was updated in the same change to consume the new chunked response format.

### VRAM Allocation (Sprint-009 Phase 2 — measured 2026-07-03)

| Model | Source Repo | Precision | VRAM Reserved | Actual VRAM (measured) | Port | Status |
|---|---|---|---|---|---|---|
| Whisper Large-v3 Turbo | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (via faster-whisper) | int8_float16 | 6,144 MB | **1,242 MB** | 8100 | ✅ Running |
| Qwen2.5-7B-Instruct-FP8-dynamic (vLLM) | `RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic` | W8A8 FP8 (auto-detected) | 16,384 MB | **10,388 MiB** (at 0.45 util, Sprint-028; was 12,628 at 0.55) | 8000 | ✅ Running |
| Veena TTS + SNAC | `maya-research/Veena` (3B) + `hubertsiuzdak/snac_24khz` | BF16 (Veena) / FP32 (SNAC) | 2,048 MB | **8,558 MiB** (Sprint-028 measured; was 7,980 at Sprint-009) | 8200 | ✅ Running (streaming since Sprint-012 Phase 3) |
| **Total (Sprint-028 measured)** | | | | **20,289 MiB used / 2,745 MiB free** | | |

> **Validated 2026-07-03 (Sprint-009 Phase 2 — end-to-end):**
> - **Whisper STT**: loaded in 7.2s, `/transcribe` latency ~330–430 ms on synthetic audio, `int8_float16` compute type, word-level timestamps returned. Endpoints `/health/live`, `/health/ready`, `/transcribe` on port 8100.
> - **Qwen LLM (vLLM)**: `--gpu-memory-utilization 0.55`, `--max-model-len 4096`. TTFT **208 ms**, SSE streaming confirmed via `/v1/chat/completions`. Served as `qwen2.5-7b-instruct-fp8`. `--dtype auto` (compressed-tensors auto-detected).
> - **Veena TTS (Sprint-009 Phase 2 batch baseline — superseded by Phase 3, see §8.1)**: loaded in ~28s (Veena 3B BF16 + SNAC 24 kHz). `/synthesize` produced valid 24 kHz float32 PCM (68.7% non-silent, RMS ~0.065). Warm RTF ~2.3 (autoregressive batch inference; TTFA p95 was 8,730ms in this configuration — TT-001). Endpoints `/health/live`, `/health/ready`, `/synthesize` on port 8200 unchanged; **as of Sprint-012 Phase 3 the endpoint streams via `vllm.AsyncLLMEngine` instead of batch-generating** — TTFA p95 = 873ms. See §8.1 for the current architecture.
> - **Veena SNAC decode**: audio codes use the 7-token-per-frame Orpheus/Veena layout (SNAC vq_strides [4,2,1] → 1+2+4). Control tokens: START_OF_HUMAN=128259, END_OF_HUMAN=128260, START_OF_AI=128261, START_OF_SPEECH=128257, END_OF_SPEECH=128258; AUDIO_CODE_BASE_OFFSET=128266. Prompt format `<spk_{speaker}> {text}`. Speakers: kavya (default), agastya, maitri, vinaya.
> - **Deployment note**: Veena is served from the public `maya-research/Veena` repo (BF16), NOT the internal `models.voiceos.internal/veena-fp16` registry (which requires VPN and was unavailable). Weights are on-node at `/opt/voiceos-gpu/models/veena-fp16/`.
> - **Process robustness**: services launched with `setsid ... < /dev/null` so they survive SSH disconnect (plain `nohup &` was killed by SIGHUP when the launching session closed).

---

## 9. Model Locations and Cache

| Model | Local Path | Cache Path | Status |
|---|---|---|---|
| Whisper Large-v3 Turbo | `/opt/voiceos-gpu/models/whisper-large-v3-turbo/` | `/opt/voiceos-gpu/cache/whisper/` | ✅ Deployed & serving |
| Qwen2.5-7B FP8 | `/opt/voiceos-gpu/models/qwen2.5-7b-fp8/` | `/opt/voiceos-gpu/cache/qwen/` | ✅ Deployed & serving |
| Veena TTS (BF16) | `/opt/voiceos-gpu/models/veena-fp16/` | `/opt/voiceos-gpu/cache/` | ✅ Deployed & serving |
| SNAC 24 kHz codec | `/opt/voiceos-gpu/models/snac-24khz/` | `/opt/voiceos-gpu/cache/` | ✅ Deployed & serving |

---

## 10. Complete GPU Python Package Inventory (Sprint-008)

Key packages in `/opt/voiceos-gpu/venv`:

| Package | Version |
|---|---|
| torch | 2.11.0+cu130 |
| torchvision | 0.26.0 |
| torchaudio | 2.11.0 |
| vllm | 0.24.0 |
| faster-whisper | 1.2.1 |
| grpcio | 1.81.1 |
| grpcio-tools | 1.81.1 |
| fastapi | 0.136.3 |
| uvicorn | 0.49.0 |
| prometheus-client | 0.25.0 |
| opentelemetry-sdk | 1.43.0 |
| opentelemetry-exporter-otlp | 1.43.0 |
| huggingface-hub | 1.21.0 |

---

## 11. Directory Structure

```
/opt/voiceos-gpu/
├── venv/                   # Python 3.12.13 virtual environment (211 packages)
├── models/
│   ├── whisper-large-v3-turbo/       # Sprint-009 — downloading
│   ├── qwen2.5-7b-fp8/               # Sprint-009 — downloading
│   └── veena-fp16/                   # Sprint-009 Phase 2 (internal registry)
├── cache/
│   ├── whisper/
│   ├── qwen/
│   └── veena/
├── models/snac-24khz/      # Sprint-009 Phase 2 — SNAC codec for Veena
├── services/
│   ├── stt/server.py       # Sprint-009 — Whisper FastAPI (port 8100)
│   ├── llm/                # Sprint-009 — vLLM launched directly (no wrapper script)
│   └── tts/server.py       # Sprint-009 — Veena + SNAC FastAPI (port 8200)
├── deployment/             # Deployment scripts (from repo)
└── logs/                   # Service logs (stt.log, llm.log, tts.log)
```

---

## 12. Environment Variables

| Variable | Service(s) | Purpose |
|---|---|---|
| `CUDA_VISIBLE_DEVICES` | All GPU services | GPU device selection (default: `0`) |
| `GPU_MEMORY_FRACTION` | vLLM | VRAM fraction to allocate (default: `0.45` — reduced from 0.55 in Sprint-028 to leave ≥2 GiB free for ctranslate2 STT CUDA workspace; 0.55 caused STT OOM on first real inference) |
| `WHISPER_MODEL_PATH` | STT service | Path to Whisper model weights |
| `QWEN_MODEL_PATH` | LLM service | Path to Qwen2.5 model weights |
| `VEENA_MODEL_PATH` | TTS service | Path to Veena model weights |
| `STT_SERVICE_PORT` | STT service | Port (default: `8100`) |
| `LLM_SERVICE_PORT` | LLM service | Port (default: `8000`) |
| `TTS_SERVICE_PORT` | TTS service | Port (default: `8200`) |
| `GPU_SCHEDULER_REPORT_URL` | All GPU services | CPU node GPU Scheduler URL |
| `VAULT_ADDR` (Sprint-019) | Reserved, unwired | Points at the self-hosted Vault instance on the CPU node (`http://<cpu-node-ip>:8200`) — no Vault runs on this node. Not yet consulted by any GPU service (STT/LLM/TTS adapters still read model-serving config directly from `.env`); reserved for a future sprint's GPU-side secrets wiring. |
| `VAULT_TOKEN` (Sprint-019) | Reserved, unwired | Would be a `voiceos-app`-scoped token (same policy as the CPU node — see CPU_NODE_STATE.md §7.8), not yet issued/used here. |

---

## 13. Service Startup Order (Sprint-009+)

1. Verify NVIDIA driver: `nvidia-smi`
2. Start Qwen2.5 LLM via vLLM (port 8000) — longest startup (~90s)
3. Start Whisper STT service (port 8100)
4. Start Veena TTS service (port 8200)
5. Verify all services ready (`bash deployment/healthcheck.sh`)
6. Signal CPU node GPU Scheduler

> **Persistence (Sprint-009 Phase 2, 2026-07-03):** all three services run as **systemd units** —
> `voiceos-llm.service`, `voiceos-stt.service`, `voiceos-tts.service` (sources in `deployment/gpu/systemd/`,
> installed to `/etc/systemd/system/`). All are `enabled` (auto-start on boot) with `Restart=on-failure`
> (verified: killing a service PID triggers automatic restart to a healthy state). Manage with
> `sudo systemctl {start,stop,restart,status} voiceos-{llm,stt,tts}`. Logs still append to
> `/opt/voiceos-gpu/logs/{llm,stt,tts}.log`. `restore.sh` auto-detects the enabled units and uses
> `systemctl restart` instead of the manual `setsid` fallback.
>
> **Manual fallback startup commands (used only if systemd units are absent):**
> ```bash
> # LLM (vLLM) — start first (longest warmup)
> # --gpu-memory-utilization 0.45: DO NOT raise above 0.45 without re-validating STT VRAM headroom.
> # At 0.55 (12,628 MiB), only 569 MiB is free post-load; ctranslate2's lazy CUDA workspace (~600 MiB)
> # OOMs on the first real STT transcription. 0.45 (10,388 MiB) leaves 2,745 MiB free — sufficient.
> setsid /opt/voiceos-gpu/venv/bin/vllm serve /opt/voiceos-gpu/models/qwen2.5-7b-fp8 \
>   --dtype auto --port 8000 --max-model-len 4096 \
>   --gpu-memory-utilization 0.45 --served-model-name qwen2.5-7b-instruct-fp8 \
>   >> /opt/voiceos-gpu/logs/llm.log 2>&1 < /dev/null &
>
> # STT (Whisper)
> setsid /opt/voiceos-gpu/venv/bin/python3 /opt/voiceos-gpu/services/stt/server.py \
>   --model-path /opt/voiceos-gpu/models/whisper-large-v3-turbo \
>   --compute-type int8_float16 --port 8100 \
>   >> /opt/voiceos-gpu/logs/stt.log 2>&1 < /dev/null &
>
> # TTS (Veena + SNAC)
> setsid /opt/voiceos-gpu/venv/bin/python3 /opt/voiceos-gpu/services/tts/server.py \
>   --model-path /opt/voiceos-gpu/models/veena-fp16 \
>   --snac-path /opt/voiceos-gpu/models/snac-24khz --port 8200 \
>   >> /opt/voiceos-gpu/logs/tts.log 2>&1 < /dev/null &
> ```
> Use `setsid ... < /dev/null` (not plain `nohup &`) so services survive SSH disconnect.

---

## 14. Health Check Commands

```bash
# NVIDIA driver
nvidia-smi

# CUDA + Python
/opt/voiceos-gpu/venv/bin/python -c "import torch; print('CUDA:', torch.cuda.is_available())"

# vLLM
/opt/voiceos-gpu/venv/bin/python -c "import vllm; print('vLLM:', vllm.__version__)"

# Docker GPU (Sprint-009+)
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi

# VRAM allocation
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader

# STT Service (after Sprint-009)
# curl -sf http://localhost:8100/health/ready && echo "Whisper: OK"

# LLM Service via vLLM (after Sprint-009)
# curl -sf http://localhost:8000/health && echo "vLLM: OK"

# TTS Service (after Sprint-009)
# curl -sf http://localhost:8200/health/ready && echo "Veena: OK"

# TTS streaming validation (Sprint-012 Phase 3, ADR-001)
# curl -sD - -o /dev/null -X POST http://localhost:8200/synthesize \
#   -H "Content-Type: application/json" -d '{"text":"health check","speaker":"kavya"}' \
#   | grep -i "transfer-encoding: chunked" && echo "Veena streaming: OK"
```

---

## 15. CPU ↔ GPU Communication

| Direction | Protocol | Port | Purpose |
|---|---|---|---|
| CPU GPUScheduler → GPU STT | HTTP | 8100 | STT inference requests |
| CPU GPUScheduler → GPU LLM | HTTP (OpenAI-compat) | 8000 | LLM completions |
| CPU GPUScheduler → GPU TTS | HTTP | 8200 | TTS synthesis requests |
| GPU services → CPU EventBus | Redis Streams | 6379 | Completion events |

---

## 16. Known Deviations from Architecture Spec

| Item | Spec | Actual | Impact |
|---|---|---|---|
| CUDA version | 12.1+ | 13.0 | None — newer is compatible. PyTorch uses cu130 build. |
| GPU VRAM | ≥24,576 MB | 23,034 MB | Resolved in Sprint-009 Phase 2: vLLM util 0.55, measured total 21,850 MB used / 695 MB free |
| Veena source | `models.voiceos.internal/veena-fp16` (FP16, internal) | `maya-research/Veena` (3B BF16, public) | Internal registry requires VPN (unavailable). Public repo verified working; BF16 not FP16. |
| TTS VRAM | 2,048 MB (reserved) | 7,980 MB (Veena 3B BF16 + SNAC) | Veena is 3B params — larger than the 2 GB reservation assumed. Scheduler reservation stays 2,048 MB; actual footprint accounted in the 0.55 vLLM util budget. |
| cuDNN standalone install | libcudnn8 | Available via PyTorch (91900 = 9.19.0) | None |
| Docker nvidia base image | Pull and test | Not pre-pulled | Pull images in Sprint-009 bootstrap |
| HuggingFace CLI | System-wide | In venv at `/opt/voiceos-gpu/venv/bin/huggingface-cli` | Use full path |
| Secrets rotation grace window (Sprint-019) | N/A — no rollback section exists in this document; added here as the closest fit | Vault is reachable from this node (`VAULT_ADDR`, §12) but not yet consulted by any GPU service — if/when it is, `SecretsManager`'s rotation grace window (60s: the old secret value remains valid for 60s after a new one is issued, src/libs/secrets/manager.py) applies here identically to the CPU node, so a rolling restart of GPU services during a secret rotation would not need to be perfectly synchronized. | Not yet applicable — GPU services still read model-serving config directly from `.env`, per Sprint-019.md's own "GPU node is not required during this sprint" scope note. |

---

## 17. Bootstrap Procedure (from fresh Ubuntu 22.04)

```bash
# 1. Add deadsnakes PPA and install Python 3.12
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update -qq
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev

# 2. Verify NVIDIA driver (pre-installed on GPU VMs)
nvidia-smi

# 3. Install NVIDIA Container Toolkit (if not present)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > \
  /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update -qq && apt-get install -y nvidia-container-toolkit

# 4. Configure Docker default runtime
cat > /etc/docker/daemon.json <<'EOF'
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": { "args": [], "path": "nvidia-container-runtime" }
  }
}
EOF
systemctl restart docker

# 5. Create directory structure
mkdir -p /opt/voiceos-gpu/{venv,models/whisper-large-v3-turbo,models/qwen2.5-7b-fp8,models/veena-fp16,cache/whisper,cache/qwen,cache/veena,logs,services/{stt,llm,tts},deployment}
chown -R ubuntu:ubuntu /opt/voiceos-gpu

# 6. Create Python venv
python3.12 -m venv /opt/voiceos-gpu/venv
/opt/voiceos-gpu/venv/bin/pip install --upgrade pip wheel setuptools

# 7. Install PyTorch (cu130)
/opt/voiceos-gpu/venv/bin/pip install torch torchvision torchaudio

# 8. Install vLLM and GPU services stack
/opt/voiceos-gpu/venv/bin/pip install \
  vllm \
  faster-whisper \
  grpcio grpcio-tools \
  fastapi uvicorn \
  prometheus-client \
  opentelemetry-sdk opentelemetry-exporter-otlp \
  huggingface_hub

# 9. Verify
/opt/voiceos-gpu/venv/bin/python -c "import torch, vllm; print('CUDA:', torch.cuda.is_available()); print('vLLM:', vllm.__version__)"
```

---

## 17.1 Kubernetes Cluster Membership (Sprint-026 Phase 2, attempted and reverted — TT-015)

**Current state: this node is NOT a member of the CPU node's Kubernetes cluster.** `kubelet`/`kubeadm`/`kubectl` binaries were installed here (relayed via `scp` from a machine with normal internet, since this node's own network — unlike the CPU VM's — has no DPI restriction and could reach `dl.k8s.io` directly) and `kubeadm join` succeeded, but the join was fully reverted after Calico could not complete its bootstrap (cross-provider NAT limitation — see the header note and `implementation/BACKLOG.md`'s TT-015 for full detail). Nothing GPU-service-related was changed; `nvidia-smi`, Docker, and the three `voiceos-{stt,llm,tts}` systemd units are exactly as documented elsewhere in this file.

**What was installed and left in place (harmless, inert):**
- `/usr/local/bin/{kubeadm,kubelet,kubectl}` — binaries only, `kubelet.service` is `stopped`/`disabled`.
- `containerd`'s CRI plugin was configured (`/etc/containerd/config.toml`, `SystemdCgroup = true`) — this coexists safely with Docker's own use of the same containerd instance (different internal namespaces: `moby` for Docker, `k8s.io` for CRI) and does not affect the `nvidia` runtime Docker uses for GPU containers.
- Kernel modules (`overlay`, `br_netfilter`) and sysctls (`net.ipv4.ip_forward=1`, bridge-netfilter) — harmless on any Linux host, commonly already-default.

**What was explicitly reverted:**
- `kubeadm reset -f` (removed `/etc/kubernetes`, stopped/unmounted kubelet-managed volumes).
- All iptables NAT rules added during the connectivity troubleshooting (`OUTPUT` DNAT for `10.0.2.2:6443` and `10.96.0.1:443`, `POSTROUTING` MASQUERADE) — removed via matching `-D` rules.
- The `10.96.0.1/32 dev enp2s0` route added for troubleshooting — removed via `ip route del`.
- The Node object (`jl-vm-440830`) on the CPU node's cluster — removed via `kubectl delete node`.

**If re-attempting in the future:** the blocker is network-level, not a configuration mistake here. Real connectivity (a WireGuard mesh or similar VPN between this GPU node's provider and the CPU VM's provider, or migrating both to the same provider/VPC) is needed before `kubeadm join` can result in a fully `Ready` node. See `implementation/BACKLOG.md`'s TT-015 for the complete diagnostic trail.

---

---

## 18. Sprint-028 Changes (2026-07-11)

### 18.1 vLLM GPU Memory Utilization: 0.55 → 0.45

**Symptom:** All 100 calls in Sprint-028 Phase 2 latency validation returned HTTP 500 from the STT endpoint (`RuntimeError: CUDA failed with error out of memory`).

**Root cause:** ctranslate2 (the Whisper backend in faster-whisper) does NOT pre-allocate its CUDA encoder workspace buffers at model load time. It allocates them lazily on the first call to `model.encode()`. With vLLM at 0.55 utilization (12,628 MiB), only **569 MiB** remained free post-load on the L4. The lazy allocation requires ~600 MiB → OOM on every real transcription.

**Fix:**
- Changed `voiceos-llm.service` `ExecStart` `--gpu-memory-utilization 0.55 → 0.45`
- At 0.45, vLLM takes 10,388 MiB (KV cache ≈ 6,200 MiB @ max_model_len=4096)
- New free VRAM: **2,745 MiB** — well above the 600 MiB ctranslate2 workspace
- KV cache reduction is acceptable for sequential (non-concurrent) call workloads

**Deployed:** `scp deployment/gpu/systemd/voiceos-llm.service → /etc/systemd/system/voiceos-llm.service`, `systemctl daemon-reload`, `systemctl restart voiceos-llm`

### 18.2 STT Server: Mandatory CUDA Warmup Inference

**Symptom:** Even after vLLM memory reduction, first real transcription could still fail if ctranslate2 has not yet allocated its workspace.

**Fix:** `deployment/gpu/services/stt/server.py` → `_load_model()` now runs a 0.5s silence transcription immediately after `WhisperModel()` loads and before setting `_model_ready = True`. This forces ctranslate2 to pre-allocate and retain its CUDA workspace in the already-allocated VRAM pool. All subsequent real requests succeed without any additional large allocation. Observed warmup time: **~323ms**.

**Key invariant:** `_model_ready` is set True ONLY AFTER the warmup succeeds. If warmup OOMs, the service crashes at startup (correct — better than serving 500s on every request).

**Deployed:** `scp deployment/gpu/services/stt/server.py → /opt/voiceos-gpu/services/stt/server.py`, `systemctl restart voiceos-stt`

### 18.3 Sprint-028 Validated Performance (post-fix)

| Stage | p50 | p95 | Notes |
|---|---|---|---|
| STT (Whisper large-v3-turbo) | ~822ms | ~1000ms | Measured from Termux (mobile 4G) — intra-DC would be ~50-150ms lower |
| LLM TTFT (Qwen2.5-7B FP8) | ~279ms | ~340ms | Prefix cache hit rate growing to 71% after 100 calls |
| TTS TTFA (Veena 3B BF16) | ~857ms | ~1000ms | Server-side: 660-730ms; client includes network RTT |
| **first_audio (gate metric)** | **~1981ms** | **~2300ms** | **GATE FAIL** — target 1500ms; architectural gap documented as Gap-2 in production-alpha-report.md |

**Thermal throttling:** L4 hits 72W TDP after ~110s continuous inference → clocks drop from 2040MHz to ~1000MHz → all three models double in latency. Cold GPU (first ~18 calls): p95=933ms intra-DC. Sustained: p95=1950ms.

**Architectural constraint:** Sum of model minimums (Whisper ~600ms + Qwen ~220ms + Veena TTFA ~660ms) = ~1480ms before any network overhead. The 1500ms gate requires sub-5ms network latency from call ingress to all three GPU services — achievable only with a GPU fleet where models are pre-loaded and calls are routed to idle nodes. ADR required to revise V1 Ch23 TTS budget (250ms specified; 660ms minimum achievable).

---

---

## 19. Sprint-028 Phase 2 Changes (2026-07-12)

### 19.1 GPU Node Restoration to New Server (217.18.55.120)

Prior node (217.18.55.78) was decommissioned. Fresh NVIDIA L4 server provisioned at 217.18.55.120. Full restore completed via `bootstrap.sh` + manual model downloads.

**Key difference from prior restore:** `bootstrap.sh` was missing `ffmpeg` from apt-get package list. PyTorch 2.11.0+cu128 installs `torchcodec 0.14.0` as a transitive dependency, which requires `libavutil.so.56` from FFmpeg. Without it, vLLM fails at import: `RuntimeError: Could not load libtorchcodec. OSError: libavutil.so.56: cannot open shared object file`. Fixed: `ffmpeg` added to `bootstrap.sh` apt-get install block (committed).

**Post-restore validation (2026-07-12):**

| Service | Health Endpoint | Status |
|---|---|---|
| STT (Whisper :8100) | `/health/ready` | ✅ READY |
| LLM (vLLM :8000) | `/health` | ✅ OK |
| TTS (Veena :8200) | `/health/ready` | ✅ READY |

Real inference: STT→LLM→TTS first_audio = 1,032ms (cold GPU, single call). PASS.

**VRAM post-restore (measured):**

| Model | Measured (MiB) |
|---|---|
| Whisper large-v3-turbo | ~1,324 |
| Qwen2.5-7B-FP8 @ 0.45 util | ~10,590 |
| Veena 3B BF16 + SNAC 24kHz | ~7,964 |
| **Total** | **~19,878 MiB / 23,034 MiB** |

### 19.2 TTS Server Security Fixes

**PEN-005/006/007 — Speaker Allowlist Enforcement**
- Added `_ALLOWED_SPEAKERS = frozenset({"kavya"})` to `deployment/gpu/services/tts/server.py`
- Speaker field validated before model inference; HTTP 422 on unknown speaker
- Re-test verified: `kavya` → 200; `arjun`, `admin`, injection-like values → 422

**PEN-009 — Text Length Cap**
- Added `_MAX_TEXT_CHARS = 2000` to `deployment/gpu/services/tts/server.py`
- HTTP 422 `"Text too long: N chars (max 2000)"` on oversize text
- Prevents single-request GPU monopolization via long synthesis

### 19.3 Latency Run D (Intra-Server Localhost, 2026-07-12)

| Stage | p50 | p95 | p99 | Budget | Gate |
|---|---|---|---|---|---|
| STT (Whisper) | 202ms | 205ms | 542ms* | 300ms | PASS (p99 inflated by restart) |
| LLM TTFT | 549ms | 655ms | 688ms | 500ms | FAIL |
| TTS TTFA | 695ms | 699ms | 725ms | 750ms | PASS |
| **FIRST-AUDIO** | **1446ms** | **1556ms** | **1559ms** | **1500ms** | **FAIL** |

*Run D contaminated: 14 errors (TTS restart mid-test), call 27 STT=542ms (post-restart anomaly). p99 STT is contamination artifact.

Root cause of FAIL: LLM TTFT p50=549ms. When LLM spikes to 600-688ms (~15% of calls), first_audio exceeds 1500ms. At `--gpu-memory-utilization 0.45`, smaller KV cache → more cache evictions → variable TTFT.

Run E (clean, no interruptions) in progress at time of writing.

---

*This document is updated after every sprint that introduces GPU changes.*
