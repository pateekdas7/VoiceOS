# GOLDEN T4×2 BASELINE
## VALIDATED ON REAL 2×T4 — KAGGLE VERSION 21

**Status:** GOLDEN BASELINE  
**Validated:** 2026-08-21T11:09:16Z  
**Kernel:** devishreedas/voiceos-sprint29-validation  
**Kaggle version:** v21  
**Overall result:** ALL PASS (STT / LLM / TTS / AudioPacer / E2E)

---

## 1. Git Provenance

| Field | Value |
|-------|-------|
| Repository | https://github.com/pateekdas7/VoiceOS.git |
| Branch | `claude/ssh-gpu-cpu-servers-y99fib` |
| Commit | `1d36425165e8bc1e3e96aaf39897a873c6943233` |
| Commit message | `fix(tts): normalize speaker name to lowercase before validation` |
| Clone method | `git clone --depth 1 --branch claude/ssh-gpu-cpu-servers-y99fib` |

**Kaggle → Git chain verified:**  
Kaggle v21 clones `claude/ssh-gpu-cpu-servers-y99fib` at HEAD. HEAD at push time was `1d36425`. Local repo confirmed clean at this commit (`git status` clean, `git rev-parse HEAD` = `1d36425165e8bc1e3e96aaf39897a873c6943233`).

---

## 2. Hardware

Source: `hardware_report.txt` from Kaggle v21 runtime (2026-08-21T10:56:41Z).

| Field | Value |
|-------|-------|
| GPU count | 2 |
| GPU model | NVIDIA Tesla T4 |
| Architecture | sm_75 (Turing) |
| Compute capability | 7.5 |
| VRAM per GPU | 14,911 MB (~14.6 GiB) |
| BF16 tensor cores | **No** (sm_75 — FP32 emulation used) |
| FP16 tensor cores | Yes |
| FP8 hardware | **No** (sm_75 — Marlin kernel dequant used) |
| CUDA runtime | 12.8 |
| cuDNN | 91002 |
| PyTorch | 2.10.0+cu128 |
| Python | 3.12.13 (GCC 11.4.0) — 2026-03-04 |

GPU UUIDs were not captured in v21. They are Kaggle-assigned ephemeral identifiers.

---

## 3. GPU Allocation

```
GPU 0 → LLM  (Qwen2.5-7B-Instruct-FP8, vLLM)
           CUDA_VISIBLE_DEVICES=0

GPU 1 → STT  (Whisper large-v3-turbo, faster-whisper)
         TTS  (Veena + SNAC 24kHz)
           CUDA_VISIBLE_DEVICES=1
```

KV cache on GPU 0: 3.6 GiB available → 67,424 tokens cached.

---

## 4. Python Environment

### Pre-installed by Kaggle (at Cell 0 audit, before Cell 1 installs)

| Package | Pre-installed version |
|---------|----------------------|
| torch | 2.10.0+cu128 |
| transformers | 5.0.0 |
| tokenizers | 0.22.2 |
| huggingface_hub | 1.11.0 |
| accelerate | 1.13.0 |
| numpy | 2.0.2 |
| fastapi | 0.136.1 |
| uvicorn | 0.46.0 |
| pydantic | 2.12.3 |
| httpx | 0.28.1 |
| triton | 3.6.0 |

### Installed by Cell 1 (exact pip pins — versions at inference time)

| Package | Requested version | Notes |
|---------|-------------------|-------|
| faster-whisper | 1.2.1 | New install |
| ctranslate2 | 4.8.0 | New install |
| transformers | 5.12.1 | Upgrade from 5.0.0 |
| tokenizers | 0.22.2 | No change |
| huggingface-hub | 0.28.0 | Downgrade from 1.11.0 |
| accelerate | 1.7.0 | Downgrade from 1.13.0 |
| snac | 1.0.0 | New install (checkpoint architecture) |
| numpy | 2.3.5 | Upgrade from 2.0.2 |
| fastapi | 0.115.6 | Downgrade from 0.136.1 |
| uvicorn[standard] | 0.30.6 | Downgrade from 0.46.0 |
| pydantic | 2.10.4 | Downgrade from 2.12.3 |
| httpx | 0.27.0 | Downgrade from 0.28.1 |
| vllm | 0.24.0 | New install |

