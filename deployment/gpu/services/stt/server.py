#!/usr/bin/env python3
"""Whisper STT Inference Server — Sprint-009 GPU deployment.

Runs on the GPU node (217.18.55.19). Loads Whisper large-v3-turbo via
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
VRAM: ~900 MB (Whisper large-v3-turbo, int8_float16).
"""

from __future__ import annotations

import argparse
import logging
import struct
import time
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("voiceos.stt.server")

# ---------------------------------------------------------------------------
# Global model state
# ---------------------------------------------------------------------------

_model: Any = None  # faster_whisper.WhisperModel
_model_ready: bool = False
_model_name: str = "large-v3-turbo"

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="VoiceOS STT Service", version="0.9.0")


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
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/health/live")
async def liveness() -> JSONResponse:
    """Liveness probe — always returns 200 if the process is alive."""
    return JSONResponse({"status": "alive", "service": "voiceos-stt"})


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

    try:
        segments, info = _model.transcribe(
            audio_f32,
            language=lang,
            beam_size=request.beam_size,
            word_timestamps=True,
        )
    except Exception as exc:
        logger.exception("Whisper transcription failed")
        raise HTTPException(status_code=500, detail=f"Transcription error: {exc}") from exc

    latency_ms = (time.monotonic() - t_start) * 1000

    words: list[WordResult] = []
    all_segments = list(segments)
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
    """Load the Whisper model into global state."""
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
    _model_ready = True

    elapsed = (time.monotonic() - t0) * 1000
    logger.info("Whisper model loaded in %.0f ms", elapsed)


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
