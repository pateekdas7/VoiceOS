#!/usr/bin/env python3
"""
VoiceOS Founder Audio Evaluator — Qwen2.5-Omni-7B
Port 8300 | Isolated from production STT/LLM/TTS pipeline

Purpose: Independent AI audio review for Sprint-029 Founder Validation only.
NOT part of the customer call path. Never called by any production service.

Accepts a WAV file, returns a structured evaluation from Qwen2.5-Omni's
perspective as a human listener. Does not have access to system internals —
evaluates only what is audible in the waveform.
"""

from __future__ import annotations

import io
import logging
import os
import sys
import time
import wave
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EVALUATOR] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/opt/voiceos-gpu/logs/evaluator.log", mode="a"),
    ],
)
log = logging.getLogger("evaluator")

MODEL_PATH = os.environ.get(
    "OMNI_MODEL_PATH", "/opt/voiceos-gpu/models/qwen2.5-omni-7b"
)
PORT = int(os.environ.get("EVALUATOR_SERVICE_PORT", "8300"))

EVALUATION_PROMPT = """\
You are an expert audio quality evaluator reviewing a speech synthesis sample. \
Listen carefully to this audio recording and evaluate it as a human listener would — \
focusing entirely on what you actually hear, not on how it may have been produced.

The audio contains a Hindi/Hinglish AI voice (collections assistant named Kavya) \
speaking to a borrower. Evaluate the following dimensions:

## VOICE QUALITY
- Overall naturalness (does it sound like a real person?)
- Human-likeness vs robotic characteristics
- Speaking pace (too fast, too slow, natural?)
- Prosody and intonation (sentence-level rhythm)
- Stress and emphasis (are the right words stressed?)
- Emotional appropriateness (empathetic, cold, forced?)

## PRONUNCIATION
- Hindi phoneme accuracy (Namaste, hun, baat, chahti, etc.)
- English loan-word pronunciation (loan, balance, payment, team)
- Hinglish code-switching (does it flow naturally between Hindi and English?)
- Accent consistency (stable throughout?)
- Name pronunciation (Prateek Das ji)
- Number pronunciation (pachaas hazaar rupay = ₹50,000)
- Currency pronunciation (rupay, rupees)
- Date pronunciation (tees July do hazaar chhabbees = 30 July 2026)

## AUDIO INTEGRITY
- Loudness consistency (does volume stay stable?)
- Clarity and intelligibility (can you understand every word?)
- Background noise, hiss, hum
- Pops, clicks, or sudden artifacts
- Codec artifacts (warbling, metallic, buzzy sounds)
- Repeated phonemes or syllables
- Repeated words or phrases
- Truncated words or sentences
- Unexpected silence or silence gaps between words
- Chunk boundaries (audible stitching points between audio segments)
- Streaming smoothness (does playback feel continuous?)
- Playback continuity (any stutters, jumps, or interruptions?)
- First-word quality (does the audio start cleanly?)
- Final-word quality (does the audio end cleanly, no cut-off?)

## CUSTOMER EXPERIENCE
- Would a borrower in India understand this clearly?
- Does it sound professional and trustworthy?
- Overall impression as a caller receiving this message

## FORMAT REQUIREMENTS
Structure your response as follows:

### OBSERVED SYMPTOMS
List only what you can directly hear — specific, concrete observations. \
Do not speculate about cause.

### MEASURED IMPRESSION (1–5 scale)
Rate each dimension: 1=very poor, 2=poor, 3=acceptable, 4=good, 5=excellent
Format: Dimension: N/5 — one-line note

### HYPOTHESES
If you hear something unusual, state what it might suggest — clearly labeled as hypothesis, \
not fact. Do not reference code, model architecture, or system internals.

### OVERALL ASSESSMENT
2–3 sentence summary of the audio quality as a human listener would experience it.

IMPORTANT:
- Describe only what you hear. Do not invent technical root causes.
- Do not reference the model name, repository, or implementation details.
- Do not say PASS or FAIL. Describe what you observe.
- Be specific: quote words or phrase sections where relevant.
- If the audio is good, say so clearly.
"""

app = FastAPI(title="VoiceOS Founder Audio Evaluator", version="1.0.0")

# ── Model state (loaded once at startup) ──────────────────────────────────────
_model = None
_processor = None
_ready = False