**torch is NOT reinstalled.** Kaggle's pre-installed 2.10.0+cu128 is used as-is.  
A pip freeze from inside the Kaggle runtime was not captured; versions above are the pip-requested pins from Cell 1.

---

## 5. Model Artifacts

### STT — Whisper large-v3-turbo

| Field | Value |
|-------|-------|
| HuggingFace repo | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` |
| HF revision | `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` |
| Local path | `/kaggle/working/hf/whisper/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` |
| Download method | `faster_whisper.utils.download_model('large-v3-turbo', cache_dir=...)` |
| Compute type | `int8_float16` (T4 sm_75 compatible) |
| Device | `cuda` (GPU 1) |
| Format | CTranslate2 |

### LLM — Qwen2.5-7B-Instruct-FP8

| Field | Value |
|-------|-------|
| HuggingFace repo | `RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic` |
| HF revision / snapshot | `a4f1d5442ea284bc8e5a3b3e1a4d528f7d6caacb` |
| Local path | `/kaggle/working/hf/models--RedHatAI--Qwen2.5-7B-Instruct-FP8-dynamic/snapshots/a4f1d5442ea284bc8e5a3b3e1a4d528f7d6caacb` |
| Download method | `huggingface_hub.snapshot_download`, ignoring `*.pt`, `*.gguf` |
| Quantization | compressed-tensors FP8 (dequantized via Marlin kernel on T4 — no native FP8) |
| Served name | `qwen2.5-7b-instruct-fp8` |
| Runtime dtype | `torch.float16` (resolved from `--dtype auto` on sm_75; no BF16) |
| Device | `cuda` (GPU 0) |

### TTS — Veena

| Field | Value |
|-------|-------|
| HuggingFace repo | `maya-research/Veena` |
| HF revision | `8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f` |
| Local path | HF cache under `/kaggle/working/hf` |
| Download method | `transformers.AutoModelForCausalLM.from_pretrained` (streaming load) |
| Precision | BF16 (FP32 emulation on T4 sm_75 — no BF16 tensor cores) |
| Device | `cuda` (GPU 1) |
| Speaker | `kavya` (normalised from any casing at server boundary) |

### SNAC Codec — snac_24khz

| Field | Value |
|-------|-------|
| HuggingFace repo | `hubertsiuzdak/snac_24khz` |
| HF revision | `d73ad176a12188fcf4f360ba3bf2c2fbbe8f58ec` |
| Config file | `config.json` |
| Weights file | `pytorch_model.bin` |
| Download method | `huggingface_hub.hf_hub_download` (manual, bypassing SNAC.from_pretrained) |
| Package version | `snac==1.0.0` |
| Sample rate | 24,000 Hz |
| VQ strides | [4, 2, 1] (3 codebooks) |
| Codebook size | 4096 |
| Device | `cuda` (GPU 1) |

---

## 6. SNAC Patches

Four patches are applied in `deployment/gpu/services/tts/server.py` at model load time. **Do not remove any patch.**

### Patch 1 — Manual HF download (bypass SNAC.from_pretrained)

**File:** `deployment/gpu/services/tts/server.py` (~line 577)  
**Original behaviour:** `SNAC.from_pretrained(repo_id)` downloads and loads in one call.  
**Failure:** API instability across snac package versions; also needed for fine-grained control.  
**Fix:** Use `hf_hub_download` to fetch `config.json` and `pytorch_model.bin` separately, then construct `SNAC(**config)` manually.  
**T4-specific:** No — applies on all hardware.  
**Revert impact:** Would break loading if SNAC.from_pretrained API differs.

### Patch 2 — LocalMHA strip

**File:** `deployment/gpu/services/tts/server.py` (~line 585)  
**Original behaviour:** `snac==1.0.0` `layers.py` always inserts `LocalMHA` into both `Encoder.block` and `Decoder.model` when `attn_window_size` is not `None` (default=32). `snac.py` never passes `attn_window_size` to `Encoder`/`Decoder`, so the default always fires.  
**Failure:** `load_state_dict` raises key mismatch because `snac_24khz` checkpoint was trained without attention layers.  
**Fix:** After construction, strip all `LocalMHA` modules by class name:
```python
def _strip_attn(seq):
    return torch.nn.Sequential(*[m for m in seq.children()
                                  if type(m).__name__ != "LocalMHA"])
