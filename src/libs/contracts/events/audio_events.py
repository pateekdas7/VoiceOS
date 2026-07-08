"""Domain events for the audio pipeline (Media Gateway → VAD → STT).

Covers the full audio session lifecycle: session open/close, frame arrival,
barge-in detection, and VAD speech boundaries. These events are consumed by
the AudioSessionManager, Monitoring, and Replay layers.

Architecture: V1 Ch3 (Media Gateway), V1 Ch4 (AudioSessionManager),
              V1 Ch6 (VAD & Endpointing), V3 Ch3 (Event Bus); V6 Ch6 EV-1.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..primitives import CallId
from .envelope import DomainEvent


class AudioSessionStarted(DomainEvent):
    """Emitted when the Media Gateway opens a new audio session for an inbound call.

    Triggers AudioSessionManager initialisation and pre-allocates the frame
    buffer (V1 Ch4.3). The ``audio_config`` captures the wire codec so all
    downstream stages know what encoding to expect.
    """

    event_type: Literal["audio.session.started"] = "audio.session.started"
    call_id: CallId
    caller_phone: str
    """E.164 caller phone number as received from the carrier."""
    encoding: str
    """Wire codec: 'mulaw' | 'alaw' | 'pcm16le' (V1 Ch3.4)."""
    sample_rate: int = Field(ge=8000, le=48000)
    """Sample rate in Hz (8000 for G.711 telephony)."""
    channels: int = Field(default=1, ge=1, le=2)


class AudioFrameReceived(DomainEvent):
    """Emitted for each PCM audio frame arriving from the Media Gateway.

    High-frequency event — only persisted when replay or forensic analysis
    is enabled for the call. The ``seq`` field is the monotone counter from
    AudioFrame used for jitter-buffer reordering (V1 Ch4).
    """

    event_type: Literal["audio.frame.received"] = "audio.frame.received"
    call_id: CallId
    seq: int = Field(ge=0)
    """Monotonically increasing frame sequence number within the call."""
    rtp_ts: int = Field(ge=0)
    """RTP timestamp in the carrier's 8 kHz clock domain."""
    frame_size_bytes: int = Field(ge=0)
    """Byte length of the PCM payload (used for bitrate monitoring)."""


class BargeinDetected(DomainEvent):
    """Emitted when the VAD detects customer speech while agent audio is playing.

    Triggers immediate flush of the TTS playback buffer (V1 Ch6.8). The
    ``playback_seq`` field lets the PlaybackEngine identify which clause was
    interrupted.

    Architecture: V1 Ch6 (VAD Barge-in), V1 Ch17 (TTS Playback).
    """

    event_type: Literal["audio.barge_in.detected"] = "audio.barge_in.detected"
    call_id: CallId
    detected_at_ms: int = Field(ge=0)
    """Milliseconds from call start when barge-in was detected."""
    playback_seq: int = Field(ge=0)
    """Sequence number of the TTS clause being played at interrupt time."""


class VADSpeechStart(DomainEvent):
    """Emitted when the VAD detects the start of a customer utterance.

    Opens the STT streaming window. The ``start_ms`` is used for aligning
    word hypotheses to the call timeline (V1 Ch6, V1 Ch8).
    """

    event_type: Literal["audio.vad.speech_start"] = "audio.vad.speech_start"
    call_id: CallId
    start_ms: int = Field(ge=0)
    """Milliseconds from call start when speech onset was detected."""
    energy_db: float
    """Short-term energy in dBFS at detection time (used for threshold tuning)."""


class VADSpeechEnd(DomainEvent):
    """Emitted when the VAD detects end-of-utterance (endpointing).

    Signals the STT adapter to finalise the transcript and the Dialogue
    Manager to begin turn processing (V1 Ch6.7, V1 Ch9).
    """

    event_type: Literal["audio.vad.speech_end"] = "audio.vad.speech_end"
    call_id: CallId
    start_ms: int = Field(ge=0)
    """Milliseconds from call start of the utterance onset (links to VADSpeechStart)."""
    end_ms: int = Field(ge=0)
    """Milliseconds from call start of the utterance end."""
    duration_ms: int = Field(ge=0)
    """Utterance duration in milliseconds (end_ms - start_ms)."""


class AudioSessionEnded(DomainEvent):
    """Emitted when the audio session terminates (call hung up, timeout, error).

    Triggers AudioSessionManager teardown, buffer flush, and call disposition
    event emission. The ``reason`` is a stable code for alerting and analytics.
    """

    event_type: Literal["audio.session.ended"] = "audio.session.ended"
    call_id: CallId
    reason: str
    """Termination reason code: 'customer_hangup' | 'agent_hangup' | 'timeout' |
    'carrier_disconnect' | 'error'."""
    duration_ms: int = Field(ge=0)
    """Total call duration in milliseconds."""


class BackchannelDetected(DomainEvent):
    """Emitted when a short utterance during agent playback is classified as a
    backchannel filler rather than a full barge-in turn.

    A backchannel is a short acknowledgement (< 800 ms) such as "hmm", "haan",
    or "theek hai" that the customer produces while the agent is speaking.
    Unlike BargeinDetected, this event does NOT flush the playback buffer —
    the agent continues speaking.

    The short utterance is still passed to STT for transcription, but the
    Dialogue Manager does NOT yield the turn.

    Architecture: V1 Ch6.8 (Backchannel Discrimination); DocSuite-02.
    """

    event_type: Literal["audio.backchannel.detected"] = "audio.backchannel.detected"
    call_id: CallId
    detected_at_ms: int = Field(ge=0)
    """Milliseconds from call start when the backchannel utterance was detected."""
    duration_ms: int = Field(ge=0)
    """Duration of the backchannel utterance in milliseconds (always < 800 ms)."""


__all__ = [
    "AudioFrameReceived",
    "AudioSessionEnded",
    "AudioSessionStarted",
    "BackchannelDetected",
    "BargeinDetected",
    "VADSpeechEnd",
    "VADSpeechStart",
]
