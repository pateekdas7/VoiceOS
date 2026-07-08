"""Unit tests for FakeRTPStream and make_wav_bytes audio harness.

Verifies that FakeRTPStream generates well-formed AudioFrame sequences
with correct sequencing, byte patterns, and silence/speech toggles.

Architecture: V1 Ch3 (AudioFrame contract); V6 Ch9 (Testing Standards).
"""

from __future__ import annotations

from src.libs.contracts.audio import Encoding, SampleRate
from tests.fixtures.audio import FakeRTPStream, make_wav_bytes

# ---------------------------------------------------------------------------
# FakeRTPStream
# ---------------------------------------------------------------------------


class TestFakeRTPStreamSequencing:
    """Sequence and timestamp increment behaviour."""

    def test_initial_seq_is_zero(self) -> None:
        stream = FakeRTPStream()
        assert stream.current_seq == 0

    def test_initial_rtp_ts_is_zero(self) -> None:
        stream = FakeRTPStream()
        assert stream.current_rtp_ts == 0

    def test_seq_increments_by_one_per_frame(self) -> None:
        stream = FakeRTPStream()
        f0 = stream.generate_frame()
        f1 = stream.generate_frame()
        assert f0.seq == 0
        assert f1.seq == 1

    def test_rtp_ts_increments_by_samples_per_frame(self) -> None:
        """RTP timestamp must advance by SAMPLES_PER_FRAME (160) each frame."""
        stream = FakeRTPStream()
        f0 = stream.generate_frame()
        f1 = stream.generate_frame()
        assert f0.rtp_ts == 0
        assert f1.rtp_ts == FakeRTPStream.SAMPLES_PER_FRAME

    def test_seq_wraps_at_65536(self) -> None:
        """seq must wrap at 2^16 per RFC 3550."""
        stream = FakeRTPStream(start_seq=65535)
        f_last = stream.generate_frame()
        f_wrap = stream.generate_frame()
        assert f_last.seq == 65535
        assert f_wrap.seq == 0

    def test_custom_start_seq_is_honoured(self) -> None:
        stream = FakeRTPStream(start_seq=100)
        frame = stream.generate_frame()
        assert frame.seq == 100

    def test_custom_start_rtp_ts_is_honoured(self) -> None:
        stream = FakeRTPStream(start_rtp_ts=1000)
        frame = stream.generate_frame()
        assert frame.rtp_ts == 1000

    def test_generate_frames_yields_correct_count(self) -> None:
        stream = FakeRTPStream()
        frames = list(stream.generate_frames(7))
        assert len(frames) == 7

    def test_generate_frames_are_sequentially_numbered(self) -> None:
        stream = FakeRTPStream()
        frames = list(stream.generate_frames(4))
        for i, frame in enumerate(frames):
            assert frame.seq == i


class TestFakeRTPStreamPayload:
    """Frame byte content for silence vs. speech."""

    def test_silence_frame_has_160_bytes(self) -> None:
        """8 kHz x 20 ms = 160 samples per mu-law frame."""
        stream = FakeRTPStream()
        frame = stream.generate_frame()
        assert len(frame.pcm_data) == 160

    def test_silence_frame_bytes_are_0xff(self) -> None:
        """Silence payload must be all 0xFF (G.711 μ-law digital silence)."""
        stream = FakeRTPStream()
        frame = stream.generate_frame()
        assert all(b == 0xFF for b in frame.pcm_data)

    def test_speech_frame_has_160_bytes(self) -> None:
        stream = FakeRTPStream(is_speech=True)
        frame = stream.generate_frame()
        assert len(frame.pcm_data) == 160

    def test_speech_frame_is_not_all_silence(self) -> None:
        """Speech payload must not be all 0xFF."""
        stream = FakeRTPStream(is_speech=True)
        frame = stream.generate_frame()
        assert not all(b == 0xFF for b in frame.pcm_data)

    def test_speech_differs_from_silence(self) -> None:
        silence_stream = FakeRTPStream(is_speech=False)
        speech_stream = FakeRTPStream(is_speech=True)
        silence = silence_stream.generate_frame()
        speech = speech_stream.generate_frame()
        assert silence.pcm_data != speech.pcm_data


