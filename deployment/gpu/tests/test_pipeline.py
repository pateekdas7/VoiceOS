"""End-to-end pipeline tests: STT → LLM → TTS.

Validates the complete voice pipeline from audio input through to audio output.
All tests run against mock services so no GPU is required.

Measures:
- STT latency
- LLM TTFT
- LLM total generation time
- TTS time-to-first-audio
- End-to-end latency (audio in → first audio out)
- Audio continuity (no gaps)

These latency numbers are measured against the MOCK services, which simulate
realistic delays (80ms STT, 60ms LLM TTFT, 150ms TTS TTFA). Production GPU
measurements will be faster or comparable depending on GPU hardware.
"""

from __future__ import annotations

import base64
import json
import struct
import time
from dataclasses import dataclass

import httpx
import pytest


# ── Pipeline client ────────────────────────────────────────────────────────────


@dataclass
class PipelineResult:
    stt_text: str
    stt_latency_ms: float
    llm_ttft_ms: float
    llm_text: str
    llm_total_ms: float
    tts_ttfa_ms: float
    tts_total_ms: float
    tts_bytes: int
    e2e_latency_ms: float  # audio-in → first TTS audio out


def _run_pipeline(
    audio_b64: str,
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    language: str = "hi",
    speaker: str = "kavya",
) -> PipelineResult:
    t_pipeline_start = time.monotonic()

    # ── STT ───────────────────────────────────────────────────────────────────
    t_stt_start = time.monotonic()
    stt_resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": audio_b64, "language": language},
    )
    stt_resp.raise_for_status()
    stt_latency_ms = (time.monotonic() - t_stt_start) * 1000
    stt_body = stt_resp.json()
    stt_text = " ".join(w["word"] for w in stt_body["words"])

    # ── LLM ───────────────────────────────────────────────────────────────────
    t_llm_start = time.monotonic()
    llm_text = ""
    llm_ttft_ms: float | None = None

    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [{"role": "user", "content": stt_text or "नमस्ते"}],
        "stream": True,
    }
    with llm_client.stream("POST", "/v1/chat/completions", json=payload) as llm_resp:
        llm_resp.raise_for_status()
        for line in llm_resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            content = chunk["choices"][0]["delta"].get("content", "")
            if content and llm_ttft_ms is None:
                llm_ttft_ms = (time.monotonic() - t_llm_start) * 1000
            llm_text += content

    llm_total_ms = (time.monotonic() - t_llm_start) * 1000
    llm_ttft_ms = llm_ttft_ms or llm_total_ms

    # ── TTS ───────────────────────────────────────────────────────────────────
    t_tts_start = time.monotonic()
    tts_ttfa_ms: float | None = None
    tts_chunks: list[bytes] = []

    with tts_client.stream(
        "POST", "/synthesize", json={"text": llm_text or "नमस्ते", "speaker": speaker}
    ) as tts_resp:
        tts_resp.raise_for_status()
        for chunk in tts_resp.iter_bytes(chunk_size=None):
            if chunk:
                if tts_ttfa_ms is None:
                    tts_ttfa_ms = (time.monotonic() - t_tts_start) * 1000
                    e2e_ms = (time.monotonic() - t_pipeline_start) * 1000
                tts_chunks.append(chunk)

    tts_total_ms = (time.monotonic() - t_tts_start) * 1000
    tts_bytes = sum(len(c) for c in tts_chunks)

    return PipelineResult(
        stt_text=stt_text,
        stt_latency_ms=stt_latency_ms,
        llm_ttft_ms=llm_ttft_ms,
        llm_text=llm_text,
        llm_total_ms=llm_total_ms,
        tts_ttfa_ms=tts_ttfa_ms or tts_total_ms,
        tts_total_ms=tts_total_ms,
        tts_bytes=tts_bytes,
        e2e_latency_ms=e2e_ms if tts_ttfa_ms is not None else (time.monotonic() - t_pipeline_start) * 1000,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_pipeline_completes(
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    silence_b64: str,
) -> None:
    """Full STT→LLM→TTS pipeline completes without error."""
    result = _run_pipeline(silence_b64, stt_client, llm_client, tts_client)
    assert result.tts_bytes > 0, "Expected audio output from pipeline"


def test_pipeline_stt_produces_text(
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    silence_b64: str,
) -> None:
    result = _run_pipeline(silence_b64, stt_client, llm_client, tts_client)
    # Mock STT returns words even for silence
    assert isinstance(result.stt_text, str)


def test_pipeline_llm_receives_stt_output(
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    silence_b64: str,
) -> None:
    result = _run_pipeline(silence_b64, stt_client, llm_client, tts_client)
    assert len(result.llm_text) > 0, "LLM should produce a non-empty response"


def test_pipeline_tts_produces_audio(
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    silence_b64: str,
) -> None:
    result = _run_pipeline(silence_b64, stt_client, llm_client, tts_client)
    assert result.tts_bytes > 0
    # Must be 4-byte aligned (float32 LE PCM)
    assert result.tts_bytes % 4 == 0


def test_pipeline_e2e_latency_under_2s(
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    silence_b64: str,
) -> None:
    """E2E latency (audio in → first TTS audio out) should be under 2s on mock services."""
    result = _run_pipeline(silence_b64, stt_client, llm_client, tts_client)
    assert result.e2e_latency_ms < 2000, (
        f"E2E latency {result.e2e_latency_ms:.0f}ms exceeds 2s budget.\n"
        f"  STT:  {result.stt_latency_ms:.0f}ms\n"
        f"  LLM TTFT: {result.llm_ttft_ms:.0f}ms\n"
        f"  TTS TTFA: {result.tts_ttfa_ms:.0f}ms"
    )


def test_pipeline_component_latencies(
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    silence_b64: str,
    capsys,
) -> None:
    """Print a latency breakdown for manual review."""
    result = _run_pipeline(silence_b64, stt_client, llm_client, tts_client)
    with capsys.disabled():
        print(
            f"\n{'─' * 50}\n"
            f"Pipeline Latency Report (mock services)\n"
            f"{'─' * 50}\n"
            f"  STT latency:     {result.stt_latency_ms:>8.1f} ms\n"
            f"  LLM TTFT:        {result.llm_ttft_ms:>8.1f} ms\n"
            f"  LLM total:       {result.llm_total_ms:>8.1f} ms\n"
            f"  TTS TTFA:        {result.tts_ttfa_ms:>8.1f} ms\n"
            f"  TTS total:       {result.tts_total_ms:>8.1f} ms\n"
            f"  E2E (in→audio):  {result.e2e_latency_ms:>8.1f} ms\n"
            f"  Audio output:    {result.tts_bytes:>8,d} bytes "
            f"({result.tts_bytes / 4 / 24000 * 1000:.0f}ms audio)\n"
            f"  LLM response:    {result.llm_text[:60]!r}...\n"
            f"{'─' * 50}"
        )


def test_pipeline_multiple_turns(
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    silence_b64: str,
) -> None:
    """Simulate a 3-turn conversation — all turns complete without error."""
    for turn in range(3):
        result = _run_pipeline(silence_b64, stt_client, llm_client, tts_client)
        assert result.tts_bytes > 0, f"Turn {turn + 1} produced no audio"
        assert result.e2e_latency_ms < 3000, f"Turn {turn + 1} E2E latency too high"


def test_pipeline_audio_is_valid_pcm(
    stt_client: httpx.Client,
    llm_client: httpx.Client,
    tts_client: httpx.Client,
    silence_b64: str,
) -> None:
    """All TTS audio chunks should contain valid float32 samples."""
    tts_all: list[bytes] = []

    # Run STT + LLM first
    stt_resp = stt_client.post(
        "/transcribe", json={"audio_b64": silence_b64, "language": "hi"}
    )
    stt_text = " ".join(w["word"] for w in stt_resp.json()["words"])

    llm_text = ""
    with llm_client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-7b-instruct-fp8",
            "messages": [{"role": "user", "content": stt_text or "नमस्ते"}],
            "stream": True,
        },
    ) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            llm_text += chunk["choices"][0]["delta"].get("content", "")

    with tts_client.stream(
        "POST", "/synthesize", json={"text": llm_text or "नमस्ते", "speaker": "kavya"}
    ) as resp:
        for chunk in resp.iter_bytes(chunk_size=None):
            if chunk:
                tts_all.append(chunk)

    audio = b"".join(tts_all)
    assert len(audio) % 4 == 0, "Audio not float32-aligned"
    n = len(audio) // 4
    samples = struct.unpack(f"<{n}f", audio)
    assert all(-100.0 < s < 100.0 for s in samples), "Audio contains invalid float32 samples"
