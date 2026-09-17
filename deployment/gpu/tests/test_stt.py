"""STT service tests — runs against mock STT server.

Validates the complete HTTP contract of the STT service:
- Health endpoints (liveness + readiness)
- Metrics endpoint (Prometheus format)
- Transcription request/response schema
- Input validation (empty audio, bad base64, missing fields)
- Concurrency (multiple simultaneous requests)
- Latency measurement
"""

from __future__ import annotations

import base64
import concurrent.futures
import struct
import time

import httpx
import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────


def _pcm16le(duration_ms: int = 1000) -> bytes:
    n = 16 * duration_ms  # 16000 samples/s → 16 samples/ms
    return struct.pack(f"<{n}h", *([0] * n))


def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")


# ── Health endpoints ───────────────────────────────────────────────────────────


def test_liveness(stt_client: httpx.Client) -> None:
    resp = stt_client.get("/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "alive"
    assert body["service"] == "voiceos-stt"


def test_readiness(stt_client: httpx.Client) -> None:
    resp = stt_client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert "model" in body
    assert "mock" in body


def test_readiness_reports_mock_mode(stt_client: httpx.Client) -> None:
    resp = stt_client.get("/health/ready")
    assert resp.status_code == 200
    # When running in mock mode the server reports mock=true and vram_target_mb=0
    body = resp.json()
    assert body["mock"] is True
    assert body["vram_target_mb"] == 0


# ── Metrics endpoint ───────────────────────────────────────────────────────────


def test_metrics_format(stt_client: httpx.Client) -> None:
    resp = stt_client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    text = resp.text
    assert "voiceos_stt_requests_total" in text
    assert "voiceos_stt_model_ready" in text
    assert "voiceos_stt_latency_ms" in text


def test_metrics_model_ready(stt_client: httpx.Client) -> None:
    resp = stt_client.get("/metrics")
    assert "voiceos_stt_model_ready 1" in resp.text


# ── Transcription: happy path ─────────────────────────────────────────────────


def test_transcribe_silence_returns_words(stt_client: httpx.Client, silence_b64: str) -> None:
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": silence_b64, "language": "hi"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "words" in body
    assert "language" in body
    assert "duration_ms" in body
    assert "latency_ms" in body
    assert isinstance(body["words"], list)
    assert body["duration_ms"] == pytest.approx(1000, abs=10)


def test_transcribe_response_schema(stt_client: httpx.Client, silence_b64: str) -> None:
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": silence_b64, "language": "hi"},
    )
    body = resp.json()
    for word in body["words"]:
        assert "word" in word
        assert "confidence" in word
        assert "start_ms" in word
        assert "end_ms" in word
        assert "is_final" in word
        assert 0.0 <= word["confidence"] <= 1.0
        assert word["start_ms"] >= 0
        assert word["end_ms"] >= word["start_ms"]


def test_transcribe_tone_returns_words(stt_client: httpx.Client, tone_b64: str) -> None:
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": tone_b64, "language": "hi"},
    )
    assert resp.status_code == 200
    assert "words" in resp.json()


def test_transcribe_auto_language(stt_client: httpx.Client, silence_b64: str) -> None:
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": silence_b64, "language": ""},
    )
    assert resp.status_code == 200


def test_transcribe_last_word_is_final(stt_client: httpx.Client, silence_b64: str) -> None:
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": silence_b64, "language": "hi"},
    )
    words = resp.json()["words"]
    if words:
        assert words[-1]["is_final"] is True


# ── Transcription: latency ─────────────────────────────────────────────────────


def test_transcribe_latency_under_500ms(stt_client: httpx.Client, silence_b64: str) -> None:
    t0 = time.monotonic()
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": silence_b64, "language": "hi"},
    )
    latency_ms = (time.monotonic() - t0) * 1000
    assert resp.status_code == 200
    assert latency_ms < 500, f"STT latency {latency_ms:.0f}ms exceeds 500ms target"


def test_transcribe_latency_reported_in_response(stt_client: httpx.Client, silence_b64: str) -> None:
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": silence_b64, "language": "hi"},
    )
    body = resp.json()
    assert body["latency_ms"] > 0
    assert body["latency_ms"] < 10_000  # sanity upper bound


# ── Input validation ───────────────────────────────────────────────────────────


def test_transcribe_empty_audio_400(stt_client: httpx.Client) -> None:
    empty_b64 = base64.b64encode(b"").decode("ascii")
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": empty_b64, "language": "hi"},
    )
    assert resp.status_code == 400


def test_transcribe_bad_base64_400(stt_client: httpx.Client) -> None:
    resp = stt_client.post(
        "/transcribe",
        json={"audio_b64": "NOT_VALID_BASE64!!!", "language": "hi"},
    )
    assert resp.status_code == 400


def test_transcribe_missing_audio_422(stt_client: httpx.Client) -> None:
    resp = stt_client.post("/transcribe", json={"language": "hi"})
    assert resp.status_code == 422


def test_transcribe_truncated_pcm(stt_client: httpx.Client) -> None:
    odd_bytes = b"\x00" * 3  # odd byte count — valid base64 but invalid PCM16LE
    b64 = base64.b64encode(odd_bytes).decode("ascii")
    resp = stt_client.post("/transcribe", json={"audio_b64": b64, "language": "hi"})
    # Either processes the truncatable portion (200) or returns 400 — both are acceptable
    assert resp.status_code in (200, 400)


# ── Concurrency ────────────────────────────────────────────────────────────────


def test_transcribe_concurrent_requests(stt_client: httpx.Client, silence_b64: str) -> None:
    """Mock STT serializes requests via thread pool; all should succeed."""
    n = 5
    payload = {"audio_b64": silence_b64, "language": "hi"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(stt_client.post, "/transcribe", json=payload) for _ in range(n)
        ]
        results = [f.result() for f in futures]
    assert all(r.status_code == 200 for r in results), [
        r.status_code for r in results if r.status_code != 200
    ]


# ── Metrics counter increments ─────────────────────────────────────────────────


def test_metrics_success_counter_increments(stt_client: httpx.Client, silence_b64: str) -> None:
    before = stt_client.get("/metrics").text
    before_count = _extract_metric(before, 'voiceos_stt_requests_total{status="success"}')

    stt_client.post("/transcribe", json={"audio_b64": silence_b64, "language": "hi"})

    after = stt_client.get("/metrics").text
    after_count = _extract_metric(after, 'voiceos_stt_requests_total{status="success"}')
    assert after_count == before_count + 1


def _extract_metric(text: str, name: str) -> float:
    for line in text.splitlines():
        if line.startswith(name + " "):
            return float(line.split()[-1])
    return 0.0
