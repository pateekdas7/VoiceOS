"""Integration test for WhisperHTTPAdapter against the real GPU node.

Path-A consolidation, Phase 3. Skipped unless GPU_NODE_HOST is set — same
requires_* skip-marker idiom as tests/integration/conftest.py's
requires_postgres/requires_redis/requires_mongodb.

Architecture: V1 Ch8; Path-A consolidation Phase 3.
"""

from __future__ import annotations

import os
import struct
from collections.abc import AsyncIterator
from math import sin, pi

import pytest

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.services.gpu_scheduler.scheduler import GPUScheduler
from src.services.gpu_scheduler.vram_ledger import VRAMLedger
from src.services.stt.adapters.whisper_http_adapter import WhisperHTTPAdapter

_GPU_NODE_HOST = os.environ.get("GPU_NODE_HOST", "")

requires_gpu_node = pytest.mark.skipif(
    not _GPU_NODE_HOST,
    reason=(
        "GPU_NODE_HOST not set — skipping real-GPU-node STT integration test. "
        "Set GPU_NODE_HOST=<ip> to enable (see deployment/GPU_NODE_STATE.md)."
    ),
)


def _tone_pcm16le(duration_s: float = 1.0, freq_hz: float = 220.0, sample_rate: int = 16_000) -> bytes:
    """A short sine tone — not real speech, just enough signal for the
    server's warmup-proven pipeline to run end-to-end without erroring on
    silence-only input. Word-level output isn't asserted; only that the
    real HTTP round trip through WhisperHTTPAdapter succeeds."""
    n = int(duration_s * sample_rate)
    samples = [int(3000 * sin(2 * pi * freq_hz * i / sample_rate)) for i in range(n)]
    return struct.pack(f"<{n}h", *samples)


async def _frames_gen(pcm: bytes, chunk_size: int = 3200) -> AsyncIterator[AudioFrame]:
    config = AudioConfig(sample_rate=SampleRate.RATE_16K, encoding=Encoding.PCM16LE)
    for i in range(0, len(pcm), chunk_size):
        yield AudioFrame(
            pcm_data=pcm[i : i + chunk_size],
            seq=i // chunk_size,
            rtp_ts=i,
            recv_ts=0.0,
            config=config,
        )


@requires_gpu_node
@pytest.mark.asyncio
async def test_whisper_http_adapter_round_trips_against_real_gpu_node() -> None:
    """A real HTTP call to the GPU node's /transcribe endpoint (port 8100)
    via WhisperHTTPAdapter completes without error and returns a well-formed
    (possibly empty, for a non-speech tone) WordHypothesis stream."""
    ledger = VRAMLedger()
    ledger.register_device("gpu0", 49_140)  # RTX A6000 — see GPU_NODE_STATE.md
    scheduler = GPUScheduler(ledger)
    adapter = WhisperHTTPAdapter(gpu_scheduler=scheduler, base_url=f"http://{_GPU_NODE_HOST}:8100")

    pcm = _tone_pcm16le()
    gen = await adapter.transcribe_stream(_frames_gen(pcm), language="hi")
    results = [hyp async for hyp in gen]

    # A pure tone may or may not be transcribed as words by Whisper — the
    # real assertion is that the full round trip (base64 encode → real HTTP
    # POST → real GPU inference → JSON decode → WordHypothesis) completed
    # without raising, proving the HTTP contract genuinely matches
    # deployment/gpu/services/stt/server.py's real, running implementation.
    assert isinstance(results, list)
    for hyp in results:
        assert 0.0 <= hyp.confidence <= 1.0
        assert hyp.start_ms >= 0
        assert hyp.end_ms >= hyp.start_ms