class TestFakeRTPStreamToggle:
    """toggle_speech and generate_frames is_speech override."""

    def test_toggle_silence_to_speech(self) -> None:
        stream = FakeRTPStream()
        silence = stream.generate_frame()
        stream.toggle_speech(is_speech=True)
        speech = stream.generate_frame()
        assert all(b == 0xFF for b in silence.pcm_data)
        assert not all(b == 0xFF for b in speech.pcm_data)

    def test_toggle_speech_back_to_silence(self) -> None:
        stream = FakeRTPStream(is_speech=True)
        speech = stream.generate_frame()
        stream.toggle_speech(is_speech=False)
        silence = stream.generate_frame()
        assert not all(b == 0xFF for b in speech.pcm_data)
        assert all(b == 0xFF for b in silence.pcm_data)

    def test_generate_frames_speech_override(self) -> None:
        """Passing is_speech=True to generate_frames overrides silence state."""
        stream = FakeRTPStream(is_speech=False)
        speech_frames = list(stream.generate_frames(3, is_speech=True))
        for frame in speech_frames:
            assert not all(b == 0xFF for b in frame.pcm_data)

    def test_generate_frames_restores_state_after_override(self) -> None:
        """State reverts to silence after a speech-override batch."""
        stream = FakeRTPStream(is_speech=False)
        list(stream.generate_frames(2, is_speech=True))
        silence = stream.generate_frame()
        assert all(b == 0xFF for b in silence.pcm_data)

    def test_generate_frames_silence_override_on_speech_stream(self) -> None:
        """is_speech=False override produces silence frames from a speech stream."""
        stream = FakeRTPStream(is_speech=True)
        silence_frames = list(stream.generate_frames(2, is_speech=False))
        for frame in silence_frames:
            assert all(b == 0xFF for b in frame.pcm_data)


class TestFakeRTPStreamAudioConfig:
    """AudioFrame config field values."""

    def test_frame_encoding_is_mulaw(self) -> None:
        frame = FakeRTPStream().generate_frame()
        assert frame.config.encoding == Encoding.MULAW

    def test_frame_sample_rate_is_8k(self) -> None:
        frame = FakeRTPStream().generate_frame()
        assert frame.config.sample_rate == SampleRate.RATE_8K

    def test_frame_channels_is_mono(self) -> None:
        frame = FakeRTPStream().generate_frame()
        assert frame.config.channels == 1

    def test_frame_duration_is_20ms(self) -> None:
        frame = FakeRTPStream().generate_frame()
        assert frame.config.frame_duration_ms == 20

    def test_recv_ts_is_positive(self) -> None:
        """recv_ts must be a positive monotonic clock value."""
        frame = FakeRTPStream().generate_frame()
        assert frame.recv_ts > 0.0


# ---------------------------------------------------------------------------
# make_wav_bytes
# ---------------------------------------------------------------------------


class TestMakeWavBytes:
    """Validates WAV file byte output from make_wav_bytes."""

    def test_returns_bytes(self) -> None:
        data = make_wav_bytes()
        assert isinstance(data, bytes)

    def test_riff_magic(self) -> None:
        assert make_wav_bytes()[:4] == b"RIFF"

    def test_wave_format_tag(self) -> None:
        assert make_wav_bytes()[8:12] == b"WAVE"

    def test_fmt_chunk_present(self) -> None:
        data = make_wav_bytes()
        assert data[12:16] == b"fmt "

    def test_data_chunk_present(self) -> None:
        """data chunk marker must appear after fmt chunk."""
        data = make_wav_bytes()
        assert b"data" in data

    def test_total_length_for_160_samples(self) -> None:
        """RIFF(12) + fmt(24) + data_hdr(8) + 160 samples = 204 bytes."""
        data = make_wav_bytes(num_samples=160)
        assert len(data) == 204

    def test_silence_and_speech_have_same_length(self) -> None:
        silence = make_wav_bytes(silence=True)
        speech = make_wav_bytes(silence=False)
        assert len(silence) == len(speech)

    def test_silence_and_speech_have_different_audio_data(self) -> None:
        silence = make_wav_bytes(silence=True)
        speech = make_wav_bytes(silence=False)
        assert silence != speech

    def test_custom_sample_rate_accepted(self) -> None:
        data = make_wav_bytes(sample_rate=16000, num_samples=320)
        assert data[:4] == b"RIFF"
        assert len(data) == 204 + 160  # 44 header + 320 samples
