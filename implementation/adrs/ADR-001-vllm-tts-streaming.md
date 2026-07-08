# ADR-001 — Replace HuggingFace Batch TTS Inference with vLLM Streaming

**Status:** ✅ Approved  
**Date:** 2026-07-03  
**Sprint:** Sprint-012 (Phase 3 — serving layer fix)  
**Scope:** TTS serving layer only (`deployment/gpu/services/tts/server.py`, `src/services/tts/adapters/veena_adapter.py`)  
**Model:** Veena AI FP16 — **retained, no change**

---

## 1. Problem Statement

Sprint-012 Phase 2 measured a first-audio latency (TTFA) p95 of **8,730ms** against a 1,500ms target. The previous session incorrectly classified this as an inherent Veena model limitation (TT-001). A deep technical investigation on 2026-07-03 overturned that conclusion.

The actual bottleneck is the **TTS serving layer implementation**, not the model:

- `deployment/gpu/services/tts/server.py` uses `AutoModelForCausalLM.generate()` — a HuggingFace batch API that generates **all** SNAC tokens before returning any audio.
- The endpoint returns `Response(content=audio_bytes)` — a fully buffered response that transmits nothing until synthesis is complete.
- Result: the client (`VeenaAdapter`) blocks for the entire synthesis duration (5–11s per clause) before receiving the first byte of audio.

---

## 2. Investigation Summary

### 2.1 SNAC 24kHz Codec Analysis

The SNAC 24kHz model (`hubertsiuzdak/snac_24khz`) has the following properties, verified from source:

```json
{ "attn_window_size": null, "vq_strides": [4, 2, 1], "encoder_rates": [2, 4, 8, 8] }
```

- `attn_window_size: null` → **no attention mechanism anywhere** — the model is entirely convolutional
- `decode()` method is **stateless** — no recurrence, no hidden state, no cross-frame dependency
- Frame independence: one 7-token super-frame = 2048 audio samples = **85.33ms of audio** at 24kHz
- `decode()` accepts any number of frames; no minimum enforced in the API
- The decoder uses non-causal (symmetric) `Conv1d` — boundary artifacts at chunk edges only

### 2.2 Sliding-Window Decode Pattern

Because the convolutional decoder introduces boundary artifacts at chunk edges, the proven technique (from both reference implementations) is:

1. Accumulate **28 tokens** (4 super-frames) before first decode
2. Every 7 new tokens: call `snac.decode()` on the **last 28 tokens** → produces 8,192 samples
3. Extract **only** `audio[2048:4096]` (85.33ms = the middle super-frame)
4. The outer 3 frames provide CNN context; their boundary artifacts are discarded

This is the canonical sliding-window SNAC decode pattern, confirmed in two independent production codebases.

### 2.3 Reference Implementations

**maya1 (official Maya Research):**
- URL: `https://huggingface.co/maya-research/maya1/blob/main/vllm_streaming_inference.py`
- License: Apache 2.0
- Architecture: identical SNAC + Llama to Veena (same token constants: `CODE_TOKEN_OFFSET=128266`, `SNAC_TOKENS_PER_FRAME=7`)
- Engine: `vllm.AsyncLLMEngine`
- Streaming: `async for request_output in engine.generate()` → `request_output.outputs[0].token_ids`
- SNAC decode: 28-token sliding window, `audio[2048:4096]` extraction
- Optimizations: custom logits processor (restrict to SNAC range), `min_tokens=28`, APC for speaker prefixes

**Orpheus-TTS (canopyai):**
- URL: `https://github.com/canopyai/Orpheus-TTS`
- Architecture: identical SNAC + Llama 3B
- Engine: `vllm.AsyncLLMEngine` (via `AsyncEngineArgs`)
- Decoder: `decoder.py` — 28-token buffer, every 7 tokens yields `audio_hat[:, :, 2048:4096]`
- Streaming server: Flask `Response(generate_audio_stream(), mimetype='audio/wav')`
- Advertised latency: **~200ms streaming**, reducible to **~100ms**

### 2.4 TextIteratorStreamer Assessment

`HuggingFace TextIteratorStreamer` was evaluated and rejected:

- Yields decoded **text strings** (e.g., `"<custom_token_128290>"`) not integer token IDs
- Requires string parsing to recover integer IDs (`int(number_str) - 10 - ((index % 7) * 4096)`)
- Both reference implementations independently chose vLLM because it yields raw integer token IDs directly via `request_output.outputs[0].token_ids`
- `TextIteratorStreamer` runs in a separate thread; vLLM `AsyncLLMEngine` is fully async — better integration with FastAPI async context

### 2.5 GPU Performance Estimate (L4)