def _load_model() -> None:
    global _model, _processor, _ready

    log.info("Loading Qwen2.5-Omni-7B from %s …", MODEL_PATH)
    t0 = time.perf_counter()

    from transformers import (
        Qwen2_5OmniForConditionalGeneration,
        Qwen2_5OmniProcessor,
        Qwen2_5OmniConfig,
    )

    _processor = Qwen2_5OmniProcessor.from_pretrained(MODEL_PATH)
    log.info("Processor loaded in %.1fs", time.perf_counter() - t0)

    # disable_audio_output: skips the speech decoder (~1.5 GiB saved).
    # We only need audio-in (listening), not audio-out (TTS).
    config = Qwen2_5OmniConfig.from_pretrained(MODEL_PATH)
    config.enable_audio_output = False

    _model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        MODEL_PATH,
        config=config,
        dtype=torch.bfloat16,
        device_map="auto",
        attn_implementation="sdpa",   # PyTorch SDPA (built-in); flash_attn not installed
    )
    _model.eval()

    elapsed = time.perf_counter() - t0
    vram_used = torch.cuda.memory_allocated() / 1024**3
    log.info("Qwen2.5-Omni-7B loaded in %.1fs | VRAM used: %.2f GiB", elapsed, vram_used)
    _ready = True


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    _load_model()


# ── Health endpoints ──────────────────────────────────────────────────────────

@app.get("/health/live")
async def health_live() -> dict:
    return {"status": "live", "service": "voiceos-evaluator"}


@app.get("/health/ready")
async def health_ready() -> dict:
    if not _ready:
        raise HTTPException(status_code=503, detail="Model not yet loaded")
    vram_free = torch.cuda.mem_get_info()[0] / 1024**3
    return {
        "status": "ready",
        "model": "Qwen2.5-Omni-7B",
        "vram_free_gib": round(vram_free, 2),
    }


# ── Evaluate endpoint ─────────────────────────────────────────────────────────

@app.post("/evaluate")
async def evaluate(wav_file: UploadFile = File(...)) -> JSONResponse:
    """
    Accept a WAV file, run Qwen2.5-Omni audio evaluation, return structured review.
    """
    if not _ready:
        raise HTTPException(status_code=503, detail="Evaluator not ready")

    wav_bytes = await wav_file.read()
    if not wav_bytes:
        raise HTTPException(status_code=400, detail="Empty WAV file")

    # Validate WAV header
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            wav_info = {
                "channels": w.getnchannels(),
                "sample_rate": w.getframerate(),
                "duration_s": round(w.getnframes() / w.getframerate(), 3),
                "sample_width_bytes": w.getsampwidth(),
            }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid WAV: {exc}")

    # Write to temp path (Qwen2.5-Omni processor needs a file path)
    tmp_path = Path("/tmp/evaluator-input.wav")
    tmp_path.write_bytes(wav_bytes)

    log.info(
        "Evaluating WAV: %s — %.3fs @ %dHz",
        wav_file.filename,
        wav_info["duration_s"],
        wav_info["sample_rate"],
    )

    t0 = time.perf_counter()
    try:
        evaluation_text = _run_evaluation(str(tmp_path))
    except Exception as exc:
        log.exception("Evaluation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Evaluation error: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    log.info("Evaluation complete in %dms", elapsed_ms)

    return JSONResponse({
        "evaluation": evaluation_text,
        "wav_info": wav_info,
        "elapsed_ms": elapsed_ms,
        "model": "Qwen/Qwen2.5-Omni-7B",
        "filename": wav_file.filename,
    })


def _run_evaluation(audio_path: str) -> str:
    try:
        from qwen_omni_utils import process_mm_info
    except ImportError:
        process_mm_info = None

    messages = [
        {
            "role": "system",
            "content": "You are an expert audio quality evaluator. You listen carefully and describe only what you hear.",
        },
        {
            "role": "user",
            "content": [
                {"type": "audio", "audio": audio_path},
                {"type": "text", "text": EVALUATION_PROMPT},
            ],
        },
    ]

    text = _processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )

    if process_mm_info is not None:
        audios, images, videos = process_mm_info(messages, use_audio_in_video=False)
        inputs = _processor(
            text=text,
            audios=audios if audios else None,
            images=images if images else None,
            return_tensors="pt",
            padding=True,
        ).to("cuda")
    else:
        inputs = _processor(
            text=text,
            return_tensors="pt",
            padding=True,
        ).to("cuda")

    with torch.no_grad():
        output_ids = _model.generate(
            **inputs,
            generation_mode="text",     # text-only output; skip talker (not initialized)
            thinker_max_new_tokens=2048,
            do_sample=False,
            repetition_penalty=1.1,
        )

    # output_ids is just the thinker's token output (generation_mode="text")
    prompt_len = inputs["input_ids"].shape[1]
    generated  = output_ids[:, prompt_len:]
    return _processor.batch_decode(generated, skip_special_tokens=True)[0].strip()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