_snac_model.encoder.block = _strip_attn(_snac_model.encoder.block)
_snac_model.decoder.model = _strip_attn(_snac_model.decoder.model)
```
Result: encoder 7→6 blocks, decoder 10→9 layers.  
**T4-specific:** No — applies on all hardware.  
**Revert impact:** `load_state_dict` fails with unexpected keys.

### Patch 3 — decode() method restoration

**File:** `deployment/gpu/services/tts/server.py` (~line 599)  
**Original behaviour:** `snac==1.0.0` removed the `SNAC.decode(codes)` method that existed in earlier versions. Only `forward(audio)` (encoder+quantizer+decoder) is present.  
**Failure:** `AttributeError: 'SNAC' object has no attribute 'decode'` at warm-up synthesis.  
**Fix:** Patch back a `decode(codes)` implementation as an instance method:
```python
def _snac_decode_compat(self, codes):
    z_q = 0
    for quantizer, code in zip(self.quantizer.quantizers, codes):
        z_q_i = quantizer.decode_code(code)       # [1, codebook_dim, T_i]
        z_q_i = quantizer.out_proj(z_q_i)         # [1, latent_dim, T_i]
        if quantizer.stride > 1:
            z_q_i = z_q_i.repeat_interleave(quantizer.stride, dim=-1)
        z_q = z_q + z_q_i
    return self.decoder(z_q)                       # [1, 1, T_audio]

_snac_model.decode = types.MethodType(_snac_decode_compat, _snac_model)
```
**T4-specific:** No — applies on all hardware.  
**Revert impact:** AttributeError at first synthesis call.

### Patch 4 — snake() JIT bypass (T4-specific)

**File:** `deployment/gpu/services/tts/server.py` (~line 616)  
**Original behaviour:** `snac/layers.py` defines `snake()` with `@torch.jit.script`, which triggers NVRTC to compile a fused CUDA kernel at first GPU execution.  
**Failure:** `RuntimeError: nvrtc: error: failed to open libnvrtc-builtins.so.13.0` — Kaggle T4 environment is missing this NVRTC shared library.  
**Fix:** Replace `snac.layers.snake` with a plain Python function before any forward pass. `Snake1d.forward()` resolves the name from `snac.layers.__dict__` at call time, so the redirect takes effect without modifying the installed package:
```python
import snac.layers as _snac_layers
def _snake_plain(x, alpha):
    shape = x.shape
    x = x.reshape(shape[0], shape[1], -1)
    x = x + (alpha + 1e-9).reciprocal() * torch.sin(alpha * x).pow(2)
    x = x.reshape(shape)
    return x
_snac_layers.snake = _snake_plain
```
**T4-specific: YES** — caused by missing `libnvrtc-builtins.so.13.0` in the Kaggle sm_75 environment.  
**Newer GPU impact:** On GPUs with NVRTC working correctly (A6000, L40S, A100), this patch is harmless — it simply uses unfused CUDA ops instead of the fused kernel. Slightly slower but functionally identical.  
**Revert impact:** Crashes at first synthesis call on environments missing libnvrtc-builtins.

---

## 7. vLLM Configuration

### Exact startup command (E2E Cell 5 — validated path)

```bash
CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
  --model /kaggle/working/hf/models--RedHatAI--Qwen2.5-7B-Instruct-FP8-dynamic/snapshots/a4f1d5442ea284bc8e5a3b3e1a4d528f7d6caacb \
  --dtype auto \
  --port 8000 \
  --host 0.0.0.0 \
  --max-model-len 512 \
  --gpu-memory-utilization 0.85 \
  --served-model-name qwen2.5-7b-instruct-fp8 \
  --trust-remote-code \
  --enforce-eager
