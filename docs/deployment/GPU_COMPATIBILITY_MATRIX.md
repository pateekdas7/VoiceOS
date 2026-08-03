# GPU Compatibility Matrix — VoiceOS v2

**Document type:** Canonical compatibility reference  
**Version:** 1.0.0  
**Established:** 2026-08-03  
**Authority:** See [GPU Runtime Specification](GPU_RUNTIME_SPEC.md) §14 for deviations.

> This matrix records which component combinations are supported, validated, or known to be incompatible. A combination not listed here has not been tested and must not be used in production without explicit validation.

---

## 1. Driver ↔ CUDA Compatibility

| NVIDIA Driver | Max CUDA | Production Config | Status |
|---|---|---|---|
| < 525.0 | < 12.1 | No | ❌ Not supported |
| 525.0 – 535.x | 12.1 | Minimum | ⚠️ Supported (not tested) |
| 545.x – 560.x | 12.6 | Compatible | ✅ Compatible |
| 570.x – 580.x | 13.0 | **580.126.20** | ✅ **Current production** |
| > 580.x | > 13.0 | Not tested | ⚠️ Unknown — test before adopting |

**Rule:** Always use a driver version that covers the installed CUDA toolkit version. The driver must be ≥ the minimum for your CUDA version.

---

## 2. CUDA ↔ PyTorch Compatibility

| CUDA Version | PyTorch Version | Wheel Variant | Status |
|---|---|---|---|
| 12.1 | 2.11.0 | `+cu121` | ✅ Compatible (not current) |
| 12.4 | 2.11.0 | `+cu124` | ✅ Compatible (not current) |
| 13.0 | 2.11.0 | `+cu130` | ✅ **Current production** |
| 13.0 | 2.x.x (other) | `+cu130` | ⚠️ Not tested — validate VRAM budget |
| < 12.1 | 2.11.0 | any | ❌ Not supported |

**Note:** PyTorch 2.11.0+cu130 is the only version validated for this GPU node. Upgrades require full VRAM budget re-measurement and latency re-validation before being adopted.

---

## 3. PyTorch ↔ vLLM Compatibility

| PyTorch Version | vLLM Version | Status |
|---|---|---|
| 2.11.0+cu130 | 0.24.0 | ✅ **Current production** |
| 2.11.0+cu130 | 0.25.x | ⚠️ Unknown — not tested |
| 2.11.0+cu130 | < 0.20.0 | ❌ Not supported |
| < 2.10.0 | 0.24.0 | ❌ Not compatible |

**Critical:** vLLM version changes affect VRAM footprint, KV cache sizing, and CUDA kernel selection. Any vLLM upgrade must be tested on a non-production node with the full VRAM budget measurement procedure before production adoption.

---

## 4. PyTorch ↔ faster-whisper Compatibility

| PyTorch Version | faster-whisper Version | CTranslate2 Version | Status |
|---|---|---|---|
| 2.11.0+cu130 | 1.2.1 | 4.8.0 | ✅ **Current production** |
| 2.11.0+cu130 | 1.0.x | 4.x | ⚠️ Untested |
| 2.11.0+cu130 | 1.3.x | 4.x | ⚠️ Untested |

**Note:** faster-whisper and CTranslate2 are tightly coupled — upgrading one requires upgrading the other per their joint release cadence. The compute type `int8_float16` must remain supported by the CTranslate2 version in use.

---

## 5. vLLM ↔ Model Compatibility

### 5.1 Qwen2.5-7B-Instruct-FP8-dynamic ↔ vLLM

| vLLM Version | Model | Quantization Method | Status |
|---|---|---|---|
| 0.24.0 | `RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic` | compressed-tensors W8A8 (auto-detect) | ✅ **Current production** |
| 0.24.0 | `Qwen/Qwen2.5-7B-Instruct-FP8` | — | ❌ Repo does not exist on HuggingFace |
| < 0.20.0 | any FP8 model | compressed-tensors | ❌ compressed-tensors not supported |

### 5.2 Veena TTS (BF16) ↔ vLLM

| vLLM Version | Model | Mode | Status |
|---|---|---|---|
| 0.24.0 | `maya-research/Veena` | AsyncLLMEngine (streaming) | ✅ **Current production** |
| 0.24.0 | `maya-research/Veena` | `AutoModelForCausalLM.generate()` (batch) | ❌ Abandoned — TTFA p95 = 8,730ms (TT-001) |
| < 0.20.0 | `maya-research/Veena` | AsyncLLMEngine | ❌ Not tested |

---

## 6. Models ↔ GPU Architecture Compatibility

| GPU Architecture | SM Version | Whisper int8_float16 | Qwen FP8 (compressed-tensors) | Veena BF16 | SNAC FP32 |
|---|---|---|---|---|---|
| Ampere | SM 8.0 (A10, A30) | ✅ Supported | ✅ Supported | ✅ Supported | ✅ Supported |
| Ampere | SM 8.6 (A10G) | ✅ Supported | ✅ Supported | ✅ Supported | ✅ Supported |
| Ada Lovelace | SM 8.9 (L4, L40) | ✅ **Validated** | ✅ **Validated** | ✅ **Validated** | ✅ **Validated** |
| Hopper | SM 9.0 (H100) | ✅ Supported | ✅ Supported | ✅ Supported | ✅ Supported |
| Volta | SM 7.0 (V100) | ⚠️ No FP8 hardware | ❌ FP8 not natively supported | ✅ Supported | ✅ Supported |
| Turing | SM 7.5 (T4) | ⚠️ Limited | ❌ No FP8 | ✅ Supported | ✅ Supported |
| < Volta | SM < 7.0 | ❌ Not supported | ❌ Not supported | ❌ Not supported | ❌ Not supported |

