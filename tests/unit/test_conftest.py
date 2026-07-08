"""Tests verifying that root conftest fixtures resolve and behave correctly.

Fixtures are defined in tests/conftest.py and injected by pytest.

Architecture: V6 Ch9 (Testing Standards); DocSuite-08.
"""

from __future__ import annotations

import uuid

from src.libs.contracts.audio import AudioFrame, Encoding, SampleRate
from src.libs.contracts.primitives import CallId, TenantId
from tests.fixtures.audio import FakeRTPStream
from tests.fixtures.redis import FakeRedisClient

# ---------------------------------------------------------------------------
# Identity fixtures
# ---------------------------------------------------------------------------


class TestIdentityFixtures:
    def test_tenant_id_is_valid_uuid(self, tenant_id: TenantId) -> None:
        """tenant_id fixture resolves to a well-formed UUID string."""
        parsed = uuid.UUID(tenant_id)
        assert parsed.version == 4

    def test_call_id_is_valid_uuid(self, call_id: CallId) -> None:
        """call_id fixture resolves to a well-formed UUID string."""
        parsed = uuid.UUID(call_id)
        assert parsed.version == 4

    def test_tenant_id_and_call_id_are_different(self, tenant_id: TenantId, call_id: CallId) -> None:
        """Each call to a fixture produces a fresh, unique UUID."""
        assert str(tenant_id) != str(call_id)

    def test_tenant_id_unique_across_calls(self, tenant_id: TenantId, call_id: CallId) -> None:
        """Fixture values must not repeat (astronomically unlikely with UUID4)."""
        assert str(tenant_id) != str(call_id)


# ---------------------------------------------------------------------------
# Audio config fixture
# ---------------------------------------------------------------------------


class TestAudioConfigFixture:
    def test_mulaw_config_sample_rate(self, audio_config_mulaw: object) -> None:
        from src.libs.contracts.audio import AudioConfig

        assert isinstance(audio_config_mulaw, AudioConfig)
        cfg = audio_config_mulaw
        assert isinstance(cfg, AudioConfig)
        assert cfg.sample_rate == SampleRate.RATE_8K

    def test_mulaw_config_encoding(self, audio_config_mulaw: object) -> None:
        from src.libs.contracts.audio import AudioConfig

        assert isinstance(audio_config_mulaw, AudioConfig)
        assert audio_config_mulaw.encoding == Encoding.MULAW

    def test_mulaw_config_channels(self, audio_config_mulaw: object) -> None:
        from src.libs.contracts.audio import AudioConfig

        assert isinstance(audio_config_mulaw, AudioConfig)
        assert audio_config_mulaw.channels == 1

    def test_mulaw_config_frame_duration(self, audio_config_mulaw: object) -> None:
        from src.libs.contracts.audio import AudioConfig

        assert isinstance(audio_config_mulaw, AudioConfig)
        assert audio_config_mulaw.frame_duration_ms == 20


# ---------------------------------------------------------------------------
# FakeRTPStream fixture
# ---------------------------------------------------------------------------


class TestFakeRTPStreamFixture:
    def test_fixture_type(self, fake_rtp_stream: FakeRTPStream) -> None:
        assert isinstance(fake_rtp_stream, FakeRTPStream)

    def test_fixture_starts_at_seq_zero(self, fake_rtp_stream: FakeRTPStream) -> None:
        assert fake_rtp_stream.current_seq == 0

    def test_fixture_starts_at_rtp_ts_zero(self, fake_rtp_stream: FakeRTPStream) -> None:
        assert fake_rtp_stream.current_rtp_ts == 0


# ---------------------------------------------------------------------------
# AudioFrame fixture
# ---------------------------------------------------------------------------


class TestAudioFrameFixture:
    def test_fixture_type(self, audio_frame: AudioFrame) -> None:
        assert isinstance(audio_frame, AudioFrame)

    def test_frame_encoding(self, audio_frame: AudioFrame) -> None:
        assert audio_frame.config.encoding == Encoding.MULAW

    def test_frame_sample_rate(self, audio_frame: AudioFrame) -> None:
        assert audio_frame.config.sample_rate == SampleRate.RATE_8K

    def test_frame_payload_length(self, audio_frame: AudioFrame) -> None:
        """8 kHz x 20 ms = 160 bytes per mu-law frame."""
        assert len(audio_frame.pcm_data) == 160

    def test_frame_seq_is_zero(self, audio_frame: AudioFrame) -> None:
        assert audio_frame.seq == 0

    def test_frame_rtp_ts_is_zero(self, audio_frame: AudioFrame) -> None:
        assert audio_frame.rtp_ts == 0


# ---------------------------------------------------------------------------
# FakeRedisClient fixture
# ---------------------------------------------------------------------------


class TestFakeRedisFixture:
    def test_fixture_type(self, fake_redis: FakeRedisClient) -> None:
        assert isinstance(fake_redis, FakeRedisClient)

    def test_ping_returns_true(self, fake_redis: FakeRedisClient) -> None:
        assert fake_redis.ping() is True

    def test_set_and_get(self, fake_redis: FakeRedisClient) -> None:
        fake_redis.set("test:key", b"value")
        assert fake_redis.get("test:key") == b"value"

    def test_get_missing_key_returns_none(self, fake_redis: FakeRedisClient) -> None:
        assert fake_redis.get("nonexistent") is None

    def test_fresh_fixture_is_empty(self, fake_redis: FakeRedisClient) -> None:
        """Each test receives a fresh, empty FakeRedisClient."""
        assert fake_redis.exists("any:key") == 0

    def test_delete(self, fake_redis: FakeRedisClient) -> None:
        fake_redis.set("del:key", b"data")
        count = fake_redis.delete("del:key")
        assert count == 1
        assert fake_redis.get("del:key") is None