```

### Flag classification

| Flag | Value | Classification |
|------|-------|----------------|
| `--model` | snapshot path | Normal |
| `--dtype auto` | resolves to `float16` on T4 | Normal (auto-selects best for hw) |
| `--port 8000` | 8000 | Normal |
| `--host 0.0.0.0` | 0.0.0.0 | Normal |
| `--max-model-len 512` | 512 tokens | Normal (Kaggle memory constraint) |
| `--gpu-memory-utilization 0.85` | 0.85 | Normal |
| `--served-model-name qwen2.5-7b-instruct-fp8` | string alias | Normal |
| `--trust-remote-code` | — | Normal |
| **`--enforce-eager`** | — | **T4-SPECIFIC** — CUDA graphs fail on sm_75; eager mode required |

### Resolved runtime config (from vLLM log)

| Field | Value |
|-------|-------|
| dtype | `torch.float16` (BF16 unavailable on sm_75) |
| quantization | `compressed-tensors` (FP8) |
| quantization backend | Marlin kernel (no native FP8 on sm_75) |
| kv_cache_dtype | `auto` |
| tensor_parallel_size | 1 |
| enforce_eager | `True` |
| enable_prefix_caching | `True` |
| enable_chunked_prefill | `True` |
| Available KV cache | 3.6 GiB |
| KV cache tokens | 67,424 |
| Throughput (observed) | 3.1 tok/s prompt, 2.7 tok/s generation |

**Important:** `vllm` is **never imported in the Kaggle kernel process** — always started as a subprocess. Importing vllm in the notebook kernel causes a CUDA segfault.

---

## 8. STT Configuration

### Startup command

```bash
CUDA_VISIBLE_DEVICES=1 python deployment/gpu/services/stt/server.py \
  --model-path /kaggle/working/hf/whisper/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf \
  --compute-type int8_float16 \
  --device cuda \
  --port 8100 \
  --host 0.0.0.0
```

### Configuration

| Field | Value | Notes |
|-------|-------|-------|
| model | large-v3-turbo | |
| compute_type | `int8_float16` | T4 sm_75 compatible (no BF16 required) |
| device | cuda (GPU 1) | |
| port | 8100 | |
| health endpoint | `GET /health/ready` | |
| transcribe endpoint | `POST /transcribe` | |
| load time | 18,638 ms | |
| warmup | 779 ms (0.5s silence) | |
| observed latency | 494 ms (2s audio, lang=hi) | |

---

## 9. TTS Configuration

### Startup command

```bash
CUDA_VISIBLE_DEVICES=1 python deployment/gpu/services/tts/server.py \
  --model-path maya-research/Veena \
  --snac-path hubertsiuzdak/snac_24khz \
  --device cuda \
  --port 8200 \
  --host 0.0.0.0
```

### Configuration

| Field | Value | Notes |
|-------|-------|-------|
| Veena model | `maya-research/Veena` | |
| SNAC codec | `hubertsiuzdak/snac_24khz` | |
| device | cuda (GPU 1) | |
| port | 8200 | |
| health endpoint | `GET /health/ready` | |
| synthesize endpoint | `POST /synthesize` | |
| output format | PCM16LE, 24 kHz, mono | |
| chunk size | 4,096 bytes = 2,048 samples = 85.33 ms | |
| speaker validation | case-insensitive (`.lower()`) | `'Kavya'` and `'kavya'` both accepted |
| allowed speakers | `{"kavya"}` | |
| max text | 2,000 chars | |
| load time | 21,410 ms | |
| warm-up | 3,490 ms (synthesis of "hello") | |
| TTFA (32 chars) | 1,160 ms | |
| Total synthesis (32 chars) | 10,855 ms | |

### Environment variables (TTS service)

| Variable | Default | Description |
|----------|---------|-------------|
| `VEENA_MODEL_PATH` | `maya-research/Veena` | Override Veena HF repo |
| `SNAC_MODEL_PATH` | `hubertsiuzdak/snac_24khz` | Override SNAC HF repo |
| `TTS_DEVICE` | `cuda` | Device |
| `TTS_SERVICE_PORT` | `8200` | Port |
| `TTS_MOCK` | `false` | Mock mode |

---

## 10. AudioPacer Configuration

| Field | Value |
|-------|-------|
| Input | PCM16LE, 24,000 Hz, mono |
| Output | G.711 μ-law, 8,000 Hz, mono |
| Frame size | 20 ms = 160 bytes (Twilio Media Streams standard) |
| Source samples per frame | 480 samples at 24 kHz |
| Downsampling | 3:1 decimation (24 kHz → 8 kHz) |
| Underrun behaviour | Insert silence frame + increment underrun counter |
| Cancellation | `cancel()` causes `drain_frame()` to return `None` |
| Thread safety | `feed()` and `drain_frame()` are thread-safe |

---

## 11. Environment Variables

### A. Required production variables

| Variable | Value in v21 | Description |
|----------|-------------|-------------|
| `HF_HOME` | `/kaggle/working/hf` | HuggingFace cache root |
| `CUDA_VISIBLE_DEVICES` | `0` (LLM) / `1` (STT+TTS) | Per-process GPU assignment |

### B. T4-specific variables

None — T4 workarounds are code-level (Patch 4) and flag-level (`--enforce-eager`), not environment variables.

### C. Service ports

| Variable | Default | Service |
|----------|---------|---------|
| `TTS_SERVICE_PORT` | 8200 | TTS server |
| `STT_SERVICE_PORT` | 8100 | STT server |
| LLM port | 8000 | vLLM (hardcoded in notebook) |

### D. Secrets

| Variable | Status |
|----------|--------|
| `HF_TOKEN` | SECRET — NOT CAPTURED (unauthenticated HF access used in v21; rate-limited) |
| Kaggle credentials | SECRET — NOT CAPTURED |

---

## 12. Startup Procedure

Exact order used by Kaggle v21 Cell 5 (E2E):

```
1. Set HF_HOME=/kaggle/working/hf

