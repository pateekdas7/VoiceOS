# PART A — CURRENT T4 BASELINE FINGERPRINT

Captured from Kaggle kernel `mamatadas7777/voiceos-sprint29-validation` v10 startup logs (2026-08-27 10:14 UTC). All items PROVEN unless noted.

## Hardware
- GPU: 2 × NVIDIA Tesla T4
- Architecture: Turing, SM 7.5
- VRAM per GPU: 14.56 GB (14 911 MiB)
- BF16 tensor cores: **absent** — BF16 runs in FP32 emulation on SM 7.5
- FP16 tensor cores: present
- NVIDIA driver: 580.159.04 (CUDA driver 13.0)
- Host RAM: 31 GiB, disk 20 GB in /kaggle/working

## Runtime / stack
- Python: 3.12.13
- PyTorch: 2.10.0+cu128 (CUDA runtime 12.8)
- cuDNN: 9.10.02 (91002)
- transformers: 5.12.1
- tokenizers: 0.22.2
- huggingface_hub: 0.28.0
- accelerate: 1.7.0
- snac: 1.0.0
- faster_whisper: 1.2.1 / ctranslate2: 4.8.0 (STT — unrelated to drift)
- numpy: 2.3.5
- fastapi 0.115.6, uvicorn 0.30.6, pydantic 2.10.4, httpx (import) 0.28.1
- vLLM: 0.24.0 (irrelevant to TTS drift)

## Model / tokenizer
- Veena model: `maya-research/Veena` — **no revision pin**; effective commit `8b770f9e69e6b35ef320d4cd70a99a4ab6dd022f` (from prior X4-A/X4-B forensic reports)
- Tokenizer: `AutoTokenizer.from_pretrained("maya-research/Veena")` — no revision pin
- SNAC codec: `hubertsiuzdak/snac_24khz` — no revision pin
- Model dtype at load: `torch.bfloat16`
- Attention impl: transformers default (SDPA); FlashAttention NOT installed on Kaggle T4 base image
- device_map at Sprint29 serve: `cuda` (single GPU 1 — via `CUDA_VISIBLE_DEVICES=1`)

## TF32 / determinism / kernel selection
- `torch.backends.cuda.matmul.allow_tf32` : default (True on Ampere+, no-op on T4 Turing)
- `torch.set_float32_matmul_precision` : NOT set (default `'highest'`)
- `torch.use_deterministic_algorithms` : NOT set
- cuDNN benchmark / deterministic flags : NOT overridden
- BF16 path on T4 → decomposes into FP32 kernels (SM 7.5 has no BF16 tensor cores)

## Generation parameters (Sprint-29 patched server.py)
- max_new_tokens = `min(int(len(text)*1.3)*7 + 21, 700)`
- do_sample: True
- temperature: 0.4
- top_p: 0.9
- repetition_penalty: 1.05
- pad_token_id: `_tokenizer.pad_token_id or 128258`
- eos_token_id: `[128258, 128262]`  (END_OF_SPEECH, END_OF_AI)
- logits_processor: None by default (Speaker-Lock disabled: `lock_tokens=0`)
- **Per-text CUDA seed** — `_vd_seed = hash(text) & 0x7FFFFFFFFFFFFFFF; torch.manual_seed(_vd_seed); torch.cuda.manual_seed_all(_vd_seed)` (added by Sprint-29 patch; L4 code had no seed)
- Warm-up on load: `_stream_synthesis_sync("hello", "kavya", VoiceConfigRequest())`

## Prompt construction
```python
prompt = f"<spk_{speaker}> {text}"          # e.g. "<spk_kavya> Namaste"
prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
input_ids_list = [128259, *prompt_ids, 128260, 128261, 128257]
                 # SOH,           EOH,   SOA,    SOS
```
IMPORTANT: `<spk_kavya>` is passed as a raw string. Its tokenization depends on whether the Veena tokenizer registers `<spk_kavya>` as an added-special-token. This is the same string-based construction used by the L4 server (commit 1ece78b).

## SNAC decode configuration
- Sliding window: 21 tokens (3 super-frames), yield middle frame [2048:4096] → 85.33 ms chunks
- Codebook clamp: `min(max(v, 0), 4095)` per position
- On T4 the SNAC model receives **three runtime patches** required to load at all:
  1. `attn_window_size=None` at from_pretrained (commit c76bdba)
  2. LocalMHA stripped after construction (commits 6a4ddb9, 289d177)
  3. `snac.layers.snake` replaced with Python fallback (no NVRTC on Kaggle T4 image — commit 8f79f10)
- SNAC.decode() is re-injected because SNAC 1.0.0's exported class was missing it (289d177)

## Git identity of serving code
- Repo: `github.com/pateekdas7/VoiceOS` branch `claude/ssh-gpu-cpu-servers-y99fib`
- Cloned HEAD: `02d1f3b100d787d1e58b15c7a9162deacad2e446` ("fix(phase-5): replace tunnel cell with CELL SERVE that restarts GPU services")
- server.py source commit line: same tree; then patched in-place by Cell 4 to add per-text seed + LogitsProcessor (patch marker: `VOICE_DRIFT_FIX_SPEAKER_LOCK_SWEEP_APPLIED`)

## Live-observed T4 behaviour
- TTS ready in 51 s (Veena + SNAC load)
- Hindi Kavya greeting: TTFA 1046 ms, 79 chunks, 6806 ms of audio, 0 underruns
- All chunks PCM16LE valid; no float32 bug
- **Drift signature (from prior X4-A/X4-B on this same env)**: 6/19 Hindi/Hinglish texts drift female→male; deterministic per (text, seed=hash(text)); FP32 does not remove drift.
