#!/usr/bin/env python3
"""Whisper STT Inference Server — Sprint-009 GPU deployment.

Runs on the GPU node (IP ephemeral; see GPU_NODE_STATE.md). Loads Whisper large-v3-turbo via
faster-whisper with int8_float16 compute type. Exposes a FastAPI HTTP server
on port 8100 for the CPU-side WhisperAdapter to call.

Endpoints:
    GET  /health/live   — liveness probe (always 200 if process is alive)
    GET  /health/ready  — readiness probe (200 only after model is loaded)
    POST /transcribe    — transcribe PCM16LE audio, return word-level results

Usage (matches restore.sh):
    python3 server.py \
        --model-path /opt/voiceos-gpu/models/whisper-large-v3-turbo \
        --compute-type int8_float16 \
        --port 8100

Architecture: V1 Ch8 (STT); Sprint-009 spec Phase 2.
VRAM: ~1400 MB (Whisper large-v3-turbo int8_float16 model + ctranslate2 CUDA
      workspace pre-allocated at warmup). The GPU node's vLLM service must leave
      ≥1400 MiB VRAM free for ctranslate2 encoder buffers; tight VRAM (<1000 MiB
      free) causes CUDA allocator fragmentation and periodic STT latency spikes.
      See GPU_NODE_STATE.md for the validated gpu-memory-utilization setting.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import logging
import struct
import threading
import time
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("voiceos.stt.server")

# ---------------------------------------------------------------------------
# Prometheus metrics (thread-safe counters; no external dependency)
# ---------------------------------------------------------------------------

_metrics_lock = threading.Lock()
_metrics: dict[str, float] = {
    "requests_success": 0.0,
    "requests_error": 0.0,
    "requests_timeout": 0.0,
    "latency_sum_ms": 0.0,
    "latency_count": 0.0,
}


def _inc(key: str, value: float = 1.0) -> None:
    with _metrics_lock:
        _metrics[key] += value


# ---------------------------------------------------------------------------
# Global model state
# ---------------------------------------------------------------------------

_model: Any = None  # faster_whisper.WhisperModel
_model_ready: bool = False
_model_name: str = "large-v3-turbo"

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="VoiceOS STT Service", version="0.9.1")

# Thread pool dedicated to Whisper inference so ctranslate2 CUDA calls never
# block uvicorn's async event loop. A single worker serializes requests, which
# matches ctranslate2's own single-GPU-context design; add workers here only
# if a multi-GPU configuration is introduced.
_TRANSCRIPTION_TIMEOUT_S = 30.0
_stt_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt-worker")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TranscribeRequest(BaseModel):
    """PCM16LE audio bytes (base64) and language."""

    audio_b64: str
    """Base64-encoded PCM16LE mono 16 kHz audio bytes."""

    language: str = "hi"
    """BCP-47 language code. Empty string lets Whisper auto-detect."""

    beam_size: int = 5
    """Beam size for CTC/attention decoding."""


class WordResult(BaseModel):
    """Single word recognition result."""

    word: str
    confidence: float
    start_ms: int
    end_ms: int
    is_final: bool = True


class TranscribeResponse(BaseModel):
    """Whisper transcription result."""

    words: list[WordResult]
    language: str
    duration_ms: int
    latency_ms: float


# ---------------------------------------------------------------------------
# Health and metrics endpoints
# ---------------------------------------------------------------------------


@app.get("/health/live")
async def liveness() -> JSONResponse:
    """Liveness probe — always returns 200 if the process is alive."""
    return JSONResponse({"status": "alive", "service": "voiceos-stt"})


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus text-format metrics endpoint (TT-017)."""
    with _metrics_lock:
        m = dict(_metrics)
    ready = 1 if _model_ready else 0
    avg_ms = (m["latency_sum_ms"] / m["latency_count"]) if m["latency_count"] > 0 else 0.0
    lines = [
        "# HELP voiceos_stt_requests_total Total STT transcription requests",
        "# TYPE voiceos_stt_requests_total counter",
        f'voiceos_stt_requests_total{{status="success"}} {int(m["requests_success"])}',
        f'voiceos_stt_requests_total{{status="error"}} {int(m["requests_error"])}',
        f'voiceos_stt_requests_total{{status="timeout"}} {int(m["requests_timeout"])}',
        "# HELP voiceos_stt_model_ready Whether the STT model is loaded and ready (1=ready)",
        "# TYPE voiceos_stt_model_ready gauge",
        f"voiceos_stt_model_ready {ready}",
        "# HELP voiceos_stt_latency_ms_sum Sum of successful transcription latencies (ms)",
        "# TYPE voiceos_stt_latency_ms_sum counter",
        f"voiceos_stt_latency_ms_sum {m['latency_sum_ms']:.1f}",
        "# HELP voiceos_stt_latency_ms_count Number of completed transcriptions",
        "# TYPE voiceos_stt_latency_ms_count counter",
        f"voiceos_stt_latency_ms_count {int(m['latency_count'])}",
        "# HELP voiceos_stt_latency_ms_avg Average transcription latency (ms)",
        "# TYPE voiceos_stt_latency_ms_avg gauge",
        f"voiceos_stt_latency_ms_avg {avg_ms:.1f}",
    ]
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    """Readiness probe — 200 only after Whisper model is fully loaded."""
    if not _model_ready:
        raise HTTPException(status_code=503, detail="Model loading in progress")
    return JSONResponse({"status": "ready", "model": _model_name, "vram_target_mb": 900})


