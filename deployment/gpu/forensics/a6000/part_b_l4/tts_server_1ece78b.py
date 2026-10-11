#!/usr/bin/env python3
"""Veena TTS Inference Server — Sprint-012 Phase 3 (streaming fix).

Streams 85.33ms PCM audio chunks via FastAPI StreamingResponse using a
sliding-window SNAC decode as tokens are generated. Reduces TTFA from
8,730ms (batch HF inference) to ~100-300ms on L4.

Architecture: V1 Ch15-17 (TTS); ADR-001 (vLLM streaming → HF streaming fix).
VRAM: ~7,808 MB (Veena 3B BF16) + ~10 MB (SNAC codec) — unchanged from batch server.
Model: maya-research/Veena — 3B params, BF16, SNAC 24 kHz — retained as production model.

Streaming design (ADR-001, refs: maya1 vllm_streaming_inference.py, Orpheus-TTS decoder.py):
  1. _SNACTokenStreamer: custom BaseStreamer that delivers raw integer token IDs from
     model.generate() without text decoding — avoids string-to-int parsing.
  2. Generation thread: model.generate() runs in a background thread with the streamer,
     so the main event loop is never blocked.
  3. Sliding-window SNAC decode: every 7 new SNAC tokens (after min 28 accumulated),
     decode the last 28 tokens (4 super-frames → 8192 samples) and yield the
     middle frame only (samples [2048:4096] = 85.33ms), discarding outer frames
     that would have CNN boundary artifacts.
  4. StreamingResponse: FastAPI yields each 85.33ms chunk to the HTTP client as it
     is decoded, achieving sub-300ms time-to-first-audio on L4.

Endpoints:
    GET  /health/live   — liveness probe
    GET  /health/ready  — readiness probe (200 only after model loaded)
    POST /synthesize    — stream PCM chunks (float32 LE, 24 kHz mono)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import queue
import threading
import time
from collections.abc import AsyncIterator, Iterator
from threading import Thread
from typing import Any, cast

import numpy as np
import torch  # type: ignore[import-not-found]
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("voiceos.tts.server")

# ---------------------------------------------------------------------------
# Prometheus metrics (thread-safe counters; no external dependency)
# ---------------------------------------------------------------------------

_metrics_lock = threading.Lock()
_metrics: dict[str, float] = {
    "requests_success": 0.0,
    "requests_error": 0.0,
    "ttfa_sum_ms": 0.0,
    "ttfa_count": 0.0,
}


def _inc(key: str, value: float = 1.0) -> None:
    with _metrics_lock:
        _metrics[key] += value


# ---------------------------------------------------------------------------
# Global model state
# ---------------------------------------------------------------------------

_model: Any = None
_tokenizer: Any = None
_snac_model: Any = None
_model_ready: bool = False
_device: str = "cuda"
_sample_rate: int = 24000

# ---------------------------------------------------------------------------
# Veena token constants — verified against maya-research/Veena tokenizer.
# custom_token_N = base_vocab_size + N = 128256 + N.
# ---------------------------------------------------------------------------

_START_OF_HUMAN_TOKEN = 128259
_END_OF_HUMAN_TOKEN = 128260
_START_OF_AI_TOKEN = 128261
_END_OF_AI_TOKEN = 128262
_START_OF_SPEECH_TOKEN = 128257
_END_OF_SPEECH_TOKEN = 128258
_AUDIO_CODE_BASE_OFFSET = 128266

_SNAC_CODEBOOK_SIZE = 4096
_TOKENS_PER_FRAME = 7  # SNAC 24 kHz: vq_strides [4,2,1] -> 1+2+4 = 7 per super-frame
_SLIDING_WINDOW_TOKENS = 21  # 3 super-frames: decode 3, keep middle frame (index 1). Reduces TTFA 28→21 tok.
# Previously 28 (4 frames). Reduced to 21 (3 frames) in Sprint-028: frame 1 of 3 is still the clean
# middle frame — no CNN boundary artifacts — while first audio arrives 7 tokens (214ms) sooner.
_SAMPLES_PER_FRAME = 2048  # 1 super-frame at 24 kHz: hop_length(512) x coarse_stride(4) = 2048 samples = 85.33ms

_SNAC_MIN_TOKEN = _AUDIO_CODE_BASE_OFFSET
_SNAC_MAX_TOKEN = _AUDIO_CODE_BASE_OFFSET + _TOKENS_PER_FRAME * _SNAC_CODEBOOK_SIZE - 1

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="VoiceOS TTS Service", version="1.0.1")

# Allowlist of speaker IDs whose voice models are loaded on this node (PEN-005).
# Rejecting unknown speakers at the API boundary prevents prompt-injection via
# the speaker field and avoids speculative model loads that would OOM on L4.
_ALLOWED_SPEAKERS: frozenset[str] = frozenset({"kavya"})


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class VoiceConfigRequest(BaseModel):
    """Prosody parameters forwarded from AdaptiveProsodyEngine."""

    pitch_shift: float = 0.0
    rate_scale: float = 1.0
    energy_scale: float = 1.0
    pause_ms_after_clause: int = 150
    language: str = "hi-IN"


class SynthesizeRequest(BaseModel):
    """TTS synthesis request."""

    text: str
    speaker: str = "kavya"
    voice_config: VoiceConfigRequest = VoiceConfigRequest()


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/health/live")
async def liveness() -> JSONResponse:
    return JSONResponse({"status": "alive", "service": "voiceos-tts"})


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus text-format metrics endpoint (TT-017)."""
    with _metrics_lock:
        m = dict(_metrics)
    ready = 1 if _model_ready else 0
    avg_ms = (m["ttfa_sum_ms"] / m["ttfa_count"]) if m["ttfa_count"] > 0 else 0.0
    lines = [
        "# HELP voiceos_tts_requests_total Total TTS synthesis requests",
        "# TYPE voiceos_tts_requests_total counter",
        f'voiceos_tts_requests_total{{status="success"}} {int(m["requests_success"])}',
        f'voiceos_tts_requests_total{{status="error"}} {int(m["requests_error"])}',
        "# HELP voiceos_tts_model_ready Whether the TTS model is loaded and ready (1=ready)",
        "# TYPE voiceos_tts_model_ready gauge",
        f"voiceos_tts_model_ready {ready}",
        "# HELP voiceos_tts_ttfa_ms_sum Sum of time-to-first-audio latencies (ms)",
        "# TYPE voiceos_tts_ttfa_ms_sum counter",
        f"voiceos_tts_ttfa_ms_sum {m['ttfa_sum_ms']:.1f}",
        "# HELP voiceos_tts_ttfa_ms_count Number of completed synthesis requests",
        "# TYPE voiceos_tts_ttfa_ms_count counter",
        f"voiceos_tts_ttfa_ms_count {int(m['ttfa_count'])}",
        "# HELP voiceos_tts_ttfa_ms_avg Average time-to-first-audio latency (ms)",
        "# TYPE voiceos_tts_ttfa_ms_avg gauge",
        f"voiceos_tts_ttfa_ms_avg {avg_ms:.1f}",
    ]
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    if not _model_ready:
        raise HTTPException(status_code=503, detail="Model loading in progress")
    return JSONResponse(
        {
            "status": "ready",
            "model": "maya-research/Veena",
            "sample_rate": _sample_rate,
            "streaming": True,
            "vram_target_mb": 7808,
        }
    )


