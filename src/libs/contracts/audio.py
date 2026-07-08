"""Audio frame and configuration contracts.

Defines the canonical representation of raw audio data moving through the
media plane (Media Gateway → Audio Session Manager → Preprocessing → VAD → STT).
All audio crossing a stage boundary is wrapped in an AudioFrame.

Architecture: V1 Ch3 (Media Gateway), V1 Ch4 (Audio Session Manager),
              V1 Appendix A; DocSuite-02 (Interface Contracts A.1).
"""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SampleRate(int, Enum):
    """Supported audio sample rates.

    8 kHz is the telephony standard (G.711); 16 kHz is the ASR input
    requirement after resampling (V1 Ch5); 24 kHz is the Veena TTS output
    rate (V1 Ch17).
    """

    RATE_8K = 8000
    RATE_16K = 16000
    RATE_24K = 24000


class Encoding(StrEnum):
    """Wire/storage audio encoding.

    PCM16LE is the canonical internal format used after codec decode.
    μ-law (PCMU) and A-law (PCMA) are telephony wire formats that the
    Media Gateway decodes before handing frames upstream.
    """

    PCM16LE = "pcm16le"
    """Signed 16-bit little-endian PCM — the canonical internal format."""

    MULAW = "mulaw"
    """ITU-T G.711 μ-law (PCMU) — 8-bit compressed, 8 kHz telephony standard."""

    ALAW = "alaw"
    """ITU-T G.711 A-law (PCMA) — 8-bit compressed, common in Europe/Asia."""


class AudioConfig(BaseModel):
    """Static configuration for an audio stream.

    Carried alongside frames so downstream stages can validate their
    assumptions without out-of-band configuration lookups.
    """

    model_config = ConfigDict(frozen=True)

    sample_rate: SampleRate
    """Sample rate of the audio data."""

    encoding: Encoding
    """Encoding format of the raw bytes."""

    channels: int = Field(default=1, ge=1, le=2)
    """Number of audio channels. VoiceOS v2 is mono (1 channel) for telephony."""

    frame_duration_ms: int = Field(default=20, ge=10, le=60)
    """Duration of one frame in milliseconds. Telephony standard is 20 ms."""


class AudioFrame(BaseModel):
    """A single audio frame from the media plane.

    Carries PCM audio data plus sequencing and timing metadata. After the
    Media Gateway decodes wire codecs, all internal audio moves as
    PCM16LE AudioFrames.

    RI-1 note: AudioFrames are produced and consumed on the real-time media
    thread. Processing must complete within the frame budget (20 ms) and
    must never block.

    Architecture: V1 Ch3.6 (Outputs), V1 Appendix A; DocSuite-02 A.1.
    """

    model_config = ConfigDict(frozen=True)

    pcm_data: bytes
    """Raw PCM audio bytes. Length = channels x samples x bytes_per_sample."""

    seq: int = Field(ge=0)
    """Monotonically increasing sequence number within the call session.
    Used for reordering in the jitter buffer (V1 Ch4)."""

    rtp_ts: int = Field(ge=0)
    """RTP timestamp in the carrier's clock domain.
    For Twilio, synthesized from a 8 kHz sample counter (V1 Ch3.12)."""

    recv_ts: float = Field(ge=0.0)
    """Local wall-clock arrival time (seconds since epoch).
    Stamped by the Media Gateway immediately on receipt for jitter measurement."""

    config: AudioConfig
    """Audio configuration for this frame."""

    is_plc: bool = False
    """True when this frame was synthesized by the PacketLossConcealer (V1 Ch4).
    Downstream consumers (VAD, STT) use this flag to discount concealment audio."""