# ---------------------------------------------------------------------------
# Transcription endpoint
# ---------------------------------------------------------------------------


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(request: TranscribeRequest) -> TranscribeResponse:
    """Transcribe PCM16LE audio, returning word-level hypotheses.

    The audio must be:
    - PCM16LE encoding
    - 16 kHz sample rate
    - Mono channel
    - Base64-encoded in the request body

    Returns:
        TranscribeResponse with per-word results.
    """
    if not _model_ready:
        raise HTTPException(status_code=503, detail="Model not ready")

    import base64

    try:
        raw_bytes = base64.b64decode(request.audio_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 audio: {exc}") from exc

    # PCM16LE → float32 normalised to [-1.0, 1.0]
    audio_f32 = _pcm16le_to_float32(raw_bytes)
    if len(audio_f32) == 0:
        raise HTTPException(status_code=400, detail="Empty audio")

    t_start = time.monotonic()
    lang = request.language or None  # None triggers auto-detect

    def _run_transcribe() -> tuple[Any, Any]:
        segs, inf = _model.transcribe(
            audio_f32,
            language=lang,
            beam_size=request.beam_size,
            word_timestamps=True,
        )
        return list(segs), inf  # materialise generator inside the thread

    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_stt_executor, _run_transcribe)
    try:
        all_segments, info = await asyncio.wait_for(future, timeout=_TRANSCRIPTION_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        _inc("requests_timeout")
        logger.error("Whisper transcription timed out after %.0fs", _TRANSCRIPTION_TIMEOUT_S)
        raise HTTPException(status_code=504, detail="Transcription timed out") from exc
    except Exception as exc:
        _inc("requests_error")
        logger.exception("Whisper transcription failed")
        raise HTTPException(status_code=500, detail=f"Transcription error: {exc}") from exc

    latency_ms = (time.monotonic() - t_start) * 1000
    _inc("requests_success")
    _inc("latency_sum_ms", latency_ms)
    _inc("latency_count")

    words: list[WordResult] = []
    total_words = sum(len(seg.words or []) for seg in all_segments)
    word_count = 0
    for seg in all_segments:
        for w in seg.words or []:
            word_count += 1
            words.append(
                WordResult(
                    word=w.word.strip(),
                    confidence=float(w.probability),
                    start_ms=int(w.start * 1000),
                    end_ms=int(w.end * 1000),
                    is_final=(word_count == total_words),
                )
            )

    duration_ms = int(len(audio_f32) / 16.0)  # 16 kHz → ms

    logger.info(
        "Transcribed %d words in %.0f ms | lang=%s",
        len(words),
        latency_ms,
        info.language,
    )

    return TranscribeResponse(
        words=words,
        language=info.language,
        duration_ms=duration_ms,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pcm16le_to_float32(pcm_bytes: bytes) -> np.ndarray:  # type: ignore[type-arg]
    """Convert PCM16LE bytes to a float32 numpy array normalised to [-1.0, 1.0]."""
    n_samples = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{n_samples}h", pcm_bytes[: n_samples * 2])
    return np.array(samples, dtype=np.float32) / 32768.0


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def _load_model(model_path: str, compute_type: str) -> None:
    """Load the Whisper model into global state, then run a warmup inference.

    The warmup is mandatory. ctranslate2 does not pre-allocate its CUDA workspace
    buffers at model load time — it allocates them lazily on the first call to
    model.encode(). If the first real inference request arrives when VRAM is
    already tight (as it is on this L4 with vLLM + Veena co-resident), the lazy
    allocation fails with CUDA OOM. Running a warmup transcription immediately
    after model load forces ctranslate2 to allocate and retain its workspace in
    the pre-allocated VRAM pool, so all subsequent real requests succeed without
    any additional large allocation. Observed warmup time: ~1-2s.
    """
    global _model, _model_ready, _model_name

    logger.info("Loading Whisper model from %s (compute_type=%s)...", model_path, compute_type)
    t0 = time.monotonic()

    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    _model = WhisperModel(
        model_path,
        device="cuda",
        compute_type=compute_type,
        num_workers=1,
    )
    _model_name = "whisper-large-v3-turbo"

    elapsed = (time.monotonic() - t0) * 1000
    logger.info("Whisper model loaded in %.0f ms. Running CUDA warmup...", elapsed)

    # Warmup: 0.5s silence forces ctranslate2 to allocate its CUDA encoder workspace.
    # This must succeed before _model_ready is set True; if it fails (VRAM), we
    # crash at startup rather than serving 500s on every real request.
    t_warmup = time.monotonic()
    _silence = np.zeros(8000, dtype=np.float32)  # 0.5s at 16kHz
    try:
        segs, _info = _model.transcribe(_silence, language="hi", beam_size=1, word_timestamps=False)
        list(segs)  # consume generator to trigger actual encoder execution
    except Exception:
        logger.exception("CUDA warmup failed — likely VRAM OOM. Check GPU memory allocations.")
        raise

    warmup_ms = (time.monotonic() - t_warmup) * 1000
    logger.info("CUDA warmup complete in %.0f ms. STT ready.", warmup_ms)
    _model_ready = True


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS STT Inference Server")
    parser.add_argument(
        "--model-path",
        default="large-v3-turbo",
        help="Path to the faster-whisper model directory or HuggingFace model ID",
    )
    parser.add_argument(
        "--compute-type",
        default="int8_float16",
        choices=["int8_float16", "float16", "int8"],
        help="CTranslate2 compute type",
    )
    parser.add_argument("--port", type=int, default=8100, help="HTTP port")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    args = parser.parse_args()

    _load_model(args.model_path, args.compute_type)

    logger.info("Starting STT server on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
