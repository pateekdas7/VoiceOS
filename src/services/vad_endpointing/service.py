"""VADEndpointingService — orchestrating service for VAD, endpointing, and barge-in.

Coordinates the full voice activity detection pipeline:

    AudioFrame (16 kHz PCM16LE)
        ↓
    VADEngine.process_window()  →  speech_probability
        ↓
    EndpointDetector.process()  →  VADSpeechStart / VADSpeechEnd events
        ↓ (when playback active)
    BargeinDetector.process()   →  tentative barge-in signal
        ↓ (when speech ends after barge-in)
    BackchannelDiscriminator.classify()  →  BackchannelDetected OR BargeinDetected

Input contract (from AudioPreprocessor, DocSuite-02):
    AudioFrame.config.sample_rate == 16000
    AudioFrame.config.encoding   == PCM16LE
    AudioFrame.config.channels   == 1 (mono)

Output contract:
    Emits DomainEvent objects from each processed frame:
        VADSpeechStart     — utterance onset
        VADSpeechEnd       — utterance end
        BargeinDetected    — full barge-in (≥ 800 ms utterance during playback)
        BackchannelDetected — short filler (< 800 ms utterance during playback)

Architecture: V1 Ch6 (VAD & Endpointing); DocSuite-02 (AudioPreprocessor ↔ VAD).
"""

from __future__ import annotations

import time

from src.libs.contracts.audio import AudioFrame, Encoding, SampleRate
from src.libs.contracts.events.audio_events import (
    BargeinDetected,
    VADSpeechEnd,
)
from src.libs.contracts.events.envelope import DomainEvent
from src.libs.contracts.primitives import CallId, TenantId
from src.services.vad_endpointing.backchannel import BackchannelDiscriminator
from src.services.vad_endpointing.bargein_detector import BargeinDetector
from src.services.vad_endpointing.endpoint_detector import EndpointDetector
from src.services.vad_endpointing.metrics import (
    increment_backchannel,
    increment_bargein,
    update_endpoint_latency,
    update_speech_ratio,
)
from src.services.vad_endpointing.vad_engine import VADEngine