# ---------------------------------------------------------------------------
# Synthesis endpoint
# ---------------------------------------------------------------------------


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest) -> StreamingResponse:
    """Stream 24 kHz PCM audio chunks for the given text.

    Returns:
        StreamingResponse of float32 LE PCM bytes (24 kHz mono).
        Each chunk is 85.33ms (2048 samples x 4 bytes = 8192 bytes per chunk).
        Transfer-Encoding: chunked — client receives audio before synthesis completes.
    """
    if not _model_ready:
        raise HTTPException(status_code=503, detail="Model not ready")

    if request.speaker not in _ALLOWED_SPEAKERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown speaker '{request.speaker}'. Allowed: {sorted(_ALLOWED_SPEAKERS)}",
        )

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    _MAX_TEXT_CHARS = 2000  # PEN-009: cap prevents single-request GPU monopolisation
    if len(text) > _MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"Text too long: {len(text)} chars (max {_MAX_TEXT_CHARS})",
        )

    t_start = time.monotonic()
    logger.info("Stream start: %d chars | speaker=%s", len(text), request.speaker)

    async def _audio_gen() -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        sync_gen = _stream_synthesis_sync(text, request.speaker, request.voice_config)

        # Sentinel pattern: catch StopIteration in the thread (not in the coroutine)
        # because Python 3.12+ raises RuntimeError if StopIteration propagates out
        # of run_in_executor into an async generator frame.
        _done = object()

        def _next_chunk() -> bytes | object:
            try:
                return next(sync_gen)
            except StopIteration:
                return _done

        first_chunk = True
        ttfa_recorded = False
        while True:
            result = await loop.run_in_executor(None, _next_chunk)
            if result is _done:
                break
            chunk = cast(bytes, result)
            if first_chunk:
                ttfa_ms = (time.monotonic() - t_start) * 1000
                logger.info(
                    "First audio chunk: %d chars -> %d bytes @ %.0f ms | speaker=%s",
                    len(text),
                    len(chunk),
                    ttfa_ms,
                    request.speaker,
                )
                _inc("requests_success")
                _inc("ttfa_sum_ms", ttfa_ms)
                _inc("ttfa_count")
                first_chunk = False
                ttfa_recorded = True
            yield chunk
        if not ttfa_recorded:
            _inc("requests_error")
        elapsed = (time.monotonic() - t_start) * 1000
        logger.info(
            "Stream done: %d chars in %.0f ms | speaker=%s",
            len(text),
            elapsed,
            request.speaker,
        )

    return StreamingResponse(_audio_gen(), media_type="application/octet-stream")