**Critical note on FP8:** `int8` compute type (not `fp8`) is used for Whisper via CTranslate2. True FP8 hardware acceleration on Whisper would require a different compute type. The actual `fp8` designation in the Qwen model refers to the weight quantization format loaded by vLLM, not a CTranslate2 compute type.

**Note on CTranslate2 FP8:** CTranslate2 4.8.0 does NOT support FP8 compute type on NVIDIA L4 (SM 8.9). The `int8_float16` compute type is the maximum supported by this combination.

---

## 7. GPU ↔ VRAM Requirements

| GPU Model | Architecture | VRAM | Can Run All 3 Services | Notes |
|---|---|---|---|---|
| NVIDIA L4 | Ada Lovelace SM 8.9 | 23,034 MiB | ✅ **Yes — production** | At vLLM util 0.55 only. Fits with 695 MiB free. |
| NVIDIA L40 | Ada Lovelace SM 8.9 | 46,068 MiB | ✅ Yes | Comfortable margin. vLLM util can be increased. |
| NVIDIA A10 | Ampere SM 8.6 | 24,576 MiB | ✅ Yes | Matches spec exactly. vLLM util 0.70 would work. |
| NVIDIA A10G | Ampere SM 8.6 | 24,576 MiB | ✅ Yes | Same as A10. |
| NVIDIA A100 40GB | Ampere SM 8.0 | 40,960 MiB | ✅ Yes | Well within budget. |
| NVIDIA A100 80GB | Ampere SM 8.0 | 81,920 MiB | ✅ Yes | Significant headroom. |
| NVIDIA H100 80GB | Hopper SM 9.0 | 81,920 MiB | ✅ Yes | Significant headroom. FP8 native hardware. |
| NVIDIA T4 | Turing SM 7.5 | 16,384 MiB | ❌ No | Insufficient VRAM for all 3 services together. |
| NVIDIA V100 16GB | Volta SM 7.0 | 16,384 MiB | ❌ No | Insufficient VRAM. No FP8 native support. |
| NVIDIA V100 32GB | Volta SM 7.0 | 32,768 MiB | ⚠️ Possible | Enough VRAM but no FP8 hardware — FP16 fallback needed. |
| NVIDIA RTX 3090 | Ampere SM 8.6 | 24,576 MiB | ✅ Yes | Consumer GPU — no ECC. Matches spec VRAM. |
| NVIDIA RTX 4090 | Ada Lovelace SM 8.9 | 24,576 MiB | ✅ Yes | Consumer GPU — no ECC. Matches spec VRAM. |

---

## 8. Known Incompatibilities

| Combination | Issue | Resolution |
|---|---|---|
| CTranslate2 4.8.0 + compute_type=`fp8` on SM 8.9 | FP8 compute type not supported by CTranslate2 on L4. Raises runtime error. | Use `int8_float16`. |
| vLLM + `Qwen/Qwen2.5-7B-Instruct-FP8` | Repo does not exist on HuggingFace. | Use `RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic`. |
| faster-whisper + Python 3.13 | faster-whisper 1.2.1 wheel not available for Python 3.13. | Use Python 3.12.13. |
| vLLM 0.24.0 + `--gpu-memory-utilization > 0.55` on L4 with Veena | Causes OOM — Veena 3B BF16 + SNAC occupies 7,980 MiB, leaving no room for vLLM KV cache at higher utilization. | Maximum safe value is 0.55 on the 23 GB L4 with all 3 services running. |
| Veena TTS + HuggingFace batch `generate()` | TTFA p95 = 8,730ms — 5.8× over the 1,500ms target. | Use `vllm.AsyncLLMEngine` (ADR-001). |
| systemd + plain `nohup &` for GPU services | `SIGHUP` kills service when SSH session closes. | Use systemd units (`voiceos-{stt,llm,tts}.service`). |
| Docker GPU + missing `nvidia` default runtime | CUDA not visible inside containers. | Set `default-runtime: nvidia` in `/etc/docker/daemon.json`. |
| `huggingface-cli` without full path on GPU node | CLI is in venv, not system-wide. | Always use `/opt/voiceos-gpu/venv/bin/huggingface-cli`. |

---

## 9. Dependency Version Lock

The following versions are **locked** and must not be changed without an approved ADR:

| Package | Locked Version | Lock Reason |
|---|---|---|
| `torch` | 2.11.0+cu130 | VRAM budget measured at this version |
| `vllm` | 0.24.0 | LLM TTFT, TTS TTFA, and FP8 auto-detect validated at this version |
| `faster-whisper` | 1.2.1 | STT latency validated at this version; model download path depends on 1.2.1 internals |
| `ctranslate2` | 4.8.0 | int8_float16 compute type validated at this version on SM 8.9 |
| `transformers` | 5.12.1 | Veena tokenizer validated at this version |

**Upgrade procedure:** For any locked version — submit ADR → test on isolated node → re-run full VRAM budget measurement → re-run validation suite → update this matrix → update GPU_RUNTIME_SPEC.md → merge.
