#!/usr/bin/env python3
"""Whisper STT Inference Server — Sprint-029 GPU Runtime Redesign.

Runs on the GPU node or in local dev mode (CPU + mock).

Endpoints:
    GET  /health/live   — liveness probe (always 200 if process is alive)
    GET  /health/ready  — readiness probe (200 only after model is loaded)
    GET  /metrics       — Prometheus text-format metrics
    POST /transcribe    — transcribe PCM16LE audio, return word-level results

Usage:
    # Production (GPU):
    python3 server.py --model-path /opt/voiceos-gpu/models/whisper-large-v3-turbo \
        --compute-type int8_float16 --device cuda --port 8100

    # Local dev (CPU, tiny model):
    python3 server.py --model-path tiny --compute-type int8 --device cpu --port 8100

    # Local dev (mock, no model):
    python3 server.py --mock --port 8100

Architecture: V1 Ch8 (STT).
Production VRAM: ~1,860 MiB (1,260 MiB model + 600 MiB ctranslate2 workspace).
The GPU node's vLLM service must leave ≥1,400 MiB VRAM free for ctranslate2
encoder buffers; tight VRAM causes CUDA allocator fragmentation and STT spikes.
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

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

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

_model: Any = None  # faster_whisper.WhisperModel or _MockSTTModel
_model_ready: bool = False
_model_name: str = "large-v3-turbo"
_mock_mode: bool = False

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="VoiceOS STT Service", version="1.0.0")


# ---------------------------------------------------------------------------
# Mock model — returns a synthetic Hindi response without loading any model.
# Used in VOICEOS_MODE=dev or when --mock flag is passed.
# ---------------------------------------------------------------------------


class _MockSTTModel:
    """Drop-in replacement for WhisperModel; returns canned transcription."""

    _MOCK_RESPONSE = "नमस्ते, मैं Kavya बोल रही हूं। आप कैसे हैं?"
    _MOCK_DELAY_S = 0.08  # Simulate 80ms STT latency

    def transcribe(
        self, audio: Any, language: str | None = None, beam_size: int = 1, **kwargs: Any
    ) -> tuple[Any, Any]:
        import time as _time

        _time.sleep(self._MOCK_DELAY_S)

        class _MockWord:
            def __init__(self, w: str, start: float, end: float) -> None:
                self.word = w
                self.probability = 0.95
                self.start = start
                self.end = end

        class _MockSegment:
            def __init__(self, words: list[Any]) -> None:
                self.words = words

        class _MockInfo:
            language = "hi"

        words_text = self._MOCK_RESPONSE.split()
        words = [
            _MockWord(w, i * 0.3, (i + 1) * 0.3) for i, w in enumerate(words_text)
        ]
        return [_MockSegment(words)], _MockInfo()

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
    return JSONResponse(
        {
            "status": "ready",
            "model": _model_name,
            "mock": _mock_mode,
            "vram_target_mb": 0 if _mock_mode else 1860,
        }
    )


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

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty audio")

    # In mock mode bypass numpy PCM conversion entirely — mock model ignores the audio.
    if _mock_mode:
        t_start = time.monotonic()
        duration_ms = len(raw_bytes) // 2 // 16  # PCM16LE at 16 kHz → ms
        lang = request.language or "hi"

        def _run_mock() -> tuple[Any, Any]:
            segs, inf = _model.transcribe([], language=lang, beam_size=1)
            return list(segs), inf

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(_stt_executor, _run_mock)
        try:
            all_segments, info = await asyncio.wait_for(future, timeout=_TRANSCRIPTION_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            _inc("requests_timeout")
            raise HTTPException(status_code=504, detail="Transcription timed out") from exc
        except Exception as exc:
            _inc("requests_error")
            raise HTTPException(status_code=500, detail=f"Transcription error: {exc}") from exc

        latency_ms = (time.monotonic() - t_start) * 1000
        _inc("requests_success")
        _inc("latency_sum_ms", latency_ms)
        _inc("latency_count")
        words = []
        for seg in all_segments:
            for w in seg.words or []:
                words.append(WordResult(
                    word=w.word.strip(),
                    confidence=float(w.probability),
                    start_ms=int(w.start * 1000),
                    end_ms=int(w.end * 1000),
                    is_final=True,
                ))
        if words:
            words[-1].is_final = True
        return TranscribeResponse(words=words, language=info.language,
                                  duration_ms=duration_ms, latency_ms=latency_ms)

    # Real mode: PCM16LE → float32 → Whisper
    if not _HAS_NUMPY:
        raise HTTPException(status_code=503, detail="numpy not installed; start with --mock for local dev")

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


def _load_model(model_path: str, compute_type: str, device: str, mock: bool) -> None:
    """Load the Whisper model (or mock) into global state, then run a warmup.

    Mock mode: skip model loading entirely; synthetic responses are returned.

    Real mode: ctranslate2 does not pre-allocate its CUDA workspace at load time
    — it allocates lazily on the first model.encode() call. On a tight VRAM
    budget (vLLM + Veena co-resident), this lazy allocation can fail. The warmup
    here forces ctranslate2 to allocate and retain its workspace upfront so all
    subsequent requests succeed. Observed warmup time: ~1-2s on GPU.
    """
    global _model, _model_ready, _model_name, _mock_mode

    _mock_mode = mock

    if mock:
        logger.info("STT mock mode: loading synthetic model (no model weights downloaded)")
        _model = _MockSTTModel()
        _model_name = "mock"
        _model_ready = True
        logger.info("STT mock ready")
        return

    logger.info(
        "Loading Whisper model from %s (device=%s compute_type=%s)...",
        model_path,
        device,
        compute_type,
    )
    t0 = time.monotonic()

    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    # int8_float16 requires CUDA; fall back to int8 on CPU automatically.
    effective_compute = compute_type
    if device == "cpu" and compute_type == "int8_float16":
        effective_compute = "int8"
        logger.info("CPU device: compute_type int8_float16 → int8")

    _model = WhisperModel(
        model_path,
        device=device,
        compute_type=effective_compute,
        num_workers=1,
    )
    _model_name = model_path if "/" not in model_path else model_path.split("/")[-1]

    elapsed = (time.monotonic() - t0) * 1000
    logger.info("Whisper model loaded in %.0f ms. Running warmup...", elapsed)

    # Warmup: 0.5s silence forces ctranslate2 to allocate its encoder workspace.
    # On GPU: pre-empts CUDA OOM under tight VRAM. On CPU: pre-JITs kernels.
    t_warmup = time.monotonic()
    _silence = np.zeros(8000, dtype=np.float32)  # 0.5s at 16 kHz
    try:
        segs, _info = _model.transcribe(_silence, language="hi", beam_size=1, word_timestamps=False)
        list(segs)
    except Exception:
        logger.exception("Warmup failed — check VRAM / model path / compute_type.")
        raise

    warmup_ms = (time.monotonic() - t_warmup) * 1000
    logger.info("Warmup complete in %.0f ms. STT ready.", warmup_ms)
    _model_ready = True


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description="VoiceOS STT Inference Server")
    parser.add_argument(
        "--model-path",
        default=os.environ.get("WHISPER_MODEL_PATH", "large-v3-turbo"),
        help="Path to the faster-whisper model directory, HF model ID, or size string",
    )
    parser.add_argument(
        "--compute-type",
        default=os.environ.get("STT_COMPUTE_TYPE", "int8_float16"),
        choices=["int8_float16", "float16", "int8", "float32"],
        help="CTranslate2 compute type (int8_float16=GPU, int8=CPU)",
    )
    parser.add_argument(
        "--device",
        default=os.environ.get("STT_DEVICE", "cuda"),
        choices=["cuda", "cpu"],
        help="Inference device (cuda for GPU, cpu for local dev)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=os.environ.get("STT_MOCK", "").lower() == "true",
        help="Mock mode: return synthetic transcriptions without loading a model",
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("STT_SERVICE_PORT", "8100")))
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    _load_model(args.model_path, args.compute_type, args.device, args.mock)

    logger.info(
        "Starting STT server on %s:%d (device=%s mock=%s)",
        args.host, args.port, args.device, args.mock,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