2. Start LLM subprocess (GPU 0):
   CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server [flags]
   Log: /kaggle/working/e2e_llm.log

3. Start STT subprocess (GPU 1):
   CUDA_VISIBLE_DEVICES=1 python deployment/gpu/services/stt/server.py [flags]
   Log: /kaggle/working/e2e_stt.log

4. Start TTS subprocess (GPU 1):
   CUDA_VISIBLE_DEVICES=1 python deployment/gpu/services/tts/server.py [flags]
   Log: /kaggle/working/e2e_tts.log

5. Poll STT  http://localhost:8100/health/ready  (timeout=120s)
6. Poll TTS  http://localhost:8200/health/ready  (timeout=700s)
7. Poll LLM  http://localhost:8000/health        (timeout=300s)

8. Run E2E: audio → POST /transcribe → POST /v1/chat/completions → POST /synthesize

9. Run AudioPacer: feed TTS chunks → drain_all_frames()

10. Shutdown: terminate all subprocesses
```

**Note on TTS startup time:** TTS takes longest (~22s model load + ~4s warm-up synthesis). The 700s poll timeout accounts for first-run HF model download.

---

## 13. Validation Results (REAL GPU)

All measurements from Kaggle v21, 2026-08-21.

### STT

| Metric | Value |
|--------|-------|
| Label | REAL — KAGGLE 2×T4 |
| Model | large-v3-turbo (mobiuslabsgmbh) |
| HF revision | 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf |
| Compute type | int8_float16 |
| GPU | GPU 1 |
| Load time | 18,638 ms |
| Warmup | 779 ms |
| E2E transcription latency | 494 ms |
| Language detected | hi (Hindi) |
| Result | **PASS** |

### LLM

| Metric | Value |
|--------|-------|
| Label | REAL — KAGGLE 2×T4 |
| Model | Qwen2.5-7B-Instruct-FP8 (RedHatAI) |
| HF revision | a4f1d5442ea284bc8e5a3b3e1a4d528f7d6caacb |
| vLLM version | 0.24.0 |
| Runtime dtype | float16 (FP32 emulation path for FP8 via Marlin) |
| GPU | GPU 0 |
| KV cache | 3.6 GiB / 67,424 tokens |
| Prompt throughput | 3.1 tokens/s |
| Generation throughput | 2.7 tokens/s |
| HTTP health | GET /health → 200 OK |
| Inference | POST /v1/chat/completions → 200 OK |
| Result | **PASS** |

### TTS

| Metric | Value |
|--------|-------|
| Label | REAL — KAGGLE 2×T4 |
| Model | Veena (maya-research) |
| HF revision | 8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f |
| SNAC | snac_24khz @ d73ad176a12188fcf4f360ba3bf2c2fbbe8f58ec |
| GPU | GPU 1 |
| Load time | 21,410 ms |
| Warm-up synthesis | 3,490 ms |
| TTFA (32-char input) | 1,160 ms |
| Total synthesis (32 chars) | 10,855 ms |
| Output format | PCM16LE 24 kHz mono |
| Chunk size | 4,096 bytes |
| Health | GET /health/ready → 200 OK |
| Synthesis | POST /synthesize → 200 OK (streaming) |
| Speaker normalisation | `.lower()` applied — `'Kavya'` accepted |
| Result | **PASS** |

### AudioPacer

| Metric | Value |
|--------|-------|
| Input | PCM16LE chunks from TTS (4,096 bytes each) |
| Output | G.711 μ-law 20 ms frames (160 bytes each) |
| Conversion | 24 kHz → 8 kHz decimation |
| Result | **PASS** |

### E2E Pipeline

| Metric | Value |
|--------|-------|
| Chain | sine audio → STT (hi) → LLM (Qwen) → TTS (Veena) → AudioPacer |
| All services healthy | Yes |
| Full chain executed | Yes |
| Result | **PASS** |

---

## 14. T4-Specific Workarounds Summary

| # | Workaround | Location | Reason | Needed on newer GPU? |
|---|-----------|----------|--------|---------------------|
| 1 | `--enforce-eager` in vLLM | Cell 5 notebook | CUDA graphs fail on sm_75 | No (A6000/L40S have working CUDA graphs) |
| 2 | `--dtype auto` resolves to `float16` | vLLM runtime | No BF16 tensor cores on sm_75 | Resolves to bfloat16 on sm_80+ |
| 3 | Marlin kernel for FP8 | vLLM (automatic) | No native FP8 on sm_75 | Not needed on sm_89+ (H100/L40S) |
| 4 | snake() JIT bypass (Patch 4) | `server.py` ~line 616 | Missing `libnvrtc-builtins.so.13.0` in Kaggle T4 env | Patch is harmless on newer GPUs; can optionally be removed |
| 5 | Veena BF16 via FP32 emulation | torch runtime (automatic) | No BF16 tensor cores on sm_75 | Not needed on sm_80+ |
| 6 | 700s TTS health poll timeout | Cell 5 notebook | First-run model download takes time | Reducible after cache warm |

---

## 15. Known Limitations

- LLM throughput is low (3.1 tok/s prompt, 2.7 tok/s generation) — expected on T4 with FP8 via Marlin dequant; not a bug.
- TTS TTFA is 1,160 ms — Veena BF16 on T4 FP32 emulation is ~2–3× slower than on sm_80+.
- Total TTS synthesis (10,855 ms for 32 chars) reflects T4 inference speed; not suitable for real-time calls without streaming.
- vLLM runs with `max-model-len=512` — sufficient for validation but not for long conversations.
- Unauthenticated HF access (no HF_TOKEN) — rate-limited; may fail under heavy concurrent usage.
- `pip freeze` from inside Kaggle was not captured — exact resolved dependency versions are pip-requested pins, not confirmed resolved versions.

---

## 16. Reproduction Procedure

To reproduce the exact v21 environment:

```bash
# 1. Kaggle kernel
# Kernel: devishreedas/voiceos-sprint29-validation
# Push version 21 is the validated baseline

# 2. Git checkout
git clone --depth 1 \
  --branch claude/ssh-gpu-cpu-servers-y99fib \
  https://github.com/pateekdas7/VoiceOS.git
cd VoiceOS
git checkout 1d36425165e8bc1e3e96aaf39897a873c6943233

# 3. Run Kaggle notebook
# Open devishreedas/voiceos-sprint29-validation
# Select accelerator: NvidiaTeslaT4x2
# Run all cells in order (0 → 1 → 2 → 3 → 4 → 5 → 6 → 7)
# Expected: FINAL OVERALL: PASS in Cell 7
```

---

## 17. Rollback Reference

If future versions break, return to this state:

- **Git commit:** `1d36425165e8bc1e3e96aaf39897a873c6943233`
- **Branch:** `claude/ssh-gpu-cpu-servers-y99fib`
- **Kaggle version:** 21
- **Key file:** `deployment/gpu/services/tts/server.py` — contains all 4 SNAC patches
- **Key notebook:** `deployment/gpu/kaggle/voiceos_gpu_validation.ipynb`