| GPU | Memory BW | Est. SNAC tok/s | Time for 28 tok | TTFA estimate |
|---|---|---|---|---|
| H100 SXM5 | 3,350 GB/s | ~21,000 | ~1.3ms | < 80ms |
| A100-40GB | 2,000 GB/s | ~12,500 | ~2.2ms | ~120ms |
| RTX 4090 | 1,008 GB/s | ~6,300 | ~4.4ms | ~200ms |
| **L4 (ours)** | **300 GB/s** | **~1,880** | **~14.9ms** | **~100–300ms** |

The advertised Veena "sub-80ms" claim refers to TTFA with vLLM streaming on H100 — consistent with hardware bandwidth ratios. Our L4 estimate of 100–300ms is comfortably within the 1,500ms target.

---

## 3. Alternatives Considered

### Option A: HuggingFace TextIteratorStreamer + StreamingResponse

- Replaces `model.generate()` with `TextIteratorStreamer` in a background thread
- Yields decoded text strings, requires parsing back to integer IDs
- Complex thread management (thread + queue + asyncio bridge)
- Not used by either reference implementation
- **Rejected:** awkward string-to-int conversion; reference implementations chose vLLM for good reasons

### Option B: vLLM AsyncLLMEngine + StreamingResponse (Selected)

- Replaces HF `AutoModelForCausalLM` with `vllm.AsyncLLMEngine`
- Native async token streaming: integer token IDs delivered directly
- Industry-proven for this exact architecture (maya1, Orpheus-TTS)
- PagedAttention for efficient KV cache management
- Continuous batching (enables future multi-call concurrency without queue stalls)
- **Selected:** matches official reference; yields raw token IDs; fully async; production-proven

### Option C: Replace Veena with Different TTS Model

- Options: Parler-TTS, StyleTTS2, Kokoro
- Would require new model download, new SNAC decoder configuration
- Disruptive to the approved model lock
- **Rejected:** the problem is the serving layer, not the model. Veena AI FP16 is retained.

### Option D: Increase Hardware (L40S or H100)

- Better GPU would reduce synthesis time
- Does not fix the batch-vs-streaming architectural problem
- TTFA with batch synthesis scales with clause length regardless of hardware speed
- **Rejected:** wrong fix for the identified root cause

---

## 4. Trade-offs

| Concern | Impact | Mitigation |
|---|---|---|
| vLLM is an additional dependency | Medium | vLLM already installed on GPU node (Qwen2.5-7B uses it); no new package installation |
| vLLM replaces HF `AutoModelForCausalLM` | Medium | Server.py is a deployment asset, not application code; swap is contained to one file |
| VeenaAdapter must read chunked HTTP | Low | Standard `httpx` streaming client; well-documented pattern |
| Custom logits processor added | Low | Standard vLLM extension point; reduces hallucination risk |
| SNAC sliding-window decode slightly more complex | Low | Proven algorithm from two production codebases; well-tested |
| Rollback requires reverting server.py | Low | Previous server.py preserved in git; one-file rollback |

---

## 5. Why vLLM AsyncLLMEngine Was Selected

1. **Evidence:** Both official Maya Research (maya1) and Orpheus-TTS independently selected vLLM for identical architecture
2. **Token ID delivery:** yields `request_output.outputs[0].token_ids` (integers) directly — no string parsing required
3. **Already installed:** the GPU node runs vLLM for Qwen2.5-7B; no new dependency
4. **Async-native:** integrates cleanly with FastAPI's async context; no threading bridge
5. **Additional capabilities:** PagedAttention, continuous batching, APC — benefits future multi-call concurrency
6. **Vocabulary restriction:** custom logits processor restricts generation to SNAC range `[128266..156937]`, eliminating hallucination in audio generation phase

---

## 6. Why HuggingFace Batch Inference Is Insufficient

- `model.generate()` is a synchronous batch API: it does not return until all tokens are generated
- The SNAC decode happens entirely after generation completes — no incremental decoding is possible
- `Response(content=audio_bytes)` transmits a `Content-Length` response — the entire body is buffered server-side before any byte is transmitted to the client
- The result is that the client (VeenaAdapter) blocks for the full synthesis duration regardless of clause length
- RTF=2.4× means a 2-second audio clause takes 4.8s to synthesize — the client blocks for 4.8s before receiving the first byte
- With streaming, the first 85ms of audio arrives at the client after only 28 tokens are generated (~14.9ms at L4 throughput + prefill overhead), not after all tokens

---

## 7. Risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| vLLM version conflict with existing Qwen service | Low | Medium | vLLM already running on GPU node; same version used for both models |
| Custom logits processor excludes valid tokens | Low | Low | SNAC token range is fixed and documented; same range as in Veena tokenizer config |
| Sliding-window decode introduces audio artifacts | Low | Low | 4-frame context on each side suppresses CNN boundary effects; proven in production |
| TTFA exceeds 300ms under L4 load | Low | Low | Still well within 1,500ms target; worst-case estimate is 300ms |
| SNAC model incompatibility with chunked tokens | Very Low | Medium | SNAC 24kHz decode() is stateless; proven in SNAC source code analysis |
| GPU VRAM increase from vLLM paged attention | Low | Low | vLLM PagedAttention is more memory-efficient than HF generate() with full KV cache |

