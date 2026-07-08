"""Root-level shared pytest fixtures for the VoiceOS test suite.

Available to all tests in unit/, integration/, e2e/, invariants/, and
ai_eval/ directories without explicit imports.

Architecture: V6 Ch9 (Testing Standards); DocSuite-08.
"""

from __future__ import annotations

import uuid

import pytest

from src.libs.contracts.audio import AudioConfig, AudioFrame, Encoding, SampleRate
from src.libs.contracts.primitives import CallId, TenantId
from tests.fixtures.audio import FakeRTPStream
from tests.fixtures.redis import FakeRedisClient

# ---------------------------------------------------------------------------
# Identity fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_id() -> TenantId:
    """A fresh random TenantId (UUID) for each test."""
    return TenantId(str(uuid.uuid4()))


@pytest.fixture
def call_id() -> CallId:
    """A fresh random CallId (UUID) for each test."""
    return CallId(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Audio fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audio_config_mulaw() -> AudioConfig:
    """Standard telephony AudioConfig: μ-law, 8 kHz, mono, 20 ms."""
    return AudioConfig(
        sample_rate=SampleRate.RATE_8K,
        encoding=Encoding.MULAW,
        channels=1,
        frame_duration_ms=20,
    )


@pytest.fixture
def fake_rtp_stream() -> FakeRTPStream:
    """A fresh FakeRTPStream starting at seq=0, in silence mode."""
    return FakeRTPStream()


@pytest.fixture
def audio_frame() -> AudioFrame:
    """A single silence AudioFrame (μ-law, 8 kHz, 20 ms, seq=0)."""
    return FakeRTPStream().generate_frame()


# ---------------------------------------------------------------------------
# Redis fixture (no I/O)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> FakeRedisClient:
    """A fresh in-memory FakeRedisClient for unit tests (no I/O)."""
    return FakeRedisClient()
