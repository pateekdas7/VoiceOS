# PART B — HISTORICAL L4 RECONSTRUCTION (from Git)

Reference commit: `1ece78b` "Sprint-028: latency gate PASS (Run F, 995ms p95) + pre-Sprint-029 audit fixes" (Sun Jul 12 2026 23:11 IST). Source file `/deployment/gpu/services/tts/server.py` (529 lines) archived at `part_b_l4/tts_server_1ece78b.py`.

Every item is labelled **PROVEN** (visible in the commit), **INFERRED** (deduced from surrounding commits/docs but not directly present), or **UNKNOWN** (no archival evidence).

## Server implementation — PROVEN
- Framework: FastAPI + uvicorn, StreamingResponse
- Endpoint: `POST /synthesize`, streams float32 LE PCM 24 kHz (raw, not PCM16LE — L4 serves raw float32; PCM16LE conversion is a Sprint-29 addition, commit 182fea6)
- Sliding window: 21 tokens (3 super-frames), yield middle frame — same as current
- Chunk size out: 8192 bytes (2048 float32 samples = 85.33 ms) — **twice** current PCM16LE 4096 B
- Warm-up call before ready: `_stream_synthesis_sync("hello", "kavya", VoiceConfigRequest())`

## Model loading — PROVEN
```python
_tokenizer = AutoTokenizer.from_pretrained("maya-research/Veena")
_model = AutoModelForCausalLM.from_pretrained(
    "maya-research/Veena",
    torch_dtype=torch.bfloat16,
    device_map=_device,             # _device = "cuda"  (single GPU)
)
_model.eval()
_snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to(_device)
_snac_model.eval()
```
- Neither model has a revision pin — resolved to HEAD of each repo at whatever wall-clock time L4 launched.
- device_map="cuda" (single GPU). L4 has 24 GB VRAM; Veena BF16 fits comfortably.

## Generation parameters — PROVEN
```python
_model.generate(
    input_ids,
    max_new_tokens=max_new,          # min(len(text)*1.3*7+21, 700)
    do_sample=True,
    temperature=0.4,
    top_p=0.9,
    repetition_penalty=1.05,
    pad_token_id=_tokenizer.pad_token_id or 128258,
    eos_token_id=[128258, 128262],
    streamer=streamer,
)
```
- **No RNG seed is set** anywhere in the module. Global CUDA + CPU RNG state is whatever the process boot + warm-up call left behind, and drifts with every subsequent call.
- No LogitsProcessor.
- No `logits_bias`, no speaker lock, no token repetition workaround.

## Prompt construction — PROVEN
```python
prompt = f"<spk_{speaker}> {text}"
prompt_ids = _tokenizer.encode(prompt, add_special_tokens=False)
input_ids_list = [128259, *prompt_ids, 128260, 128261, 128257]
```
Identical to the current T4 code path.

## Speaker whitelist — PROVEN
`_ALLOWED_SPEAKERS = frozenset({"kavya"})` — only kavya was ever loaded on L4.

## SNAC decoding — PROVEN
- Standard `SNAC.from_pretrained("hubertsiuzdak/snac_24khz").to("cuda")` — NO patches.
- No LocalMHA stripping (commit 6a4ddb9 is AFTER 1ece78b).
- No `attn_window_size=None` override (commit c76bdba is AFTER 1ece78b).
- No snake Python fallback (commit 8f79f10 is AFTER 1ece78b — L4 uses CUDA/NVRTC snake).
- `snac==0.1.0` (initial), later `snac==1.0.0` after commit 080eac6 — **which snac version L4 actually ran depends on the pip-install time; UNKNOWN without a preserved `pip freeze`**.

## Runtime dependencies — INFERRED / UNKNOWN
- torch: UNKNOWN at time of L4 Run F. Sprint-029 Phase-3 spec froze A6000 prod at `torch 2.11.0`; L4 preceded that — could have been any 2.x. **UNKNOWN**.
- transformers: UNKNOWN at L4 Run F. Sprint-029 spec pins A6000 at `transformers 5.12.1`; L4 preceded that. **UNKNOWN**.
- CUDA runtime: UNKNOWN. Sprint-029 GPU_NODE_STATE.md audit note says CUDA `cu128` was current at Sprint-029 Phase-2. L4 preceded that. **UNKNOWN**.
- SNAC: 0.1.0 or 1.0.0 — **UNKNOWN**.
- Attention impl: PyTorch default (SDPA). FlashAttention presence on L4 image at that date — **UNKNOWN**.
- TF32 setting: NOT touched in code. Default was True on Ampere/Ada. On SM 8.9 (L4) FP32 matmul used TF32 tensor cores by default → **effectively lower precision than the current T4 BF16-in-FP32-emulation path in some regions of the network**. INFERRED.
- Determinism / cuDNN benchmark: NOT touched. INFERRED default (non-deterministic, benchmark ON).

## L4 host / server context — PROVEN from commit body
- Sprint-028 Run F: GPU node **217.18.55.122**, fresh server, 2026-07-12
- Latency: first_audio p95 = 995 ms (gate ≤1500 ms)
- LLM TTFT p50/p95 = 74/98 ms (Qwen via vLLM — irrelevant to TTS drift)
- STT p50 = 204 ms, TTS p50 = 695 ms
- gpu_memory_utilization = 0.45 (LLM; not TTS)

## L4 GPU identity — INFERRED
The commit does not explicitly state "L4" but Sprint-028 spec directory (`deployment/gpu/framework/runtime_spec.yaml`) at that era shows L4 as the runtime target. Confirmation would require reading that yaml at commit 1ece78b^ (INFERRED, not yet retrieved).

## Delta vs current T4 code (server.py only) — PROVEN
Only three functional differences exist in the Python source:
1. **Per-text CUDA seed** added by Sprint-29 patch (`torch.manual_seed(hash(text)&…)` + `torch.cuda.manual_seed_all(…)`) — L4 has none.
2. **Optional Speaker-Lock LogitsProcessor** — off by default (`lock_tokens=0`), so identical behaviour when unused.
3. **SNAC patches** (LocalMHA strip, snake fallback, decode re-injection, attn_window_size=None) — added because SNAC 1.0.0 requires them on the T4 Kaggle image; L4 never applied them.

The **generation call, sampler, prompt string, model+tokenizer+SNAC identifiers, dtype, device_map, EOS, pad-id, sliding-window, chunk-selection** are all bitwise-identical to L4.

## What CANNOT be reproduced (be honest)
- Exact snac version at L4 Run F — UNKNOWN
- Exact transformers/torch/CUDA versions at L4 Run F — UNKNOWN
- Attention backend actually selected by that transformers version on that L4 image — UNKNOWN
- Whether L4 had NVRTC / triton available (which would use the CUDA snake path) — INFERRED yes
- Whether TF32 was enabled at run time — INFERRED default (yes, on Ada)
- Presence/absence of any call recordings, F0 traces or per-utterance logs from L4-era operation — **NONE archived** (see PART K)

## Observational L4 claim (user memory)
> "During earlier L4 operation, I observed Kavya calls using the female voice without the female→male switching we are seeing now."

This is treated throughout the forensic as **observational evidence**, not archival proof. No F0 traces, no audio files, no transcripts from that period are in the repo.