---

## 8. Rollback Plan

1. `git revert` the `server.py` change → restores HF batch inference (server deployed from git)
2. `git revert` the `veena_adapter.py` change → restores `resp.content` buffered pattern
3. Re-deploy: `scp` updated server.py to GPU node → restart Veena service via `setsid`
4. Result: system returns to Phase 2 state (8,730ms p95) — pipeline still functional, slower

---

## 9. Migration Plan

### GPU Node Changes (`deployment/gpu/services/tts/server.py`)

1. Replace `AutoModelForCausalLM` with `vllm.AsyncLLMEngine` initialized with `AsyncEngineArgs`
2. Add `OnlyAudioAfterSOS` custom logits processor — restricts vocab to SNAC range after `START_OF_SPEECH_TOKEN`
3. Replace `_synthesize()` batch function with `_stream_synthesis()` async generator:
   - `async for request_output in engine.generate()`: collect new integer token IDs
   - Filter: keep only tokens in `[AUDIO_CODE_BASE_OFFSET .. AUDIO_CODE_BASE_OFFSET + 7*4096)`
   - Accumulate in rolling buffer; every 7 tokens when `len(buffer) >= 28`: sliding-window SNAC decode → yield `audio[2048:4096]` as `bytes`
4. Replace `/synthesize` endpoint: `return StreamingResponse(_stream_synthesis(...), media_type="application/octet-stream")`
5. Retain health endpoints, speaker/voice_config handling, startup model loading structure

### CPU Node Changes (`src/services/tts/adapters/veena_adapter.py`)

1. Replace `resp.content` with `async with client.stream("POST", ...) as resp:` + `async for chunk in resp.aiter_bytes()`
2. Reassemble streamed PCM chunks per clause into `AudioClause` objects, or yield sub-clause `AudioClause` objects per 85ms chunk for lower latency

### Deployment

1. Sync updated `server.py` to GPU node: `rsync -e "ssh -i ~/.ssh/temporary.pem" deployment/gpu/services/tts/server.py ubuntu@<GPU_IP>:/opt/voiceos-gpu/services/tts/`
2. Restart Veena service: `setsid python3 /opt/voiceos-gpu/services/tts/server.py --model-path /opt/voiceos-gpu/models/veena-fp16 --port 8200 &`
3. Verify: `GET /health/ready` returns 200
4. Validate: streaming test confirms `Transfer-Encoding: chunked` and incremental chunk delivery

---

## 10. Expected Latency Improvements

| Metric | Phase 2 (Batch) | Phase 3 (Streaming) Target |
|---|---|---|
| TTFA p95 | 8,730ms | 100–300ms |
| First audio chunk | After full synthesis (5–11s) | After 28 SNAC tokens (~15ms + prefill) |
| Streaming cadence | N/A (batch) | 85.33ms per chunk (7 new tokens) |
| Meets 1,500ms gate | ❌ No | ✅ Yes |
| LLM TTFT | ~200-300ms ✓ | ~200-300ms ✓ (unchanged) |
| Veena model | Veena AI FP16 | Veena AI FP16 (unchanged) |

---

## 11. References

| Source | URL | Relevance |
|---|---|---|
| maya1 vLLM streaming server | `huggingface.co/maya-research/maya1/blob/main/vllm_streaming_inference.py` | Primary reference — official Maya Research, identical architecture |
| Orpheus-TTS decoder | `github.com/canopyai/Orpheus-TTS/blob/main/orpheus_tts_pypi/orpheus_tts/decoder.py` | Secondary reference — production streaming decoder, identical SNAC architecture |
| SNAC source | `github.com/hubertsiuzdak/snac/blob/main/snac/snac.py` | Proves stateless `decode()`, no minimum length |
| SNAC 24kHz config | `huggingface.co/hubertsiuzdak/snac_24khz/raw/main/config.json` | Confirms `attn_window_size: null` — no attention |
| Veena model card | `huggingface.co/maya-research/Veena` | Confirms streaming is "future updates", architecture specs |
| vLLM AsyncEngineArgs | vLLM documentation | Engine configuration reference |

---

## 12. Approval

This ADR modifies only the TTS serving layer. It does not change:
- VoiceOS architecture (Volumes 1–7)
- The production model (Veena AI FP16)
- The VeenaAdapter's external contract (still returns `AudioClause` objects)
- Any Sprint-001 through Sprint-011 components
- The LLM or STT serving layers

**Approved for implementation in Sprint-012 Phase 3.**