# ---------------------------------------------------------------------------
# Custom token streamer — delivers raw integer token IDs without text decoding
# ---------------------------------------------------------------------------


class _SNACTokenStreamer:
    """BaseStreamer subclass that enqueues raw integer token IDs.

    Unlike TextIteratorStreamer (which calls tokenizer.decode() and yields strings),
    this delivers integer token IDs directly. The consumer reads SNAC-range tokens
    and converts them to codebook values without any string parsing.

    Compatible with transformers BaseStreamer interface (put/end).
    """

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[int | None] = queue.SimpleQueue()

    def put(self, value: torch.LongTensor) -> None:
        for token_id in value.flatten().tolist():
            self._queue.put(int(token_id))

    def end(self) -> None:
        self._queue.put(None)

    def __iter__(self) -> Iterator[int]:
        while True:
            item = self._queue.get()
            if item is None:
                return
            yield item


# ---------------------------------------------------------------------------
# Sliding-window SNAC decode
# ---------------------------------------------------------------------------


def _snac_decode_window(window: list[int], voice_config: VoiceConfigRequest) -> bytes | None:
    """Decode a 21-token sliding window; return middle super-frame as float32 bytes.

    Args:
        window: List of 21 pre-decoded codebook values (position-indexed, 0..4095).
                window[7*i + j] is the codebook value for frame i, position j.
        voice_config: Prosody config (energy_scale applied per chunk).

    Returns:
        8192 bytes (2048 float32 samples = 85.33ms) or None if window too short.

    Design: decode 3 super-frames (21 tokens → 6144 samples), extract only the
    middle frame (samples [2048:4096]), discarding outer frames which have
    CNN boundary artifacts from the non-causal convolutional decoder.
    """
    if len(window) < _SLIDING_WINDOW_TOKENS:
        return None

    num_frames = _SLIDING_WINDOW_TOKENS // _TOKENS_PER_FRAME  # 3 (window=21 tokens)
    codes_0: list[int] = []
    codes_1: list[int] = []
    codes_2: list[int] = []

    for i in range(num_frames):
        b = i * _TOKENS_PER_FRAME
        codes_0.append(window[b])
        codes_1.append(window[b + 1])
        codes_2.append(window[b + 2])
        codes_2.append(window[b + 3])
        codes_1.append(window[b + 4])
        codes_2.append(window[b + 5])
        codes_2.append(window[b + 6])

    def _clamp(vals: list[int]) -> list[int]:
        return [min(max(v, 0), _SNAC_CODEBOOK_SIZE - 1) for v in vals]

    codes = [
        torch.tensor(_clamp(codes_0), dtype=torch.long, device=_device).unsqueeze(0),
        torch.tensor(_clamp(codes_1), dtype=torch.long, device=_device).unsqueeze(0),
        torch.tensor(_clamp(codes_2), dtype=torch.long, device=_device).unsqueeze(0),
    ]

    with torch.no_grad():
        audio_hat: torch.Tensor = cast(torch.Tensor, _snac_model.decode(codes))  # [1, 1, 8192]

    # Extract middle frame: [2048:4096], avoiding CNN boundary artifacts on frames 0 and 3
    waveform = audio_hat.squeeze()[_SAMPLES_PER_FRAME : _SAMPLES_PER_FRAME * 2]

    if abs(voice_config.energy_scale - 1.0) > 0.01:
        waveform = waveform * voice_config.energy_scale

    return cast(bytes, waveform.cpu().float().numpy().tobytes())


# ---------------------------------------------------------------------------
# Streaming synthesis — generates SNAC tokens, decodes incrementally
# ---------------------------------------------------------------------------


