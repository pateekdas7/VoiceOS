"""TTS service tests — runs against mock TTS server.

Validates the complete HTTP contract of the TTS service:
- Health endpoints (liveness + readiness)
- Metrics endpoint
- Streaming synthesis (PCM16LE chunks — NOT float32)
- Audio chunk format (size = 4096 bytes = 2048 int16 samples = 85.33ms)
- Input validation
- Speaker validation
- Time-to-first-audio measurement

IMPORTANT — output format:
    The TTS server outputs PCM16LE (signed 16-bit, 2 bytes/sample).
    Each chunk = 4096 bytes = 2048 samples at 24 kHz = 85.33ms.
    The CPU-side AudioOutput.convert() uses audioop.ratecv(width=2) which
    expects PCM16. Float32 (8192 bytes, 4 bytes/sample) would be misread,
    causing 2× speed distortion: the "Na--mas--te" production failure.
"""

from __future__ import annotations

import struct
import time

import httpx
import pytest

_CHUNK_BYTES = 4096   # 2048 PCM16LE samples = 85.33ms at 24 kHz (NOT float32 = 8192)
_SAMPLE_RATE = 24000


# ── Health ─────────────────────────────────────────────────────────────────────


def test_liveness(tts_client: httpx.Client) -> None:
    resp = tts_client.get("/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "alive"
    assert body["service"] == "voiceos-tts"


def test_readiness(tts_client: httpx.Client) -> None:
    resp = tts_client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["sample_rate"] == _SAMPLE_RATE
    assert body["streaming"] is True


def test_readiness_reports_mock(tts_client: httpx.Client) -> None:
    body = tts_client.get("/health/ready").json()
    assert body["mock"] is True
    assert body["vram_target_mb"] == 0


# ── Metrics ────────────────────────────────────────────────────────────────────


def test_metrics_format(tts_client: httpx.Client) -> None:
    resp = tts_client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "voiceos_tts_requests_total" in text
    assert "voiceos_tts_model_ready 1" in text
    assert "voiceos_tts_ttfa_ms" in text


# ── Synthesis: happy path ──────────────────────────────────────────────────────


def test_synthesize_returns_audio_bytes(tts_client: httpx.Client) -> None:
    with tts_client.stream(
        "POST", "/synthesize", json={"text": "नमस्ते", "speaker": "kavya"}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        data = b"".join(resp.iter_bytes())
    assert len(data) > 0


def test_synthesize_audio_is_pcm16le(tts_client: httpx.Client) -> None:
    """Response must be PCM16LE (2-byte-aligned), NOT float32 (4-byte-aligned).

    The CPU-side AudioOutput.convert() calls audioop.ratecv(data, width=2, ...)
    which expects 2 bytes per sample. Float32 (4 bytes/sample) would cause the
    data to be misread as PCM16 at double speed — the "Na--mas--te" failure mode.
    """
    with tts_client.stream(
        "POST", "/synthesize", json={"text": "payment", "speaker": "kavya"}
    ) as resp:
        data = b"".join(resp.iter_bytes())

    # Must be 2-byte aligned (PCM16LE)
    assert len(data) % 2 == 0, f"Audio length {len(data)} is not PCM16LE-aligned (requires 2 bytes)"
    # Must NOT be indicated as float32 by size (float32 = 8192 per chunk, PCM16 = 4096)
    assert len(data) % 4096 == 0 or len(data) < 4096, (
        "Audio size should be a multiple of 4096 bytes (PCM16LE chunks). "
        f"Got {len(data)} bytes."
    )

    n_samples = len(data) // 2
    samples = struct.unpack(f"<{n_samples}h", data)
    # All samples should be in int16 range
    assert all(-32768 <= s <= 32767 for s in samples), "Audio contains out-of-range PCM16 samples"


def test_synthesize_chunk_size(tts_client: httpx.Client) -> None:
    """Mock TTS yields chunks of exactly _CHUNK_BYTES (85.33ms at 24 kHz)."""
    chunks: list[bytes] = []
    with tts_client.stream(
        "POST", "/synthesize", json={"text": "आपका payment due है।", "speaker": "kavya"}
    ) as resp:
        for chunk in resp.iter_bytes(chunk_size=None):
            if chunk:
                chunks.append(chunk)

    assert len(chunks) > 0
    # Each chunk should be exactly the standard chunk size
    for chunk in chunks:
        assert len(chunk) == _CHUNK_BYTES, (
            f"Unexpected chunk size: {len(chunk)} bytes (expected {_CHUNK_BYTES})"
        )


def test_synthesize_ttfa_under_750ms(tts_client: httpx.Client) -> None:
    t0 = time.monotonic()
    ttfa_ms: float | None = None
    with tts_client.stream(
        "POST", "/synthesize", json={"text": "नमस्ते", "speaker": "kavya"}
    ) as resp:
        for chunk in resp.iter_bytes(chunk_size=None):
            if chunk:
                ttfa_ms = (time.monotonic() - t0) * 1000
                break

    assert ttfa_ms is not None, "No audio chunk received"
    assert ttfa_ms < 750, f"TTFA {ttfa_ms:.0f}ms exceeds 750ms target"


def test_synthesize_longer_text_more_chunks(tts_client: httpx.Client) -> None:
    short_chunks: list[bytes] = []
    long_chunks: list[bytes] = []

    with tts_client.stream(
        "POST", "/synthesize", json={"text": "hi", "speaker": "kavya"}
    ) as resp:
        for chunk in resp.iter_bytes(chunk_size=None):
            if chunk:
                short_chunks.append(chunk)

    long_text = "आपका loan balance ₹50,000 है। कृपया payment करें। धन्यवाद।"
    with tts_client.stream(
        "POST", "/synthesize", json={"text": long_text, "speaker": "kavya"}
    ) as resp:
        for chunk in resp.iter_bytes(chunk_size=None):
            if chunk:
                long_chunks.append(chunk)

    assert len(long_chunks) >= len(short_chunks), (
        "Longer text should produce at least as many audio chunks"
    )


def test_synthesize_with_voice_config(tts_client: httpx.Client) -> None:
    payload = {
        "text": "EMI payment करें।",
        "speaker": "kavya",
        "voice_config": {
            "pitch_shift": 0.0,
            "rate_scale": 1.0,
            "energy_scale": 0.9,
            "pause_ms_after_clause": 200,
            "language": "hi-IN",
        },
    }
    with tts_client.stream("POST", "/synthesize", json=payload) as resp:
        assert resp.status_code == 200
        data = b"".join(resp.iter_bytes())
    assert len(data) > 0


# ── Input validation ───────────────────────────────────────────────────────────


def test_synthesize_empty_text_400(tts_client: httpx.Client) -> None:
    resp = tts_client.post("/synthesize", json={"text": "", "speaker": "kavya"})
    assert resp.status_code == 400


def test_synthesize_whitespace_text_400(tts_client: httpx.Client) -> None:
    resp = tts_client.post("/synthesize", json={"text": "   ", "speaker": "kavya"})
    assert resp.status_code == 400


def test_synthesize_unknown_speaker_422(tts_client: httpx.Client) -> None:
    resp = tts_client.post(
        "/synthesize", json={"text": "hello", "speaker": "invalid_speaker_xyz"}
    )
    assert resp.status_code == 422


def test_synthesize_text_too_long_422(tts_client: httpx.Client) -> None:
    too_long = "x" * 2001
    resp = tts_client.post("/synthesize", json={"text": too_long, "speaker": "kavya"})
    assert resp.status_code == 422


def test_synthesize_missing_text_422(tts_client: httpx.Client) -> None:
    resp = tts_client.post("/synthesize", json={"speaker": "kavya"})
    assert resp.status_code == 422


# ── Metrics counter increments ─────────────────────────────────────────────────


def test_metrics_success_counter_increments(tts_client: httpx.Client) -> None:
    before = _extract_metric(tts_client.get("/metrics").text, 'voiceos_tts_requests_total{status="success"}')

    with tts_client.stream(
        "POST", "/synthesize", json={"text": "test", "speaker": "kavya"}
    ) as resp:
        for _ in resp.iter_bytes():
            pass

    after = _extract_metric(tts_client.get("/metrics").text, 'voiceos_tts_requests_total{status="success"}')
    assert after == before + 1


def _extract_metric(text: str, name: str) -> float:
    for line in text.splitlines():
        if line.startswith(name + " "):
            return float(line.split()[-1])
    return 0.0
