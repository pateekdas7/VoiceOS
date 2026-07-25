"""CallRecorder — optional per-call transcript/timing/audio capture.

Built for Sprint-029's Call-002 production acceptance review (the user's
explicit instrumentation requirements: full transcript, per-stage timing,
barge-in events, and call audio, submitted afterward to both an engineering
report and an independent LLM evaluator). Opt-in and purely additive:
CallOrchestrator takes `recorder: CallRecorder | None = None` and every
call site is a no-op when it's None, so no existing call path or test
changes behavior by this file's existence.

Architecture: V3 Ch16 (Logging) -- this captures call-scoped artifacts for
offline review, distinct from StructuredLogger's ongoing operational log
stream, which every call already emits regardless of whether a recorder is
attached.
"""

from __future__ import annotations

import json
import time
import wave
from pathlib import Path


class CallRecorder:
    """Accumulates transcript events + raw audio for one call, flushed to
    disk on close().

    Inbound (customer) and outbound (Kavya) audio are kept as separate
    mono PCM16LE WAV files rather than mixed into one stream -- each side
    already arrives at its own natural sample rate (customer audio at
    whatever AudioPreprocessorService resamples to for VAD/STT; Kavya's
    TTS clauses at their own `sample_rate`, typically 24kHz), and keeping
    them separate is simpler and lossless for the evaluator, which can
    align them using the JSONL event timestamps.
    """

    def __init__(self, call_id: str, output_dir: str) -> None:
        self._call_id = call_id
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._events: list[dict[str, object]] = []
        self._inbound_pcm = bytearray()
        self._inbound_sample_rate = 16000
        self._outbound_pcm = bytearray()
        self._outbound_sample_rate = 24000

    def event(self, event_type: str, **fields: object) -> None:
        """Append one timestamped structured event (turn boundaries, STT
        transcripts, per-stage latency, barge-in, etc.) to this call's
        transcript log."""
        self._events.append(
            {
                "event": event_type,
                "wall_time": time.time(),
                "call_id": self._call_id,
                **fields,
            }
        )

    def add_inbound_audio(self, pcm16le: bytes, sample_rate: int) -> None:
        self._inbound_sample_rate = sample_rate
        self._inbound_pcm.extend(pcm16le)

    def add_outbound_audio(self, pcm16le: bytes, sample_rate: int) -> None:
        self._outbound_sample_rate = sample_rate
        self._outbound_pcm.extend(pcm16le)

    def close(self) -> None:
        """Flush the accumulated transcript/audio to disk. Safe to call
        even if no audio/events were ever recorded (e.g. a call that was
        rejected before any turn ran) -- writes an empty-but-valid JSONL
        file and skips WAV files with no frames."""
        events_path = self._dir / f"{self._call_id}_events.jsonl"
        with events_path.open("w", encoding="utf-8") as f:
            for ev in self._events:
                f.write(json.dumps(ev, default=str) + "\n")

        if self._inbound_pcm:
            self._write_wav(self._dir / f"{self._call_id}_customer.wav", bytes(self._inbound_pcm), self._inbound_sample_rate)
        if self._outbound_pcm:
            self._write_wav(self._dir / f"{self._call_id}_kavya.wav", bytes(self._outbound_pcm), self._outbound_sample_rate)

    @staticmethod
    def _write_wav(path: Path, pcm16le: bytes, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm16le)