class VADEndpointingService:
    """Orchestrates VAD, adaptive endpointing, barge-in, and backchannel detection.

    Each service instance is per-call.  Construct a new instance for each
    call session and discard it when the call ends.

    Barge-in / backchannel arbitration logic:
        1. When playback is active, BargeinDetector monitors for sustained
           speech above the 0.65 threshold for ≥ 200 ms.
        2. When BargeinDetected fires internally, the service sets a
           ``_barge_in_pending`` flag but does NOT immediately emit the event.
        3. When VADSpeechEnd fires (the utterance is confirmed over):
             - duration < 800 ms: BackchannelDetected (agent continues playing)
             - duration >= 800 ms: BargeinDetected (playback flush triggered)
        4. If speech has been ongoing for ≥ 800 ms since barge-in was detected
           (utterance not yet ended), BargeinDetected is emitted immediately so
           the PlaybackScheduler can flush without waiting for the utterance end.

    Usage::

        service = VADEndpointingService(vad_engine, endpoint_detector,
                                        bargein_detector, backchannel_discriminator)
        service.start()

        # Notify when TTS playback starts / stops
        service.set_playback_active(True, playback_seq=3)

        for frame in call_frames:
            events = service.process_frame(frame, call_id, tenant_id, call_time_ms)
            for event in events:
                event_bus.publish(event)

        service.stop()
    """

    # Frames are buffered until we have a full Silero window (512 samples = 1024 bytes)
    _WINDOW_BYTES: int = VADEngine.WINDOW_SIZE_SAMPLES * 2

    def __init__(
        self,
        vad_engine: VADEngine,
        endpoint_detector: EndpointDetector,
        bargein_detector: BargeinDetector,
        backchannel_discriminator: BackchannelDiscriminator,
    ) -> None:
        self._vad = vad_engine
        self._endpoint = endpoint_detector
        self._bargein = bargein_detector
        self._backchannel = backchannel_discriminator

        self._running: bool = False
        self._pcm_buffer: bytearray = bytearray()
        """Accumulates PCM bytes until a full 512-sample window is available."""

        # Metrics accumulators
        self._total_frames: int = 0
        self._speech_frames: int = 0

        # Barge-in arbitration state
        self._barge_in_pending: bool = False
        self._barge_in_speech_start_ms: int = 0
        self._barge_in_detected_at_ms: int = 0
        self._barge_in_emitted: bool = False
        """True once BargeinDetected has been emitted externally for this candidate."""
        self._last_frame_was_speech: bool = False
        """True when the most recently processed VAD window showed speech."""

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Mark the service as running."""
        self._running = True
        self._vad.reset()
        self._endpoint.reset()
        self._bargein.reset()
        self._pcm_buffer.clear()

    def stop(self) -> None:
        """Mark the service as stopped."""
        self._running = False

    @property
    def is_running(self) -> bool:
        """True between ``start()`` and ``stop()``."""
        return self._running

    # ------------------------------------------------------------------
    # Playback state control
    # ------------------------------------------------------------------

    def set_playback_active(self, active: bool, playback_seq: int = 0) -> None:
        """Notify the service that TTS playback has started or stopped.

        Args:
            active:       True when the agent is streaming audio to the caller.
            playback_seq: Sequence number of the currently playing TTS clause.
        """
        self._bargein.set_playback_active(active, playback_seq)
        if not active:
            # Playback stopped — cancel any pending barge-in
            self._reset_barge_in_state()

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def process_frame(
        self,
        frame: AudioFrame,
        call_id: CallId,
        tenant_id: TenantId,
        call_time_ms: int,
    ) -> list[DomainEvent]:
        """Process one audio frame and return any emitted VAD/endpoint events.

        The service buffers PCM until a full 512-sample Silero window is
        accumulated.  For 20 ms frames (320 samples at 16 kHz) this means a
        new VAD probability is produced every ~64 ms (two frames buffered);
        for 32 ms frames (512 samples) every frame produces one window.

        Args:
            frame:        PCM16LE AudioFrame at 16 kHz mono.
            call_id:      Call identifier threaded through to events.
            tenant_id:    Tenant scope.
            call_time_ms: Current call position in milliseconds.

        Returns:
            List of DomainEvent objects emitted for this frame.

        Raises:
            ValueError: When the frame has an unsupported sample rate or encoding.
        """
        self._validate_frame(frame)

        self._pcm_buffer.extend(frame.pcm_data)
        events: list[DomainEvent] = []

        while len(self._pcm_buffer) >= self._WINDOW_BYTES:
            window = bytes(self._pcm_buffer[: self._WINDOW_BYTES])
            del self._pcm_buffer[: self._WINDOW_BYTES]
            window_events = self._process_window(window, call_id, tenant_id, call_time_ms)
            events.extend(window_events)

        return events

    def _process_window(
        self,
        window: bytes,
        call_id: CallId,
        tenant_id: TenantId,
        call_time_ms: int,
    ) -> list[DomainEvent]:
        t0 = time.perf_counter()
        probability = self._vad.process_window(window)

        self._total_frames += 1
        self._last_frame_was_speech = self._vad.is_speech(probability)
        if self._last_frame_was_speech:
            self._speech_frames += 1

        update_speech_ratio(str(call_id), self._speech_frames, self._total_frames)

        # --- Endpoint detection ---
        endpoint_events = self._endpoint.process(
            probability,
            call_time_ms,
            call_id,
            tenant_id,
        )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        update_endpoint_latency(str(call_id), elapsed_ms)

        # --- Barge-in detection (only when playback active) ---
        bargein_raw: BargeinDetected | None = self._bargein.process(
            probability,
            call_time_ms,
            call_id,
            tenant_id,
        )

        if bargein_raw is not None and not self._barge_in_pending:
            # First barge-in trigger for this candidate
            self._barge_in_pending = True
            self._barge_in_detected_at_ms = call_time_ms
            # Speech start is approximately required_duration before detection
            self._barge_in_speech_start_ms = max(0, call_time_ms - 200)

        # --- Arbitrate barge-in vs. backchannel ---
        output_events: list[DomainEvent] = []
        for event in endpoint_events:
            if isinstance(event, VADSpeechEnd) and self._barge_in_pending and not self._barge_in_emitted:
                duration_ms = event.end_ms - self._barge_in_speech_start_ms
                decision = self._backchannel.classify(
                    duration_ms,
                    call_id,
                    tenant_id,
                    event.end_ms,
                )
                if decision is not None:
                    # Short utterance — backchannel, suppress barge-in
                    output_events.append(decision)
                    increment_backchannel(str(call_id))
                else:
                    # Long utterance — confirm barge-in
                    output_events.append(
                        BargeinDetected(
                            call_id=call_id,
                            tenant_id=tenant_id,
                            detected_at_ms=self._barge_in_detected_at_ms,
                            playback_seq=bargein_raw.playback_seq if bargein_raw else 0,
                        )
                    )
                    increment_bargein(str(call_id))
                    self._barge_in_emitted = True
                self._reset_barge_in_state()
                output_events.append(event)
            else:
                output_events.append(event)

        # Emit barge-in immediately when speech is still active and utterance has
        # reached 800 ms from estimated speech start (don't wait for speech end).
        if (
            self._barge_in_pending
            and not self._barge_in_emitted
            and self._last_frame_was_speech
            and (call_time_ms - self._barge_in_speech_start_ms) >= self._backchannel.max_duration_ms
        ):
            output_events.append(
                BargeinDetected(
                    call_id=call_id,
                    tenant_id=tenant_id,
                    detected_at_ms=self._barge_in_detected_at_ms,
                    playback_seq=0,
                )
            )
            increment_bargein(str(call_id))
            self._barge_in_emitted = True

        return output_events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_frame(self, frame: AudioFrame) -> None:
        if frame.config.sample_rate != SampleRate.RATE_16K:
            raise ValueError(
                f"VADEndpointingService requires 16 kHz frames, "
                f"got {frame.config.sample_rate} Hz.  "
                "Ensure AudioPreprocessorService (ResamplerStage) runs before VAD."
            )
        if frame.config.encoding != Encoding.PCM16LE:
            raise ValueError(f"VADEndpointingService requires PCM16LE encoding, got {frame.config.encoding}.")

    def _reset_barge_in_state(self) -> None:
        self._barge_in_pending = False
        self._barge_in_detected_at_ms = 0
        self._barge_in_speech_start_ms = 0
        self._barge_in_emitted = False