def _stream_synthesis_sync(
    text: str,
    speaker: str,
    voice_config: VoiceConfigRequest,
) -> Iterator[bytes]:
    """Sync generator: streams 85ms audio chunks as Veena generates SNAC tokens.

    Runs model.generate() in a background thread with _SNACTokenStreamer.
    The main thread consumes token IDs from the streamer's queue and decodes
    sliding windows of 28 tokens every 7 new tokens, yielding 85.33ms chunks.

    Yields:
        bytes: Float32 LE PCM (8192 bytes = 2048 samples = 85.33ms at 24 kHz).
               First chunk arrives after 21 SNAC tokens are generated (~640ms on L4).
    """
    # ── Build the Veena prompt ────────────────────────────────────────────────
    prompt = f"<spk_{speaker}> {text}"
    prompt_ids = _tokenizer.encode(prompt, add_special_tokens=False)
    input_ids_list = [
        _START_OF_HUMAN_TOKEN,
        *prompt_ids,
        _END_OF_HUMAN_TOKEN,
        _START_OF_AI_TOKEN,
        _START_OF_SPEECH_TOKEN,
    ]
    input_ids = torch.tensor([input_ids_list], device=_device)
    max_new = min(int(len(text) * 1.3) * _TOKENS_PER_FRAME + 21, 700)

    # ── Launch generation thread ─────────────────────────────────────────────
    streamer = _SNACTokenStreamer()

    gen_exception: list[Exception] = []

    def _generate() -> None:
        try:
            with torch.no_grad():
                _model.generate(
                    input_ids,
                    max_new_tokens=max_new,
                    do_sample=True,
                    temperature=0.4,
                    top_p=0.9,
                    repetition_penalty=1.05,
                    pad_token_id=_tokenizer.pad_token_id or _END_OF_SPEECH_TOKEN,
                    eos_token_id=[_END_OF_SPEECH_TOKEN, _END_OF_AI_TOKEN],
                    streamer=streamer,
                )
        except Exception as exc:
            logger.exception("Generation thread error: %s", exc)
            gen_exception.append(exc)
        finally:
            streamer.end()  # Always signal end so consumer doesn't block

    thread = Thread(target=_generate, daemon=True)
    thread.start()

    # ── Sliding-window SNAC decode ───────────────────────────────────────────
    audio_buffer: list[int] = []
    audio_count: int = 0

    try:
        for token_id in streamer:
            if not (_SNAC_MIN_TOKEN <= token_id <= _SNAC_MAX_TOKEN):
                continue

            # Map raw token ID → 0-indexed codebook value for this frame position
            pos = audio_count % _TOKENS_PER_FRAME
            codebook_val = token_id - _AUDIO_CODE_BASE_OFFSET - pos * _SNAC_CODEBOOK_SIZE
            audio_buffer.append(max(0, min(codebook_val, _SNAC_CODEBOOK_SIZE - 1)))
            audio_count += 1

            # Every complete super-frame (7 tokens), after minimum 3 frames (21 tokens):
            # decode sliding window and yield the middle frame (85.33ms audio)
            if audio_count % _TOKENS_PER_FRAME == 0 and audio_count >= _SLIDING_WINDOW_TOKENS:
                chunk = _snac_decode_window(audio_buffer[-_SLIDING_WINDOW_TOKENS:], voice_config)
                if chunk:
                    yield chunk

    finally:
        thread.join()

    if gen_exception:
        raise RuntimeError(f"Veena generation failed: {gen_exception[0]}") from gen_exception[0]

    # ── Inter-clause silence ─────────────────────────────────────────────────
    if voice_config.pause_ms_after_clause > 0:
        silence_samples = int(voice_config.pause_ms_after_clause * _sample_rate / 1000)
        silence = np.zeros(silence_samples, dtype=np.float32)
        yield silence.tobytes()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def _load_model(model_path: str, snac_path: str) -> None:
    """Load Veena BF16 model and SNAC 24 kHz codec into global state."""
    global _model, _tokenizer, _snac_model, _model_ready

    logger.info("Loading Veena TTS model from %s ...", model_path)
    t0 = time.monotonic()

    from snac import SNAC  # type: ignore[import-not-found]
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import-not-found]

    _tokenizer = AutoTokenizer.from_pretrained(model_path)
    _model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=torch.bfloat16,
        device_map=_device,
    )
    _model.eval()

    logger.info("Loading SNAC 24 kHz codec from %s ...", snac_path)
    _snac_model = SNAC.from_pretrained(snac_path).to(_device)
    _snac_model.eval()

    # Warm-up: run one short synthesis to trigger CUDA kernel JIT compilation.
    # HuggingFace transformers/triton kernels compile on first use; without this
    # warm-up the first real request pays a ~900ms one-time penalty.
    logger.info("Running CUDA kernel warm-up synthesis...")
    t_warmup = time.monotonic()
    _warmup_req = VoiceConfigRequest()
    for _ in _stream_synthesis_sync("hello", "kavya", _warmup_req):
        pass
    logger.info("Warm-up complete in %.0f ms", (time.monotonic() - t_warmup) * 1000)

    _model_ready = True
    logger.info("Veena + SNAC loaded in %.0f ms (streaming mode)", (time.monotonic() - t0) * 1000)


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS TTS Inference Server (streaming)")
    parser.add_argument("--model-path", default="maya-research/Veena")
    parser.add_argument("--snac-path", default="hubertsiuzdak/snac_24khz")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    _load_model(args.model_path, args.snac_path)

    logger.info("Starting streaming TTS server on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
